import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from botocore.exceptions import ClientError

from lambda_fn import start_glue_job as fn

PREFIX = "raw/batches/batch1"
KEYS = [f"{PREFIX}/{name}" for name in fn.FILES]


class FakeS3:
    def __init__(self, existing):
        self.existing = set(existing)
        self.claims = set()
        self.lock = Lock()

    def put_object(self, Bucket, Key, IfNoneMatch, **kwargs):
        assert IfNoneMatch == "*"
        with self.lock:
            if Key in self.claims:
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
            self.claims.add(Key)
        return {}

    def head_object(self, Bucket, Key):
        if Key not in self.existing:
            raise FileNotFoundError(Key)
        return {}


class FakeGlue:
    def __init__(self, error=None, runs=None):
        self.error = error
        self.runs = list(runs or [])
        self.started = []

    def get_paginator(self, name):
        assert name == "get_job_runs"
        return self

    def paginate(self, **kwargs):
        yield {"JobRuns": []}
        yield {"JobRuns": self.runs[:]}

    def start_job_run(self, **kwargs):
        if self.error:
            raise self.error
        self.started.append(kwargs)
        run_id = f"jr_{len(self.started)}"
        self.runs.append({"Id": run_id, "Arguments": kwargs["Arguments"], "JobRunState": "WAITING"})
        return {"JobRunId": run_id}


def event(*keys):
    return {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": key}}} for key in keys]}


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("BUCKET", "b")
    monkeypatch.setenv("GLUE_JOB_NAME", "job")


def test_only_ready_marker_starts_complete_package():
    glue = FakeGlue()
    result = fn.handler(event(f"{PREFIX}/ready.json"), s3=FakeS3(KEYS), glue=glue)
    assert result["started"]
    assert glue.started == [{"JobName": "job", "Arguments": {"--input_prefix": PREFIX},
                             "JobRunQueuingEnabled": True}]


@pytest.mark.parametrize("key", ["raw/customers.csv", f"{PREFIX}/transactions.csv", "raw/batches/../ready.json"])
def test_partial_or_unrelated_upload_does_not_start(key):
    glue = FakeGlue()
    result = fn.handler(event(key), s3=FakeS3(KEYS), glue=glue)
    assert not result["started"] and glue.started == []


def test_incomplete_package_is_retried_instead_of_silently_discarded():
    glue = FakeGlue()
    with pytest.raises(FileNotFoundError):
        fn.handler(event(f"{PREFIX}/ready.json"), s3=FakeS3(KEYS[:1]), glue=glue)
    assert glue.started == []


def test_redelivery_after_submission_does_not_start_duplicate():
    glue = FakeGlue()
    for _ in range(2):
        result = fn.handler(event(f"{PREFIX}/ready.json"), s3=FakeS3(KEYS), glue=glue)
    assert not result["started"]
    assert len(glue.started) == 1


def test_new_package_queues_while_another_package_is_running():
    glue = FakeGlue(runs=[{"Id": "old", "Arguments": {"--input_prefix": "raw/batches/older"},
                           "JobRunState": "RUNNING"}])
    result = fn.handler(event(f"{PREFIX}/ready.json"), s3=FakeS3(KEYS), glue=glue)
    assert result["started"]
    assert glue.started[0]["JobRunQueuingEnabled"] is True


def test_start_errors_propagate_for_lambda_retry():
    with pytest.raises(RuntimeError, match="quota"):
        fn.handler(event(f"{PREFIX}/ready.json"), s3=FakeS3(KEYS), glue=FakeGlue(error=RuntimeError("quota")))


def test_encoded_marker_and_duplicate_records_are_handled_once():
    glue = FakeGlue()
    result = fn.handler(event("raw%2Fbatches%2Fbatch1%2Fready.json", f"{PREFIX}/ready.json"),
                        s3=FakeS3(KEYS), glue=glue)
    assert result["started"] and len(glue.started) == 1


def test_multiple_packages_are_not_lost_in_one_event():
    other = "raw/batches/batch2"
    keys = KEYS + [f"{other}/{name}" for name in fn.FILES]
    glue = FakeGlue()
    result = fn.handler(event(f"{PREFIX}/ready.json", f"{other}/ready.json"), s3=FakeS3(keys), glue=glue)
    assert len(result["batches"]) == 2
    assert len(glue.started) == 2


def test_wrong_bucket_is_ignored():
    message = event(f"{PREFIX}/ready.json")
    message["Records"][0]["s3"]["bucket"]["name"] = "other"
    assert not fn.handler(message, s3=FakeS3(KEYS), glue=FakeGlue())["started"]


def test_concurrent_deliveries_cannot_submit_the_same_package_twice():
    storage, glue = FakeS3(KEYS), FakeGlue()
    entered, release = Event(), Event()
    start = glue.start_job_run

    def blocked_start(**kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return start(**kwargs)

    glue.start_job_run = blocked_start
    marker = event(f"{PREFIX}/ready.json")
    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(fn.handler, marker, s3=storage, glue=glue)
        try:
            assert entered.wait(timeout=5)
            with pytest.raises(RuntimeError, match="Submission claim exists"):
                fn.handler(marker, s3=storage, glue=glue)
        finally:
            release.set()
        assert winner.result(timeout=5)["started"]
    assert len(glue.started) == 1
    assert not fn.handler(marker, s3=storage, glue=glue)["started"]


def test_ambiguous_start_failure_retains_claim_and_blocks_automatic_resubmission():
    storage, glue = FakeS3(KEYS), FakeGlue(error=TimeoutError("response lost"))
    marker = event(f"{PREFIX}/ready.json")
    with pytest.raises(TimeoutError):
        fn.handler(marker, s3=storage, glue=glue)
    glue.error = None
    with pytest.raises(RuntimeError, match="Submission claim exists"):
        fn.handler(marker, s3=storage, glue=glue)
    assert glue.started == []


def test_claim_write_failure_never_starts_glue(monkeypatch):
    storage, glue = FakeS3(KEYS), FakeGlue()

    def denied(**kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")

    monkeypatch.setattr(storage, "put_object", denied)
    with pytest.raises(ClientError, match="AccessDenied"):
        fn.handler(event(f"{PREFIX}/ready.json"), s3=storage, glue=glue)
    assert glue.started == []


def test_lost_start_response_is_not_retried_by_sdk_or_next_lambda_invocation(monkeypatch):
    import boto3
    from botocore.exceptions import ReadTimeoutError
    import botocore.endpoint
    from unittest.mock import Mock

    # Even a permissive account/profile retry setting must not replay StartJobRun.
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "7")
    original_client = boto3.client
    sent = Mock(side_effect=ReadTimeoutError(endpoint_url="https://glue.test"))

    def create_client(service, **kwargs):
        assert service == "glue"
        client = original_client(service, region_name="eu-central-1",
                                 aws_access_key_id="testing", aws_secret_access_key="testing", **kwargs)
        monkeypatch.setattr(client._endpoint.http_session, "send", sent)
        return client

    monkeypatch.setattr(boto3, "client", create_client)
    monkeypatch.setattr(botocore.endpoint.time, "sleep", lambda _: None)
    monkeypatch.setattr(fn, "_seen_run", lambda *_: None)
    storage = FakeS3(KEYS)
    marker = event(f"{PREFIX}/ready.json")
    with pytest.raises(ReadTimeoutError):
        fn.handler(marker, s3=storage)
    assert sent.call_count == 1
    # An ambiguous response retains the claim; Lambda retries cannot replay it.
    with pytest.raises(RuntimeError, match="Submission claim exists"):
        fn.handler(marker, s3=storage)
    assert sent.call_count == 1
