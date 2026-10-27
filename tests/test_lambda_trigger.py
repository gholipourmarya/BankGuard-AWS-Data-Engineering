import pytest

from lambda_fn import start_glue_job as fn

PREFIX = "raw/batches/batch1"
KEYS = [f"{PREFIX}/{name}" for name in fn.FILES]


class FakeS3:
    def __init__(self, existing):
        self.existing = set(existing)

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
