"""Validate AWS request shapes with the installed SDK, without network calls."""
import copy
import json
from unittest.mock import Mock

import pytest

boto3 = pytest.importorskip("boto3")
from botocore.stub import Stubber

from lambda_fn import start_glue_job
from src import snapshot


def client(service):
    return boto3.client(service, region_name="eu-central-1", aws_access_key_id="testing",
                        aws_secret_access_key="testing")


def test_lambda_requests_match_actual_sdk_models(monkeypatch):
    monkeypatch.setenv("BUCKET", "bucket")
    monkeypatch.setenv("GLUE_JOB_NAME", "job")
    prefix = "raw/batches/test1"
    s3, glue = client("s3"), client("glue")
    with Stubber(s3) as storage, Stubber(glue) as jobs:
        for name in start_glue_job.FILES:
            storage.add_response("head_object", {}, {"Bucket": "bucket", "Key": f"{prefix}/{name}"})
        storage.add_response("put_object", {}, {
            "Bucket": "bucket", "Key": "submissions/test1.json", "IfNoneMatch": "*",
            "ContentType": "application/json",
            "Body": json.dumps({"input_prefix": prefix, "job": "job"}).encode("utf-8"),
        })
        jobs.add_response("get_job_runs", {"JobRuns": []}, {"JobName": "job"})
        jobs.add_response("start_job_run", {"JobRunId": "jr_test"}, {
            "JobName": "job", "Arguments": {"--input_prefix": prefix}, "JobRunQueuingEnabled": True})
        result = start_glue_job.handler({"Records": [{"s3": {"bucket": {"name": "bucket"},
                                                     "object": {"key": f"{prefix}/ready.json"}}}]},
                                        s3=s3, glue=glue)
        assert result["run_id"] == "jr_test"
        storage.assert_no_pending_responses()
        jobs.assert_no_pending_responses()


def test_catalog_partition_requests_match_sdk_models():
    glue = client("glue")
    descriptor = {"Columns": [{"Name": "amount", "Type": "decimal(18,2)"}], "Location": "s3://bucket/curated/transactions/",
                  "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                  "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                  "SerdeInfo": {"SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"}}
    partition_storage = copy.deepcopy(descriptor)
    partition_storage["Location"] = "s3://bucket/snapshots/jr_test/curated/transactions/transaction_date=2025-12-31/"
    with Stubber(glue) as jobs:
        jobs.add_response("get_table", {"Table": {"Name": "curated_transactions", "StorageDescriptor": descriptor}},
                          {"DatabaseName": "custom", "Name": "curated_transactions"})
        jobs.add_response("batch_create_partition", {"Errors": []}, {
            "DatabaseName": "custom", "TableName": "curated_transactions",
            "PartitionInputList": [{"Values": ["2025-12-31"], "StorageDescriptor": partition_storage}]})
        snapshot.register_partitions(glue, "custom", "bucket", "jr_test", [{"transaction_date": "2025-12-31"}])
        jobs.assert_no_pending_responses()


def test_snapshot_catalog_creation_matches_sdk_models():
    glue = client("glue")
    descriptor = {"Columns": [{"Name": "amount", "Type": "decimal(18,2)"}], "Location": "s3://bucket/template/"}
    with Stubber(glue) as jobs:
        jobs.add_response("create_database", {}, {"DatabaseInput": {
            "Name": "custom_jr_test", "Description": "BankGuard immutable snapshot jr_test"}})
        for name, path in snapshot.TABLE_PATHS.items():
            jobs.add_response("get_table", {"Table": {"Name": name, "StorageDescriptor": descriptor}},
                              {"DatabaseName": "custom", "Name": name})
            storage = copy.deepcopy(descriptor)
            storage["Location"] = f"s3://bucket/snapshots/jr_test/{path}"
            jobs.add_response("create_table", {}, {"DatabaseName": "custom_jr_test", "TableInput": {
                "Name": name, "TableType": "EXTERNAL_TABLE", "StorageDescriptor": storage,
                "Parameters": {}, "PartitionKeys": []}})
        assert snapshot.create_catalog(glue, "custom", "bucket", "jr_test") == "custom_jr_test"
        jobs.assert_no_pending_responses()


def test_release_update_matches_real_sdk_and_preserves_job_settings():
    from pathlib import Path
    from scripts import deploy_release
    s3, glue = Mock(), client("glue")
    root = Path(__file__).resolve().parents[1]
    prefix = f"releases/{deploy_release.release_id(root)}"
    original = {"Name": "job", "Role": "role", "Command": {"Name": "glueetl", "ScriptLocation": "s3://old/script"},
                "DefaultArguments": {"--bucket": "bucket"}, "GlueVersion": "4.0", "WorkerType": "G.1X",
                "NumberOfWorkers": 2, "MaxCapacity": 2, "AllocatedCapacity": 2, "Timeout": 30,
                "JobRunQueuingEnabled": True}
    update = {key: copy.deepcopy(value) for key, value in original.items()
              if key not in {"Name", "MaxCapacity", "AllocatedCapacity"}}
    update["Command"]["ScriptLocation"] = f"s3://bucket/{prefix}/banking_etl.py"
    update["DefaultArguments"]["--extra-py-files"] = f"s3://bucket/{prefix}/src.zip"
    with Stubber(glue) as jobs:
        jobs.add_response("get_job", {"Job": original}, {"JobName": "job"})
        jobs.add_response("update_job", {"JobName": "job"}, {"JobName": "job", "JobUpdate": update})
        assert deploy_release.deploy_release(s3, glue, "bucket", "job", root) == prefix
        jobs.assert_no_pending_responses()


def test_expired_run_monitor_matches_sdk_requests():
    import json
    from lambda_fn.check_glue_runs import check_runs
    glue, s3, sns = client("glue"), client("s3"), client("sns")
    topic = "arn:aws:sns:eu-central-1:123456789012:alerts"
    key = "monitor/notified/jr_expired-EXPIRED.json"
    message = json.dumps({"job": "job", "run_id": "jr_expired", "state": "EXPIRED", "error": ""}, sort_keys=True)
    with Stubber(glue) as jobs, Stubber(s3) as storage, Stubber(sns) as alerts:
        jobs.add_response("get_job_runs", {"JobRuns": [{"Id": "jr_expired", "JobRunState": "EXPIRED"}]},
                          {"JobName": "job", "MaxResults": 200})
        storage.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404,
                                 expected_params={"Bucket": "bucket", "Key": key})
        alerts.add_response("publish", {"MessageId": "message-id"}, {
            "TopicArn": topic, "Subject": "BankGuard Glue run failed", "Message": message})
        storage.add_response("put_object", {}, {"Bucket": "bucket", "Key": key,
                             "Body": message.encode("utf-8"), "ContentType": "application/json"})
        assert check_runs(glue, s3, sns, "job", "bucket", topic)["notified"] == ["jr_expired"]
        jobs.assert_no_pending_responses()
        storage.assert_no_pending_responses()
        alerts.assert_no_pending_responses()
