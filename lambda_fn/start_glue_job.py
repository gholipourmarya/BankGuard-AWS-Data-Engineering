"""Start one queued Glue run per completed raw/batches/<id>/ready.json package.

The uploader writes three immutable CSVs first and ready.json last. Glue job
history suppresses marker redeliveries after submission. A permanent conditional
S3 claim prevents concurrent submissions, including after history expires.
Failed/expired runs require an explicit operator retry; they are never reported
as successful. An interrupted/ambiguous submission keeps its claim and requires
operator review; automatically expiring a claim could submit the package twice.
"""
import json
import os
import re
from urllib.parse import unquote_plus

from botocore.config import Config
from botocore.exceptions import ClientError

FILES = ("customers.csv", "accounts.csv", "transactions.csv")
READY_KEY = re.compile(r"raw/batches/[A-Za-z0-9_-]{1,128}/ready\.json\Z")


def _seen_run(glue, job_name, prefix):
    for page in glue.get_paginator("get_job_runs").paginate(JobName=job_name):
        for run in page.get("JobRuns", []):
            if run.get("Arguments", {}).get("--input_prefix") == prefix:
                return run["Id"]
    return None


def handler(event, context=None, s3=None, glue=None):
    bucket = os.environ["BUCKET"]
    job_name = os.environ["GLUE_JOB_NAME"]
    keys = set()
    for record in event.get("Records", []):
        source = record.get("s3", {})
        if source.get("bucket", {}).get("name", bucket) != bucket:
            continue
        key = unquote_plus(source.get("object", {}).get("key", ""))
        if READY_KEY.fullmatch(key):
            keys.add(key)
    if not keys:
        return _result(False, "ignored: no completed data package")

    if s3 is None or glue is None:
        import boto3
        s3 = s3 or boto3.client("s3")
        # StartJobRun has no idempotency token. Never retry an ambiguous HTTP
        # request inside the SDK; Lambda redelivery reconciles via history/claim.
        glue = glue or boto3.client("glue", config=Config(
            retries={"total_max_attempts": 1, "mode": "standard"},
            connect_timeout=5, read_timeout=10,
        ))

    results = []
    for key in sorted(keys):
        prefix = key.rsplit("/", 1)[0]
        previous = _seen_run(glue, job_name, prefix)
        if previous:
            results.append(_result(False, "package already submitted", run_id=previous, input_prefix=prefix))
            continue
        # A missing object is an error: Lambda must retry and then notify, not silently discard it.
        for name in FILES:
            s3.head_object(Bucket=bucket, Key=f"{prefix}/{name}")
        claim_key = f"submissions/{prefix.rsplit('/', 1)[1]}.json"
        try:
            s3.put_object(Bucket=bucket, Key=claim_key, IfNoneMatch="*",
                          ContentType="application/json",
                          Body=json.dumps({"input_prefix": prefix, "job": job_name}).encode("utf-8"))
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "PreconditionFailed":
                raise
            # The winning invocation may have submitted while we checked history.
            previous = _seen_run(glue, job_name, prefix)
            if previous:
                results.append(_result(False, "package already submitted", run_id=previous, input_prefix=prefix))
                continue
            raise RuntimeError(f"Submission claim exists for {prefix}; retry, then review Glue history "
                               "before an explicit operator restart") from exc
        run = glue.start_job_run(JobName=job_name, Arguments={"--input_prefix": prefix},
                                 JobRunQueuingEnabled=True)
        results.append(_result(True, "package submitted", run_id=run["JobRunId"], input_prefix=prefix))
    return results[0] if len(results) == 1 else {"started": any(r["started"] for r in results), "batches": results}


def _result(started, reason, **extra):
    result = {"started": started, "reason": reason, **extra}
    print(json.dumps(result, sort_keys=True))
    return result
