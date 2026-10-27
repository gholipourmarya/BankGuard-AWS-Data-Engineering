import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

from lambda_fn.check_glue_runs import check_runs
from scripts import deploy_release

ROOT = Path(__file__).resolve().parents[1]


class MissingKey(Exception):
    pass


class Storage:
    exceptions = SimpleNamespace(NoSuchKey=MissingKey)

    def __init__(self):
        self.data = {}

    def get_object(self, Bucket, Key):
        if Key not in self.data:
            raise MissingKey(Key)
        return {"Body": io.BytesIO(self.data[Key])}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.data[Key] = Body


def monitor_glue():
    glue = Mock()
    glue.get_paginator.return_value.paginate.return_value = [
        {"JobRuns": [{"Id": "expired", "JobRunState": "EXPIRED"}, {"Id": "normal", "JobRunState": "SUCCEEDED"}]},
        {"JobRuns": [{"Id": "error", "JobRunState": "ERROR", "ErrorMessage": "capacity"}]},
    ]
    return glue


def test_poller_notifies_expired_and_error_once_across_pages():
    glue, storage, sns = monitor_glue(), Storage(), Mock()
    assert check_runs(glue, storage, sns, "job", "bucket", "topic")["notified"] == ["expired", "error"]
    assert check_runs(glue, storage, sns, "job", "bucket", "topic")["notified"] == []
    assert sns.publish.call_count == 2
    assert {json.loads(call.kwargs["Message"])["state"] for call in sns.publish.call_args_list} == {"ERROR", "EXPIRED"}


def test_failed_notification_is_not_marked_delivered():
    glue, storage, sns = monitor_glue(), Storage(), Mock()
    sns.publish.side_effect = RuntimeError("SNS unavailable")
    with pytest.raises(RuntimeError, match="SNS unavailable"):
        check_runs(glue, storage, sns, "job", "bucket", "topic")
    assert storage.data == {}
    sns.publish.side_effect = None
    assert len(check_runs(glue, storage, sns, "job", "bucket", "topic")["notified"]) == 2


def test_release_zip_is_deterministic_and_includes_snapshot_helper():
    assert deploy_release.library_zip(ROOT).getvalue() == deploy_release.library_zip(ROOT).getvalue()
    with zipfile.ZipFile(deploy_release.library_zip(ROOT)) as archive:
        assert set(archive.namelist()) == {"validate.py", "transform.py", "snapshot.py"}
        assert archive.read("snapshot.py") == (ROOT / "src/snapshot.py").read_bytes()


def fake_deploy_clients():
    s3, glue = Mock(), Mock()
    fields = {name: None for name in ("Role", "Command", "DefaultArguments", "WorkerType", "NumberOfWorkers",
                                    "MaxCapacity", "AllocatedCapacity", "Timeout", "JobRunQueuingEnabled")}
    glue.meta.service_model.operation_model.return_value.input_shape.members = {
        "JobUpdate": SimpleNamespace(members=fields)}
    glue.get_job.return_value = {"Job": {"Name": "job", "CreatedOn": "read-only", "Role": "role",
        "Command": {"Name": "glueetl", "ScriptLocation": "s3://old/script.py"},
        "DefaultArguments": {"--bucket": "bucket", "--database": "custom", "--extra-py-files": "s3://old/lib.zip"},
        "WorkerType": "G.1X", "NumberOfWorkers": 2, "MaxCapacity": 2, "AllocatedCapacity": 2,
        "Timeout": 30, "JobRunQueuingEnabled": True}}
    return s3, glue


def test_release_switches_both_uris_once_and_preserves_worker_configuration():
    s3, glue = fake_deploy_clients()
    events = []
    s3.upload_file.side_effect = lambda *args: events.append("script")
    s3.upload_fileobj.side_effect = lambda *args: events.append("library")
    glue.update_job.side_effect = lambda **kwargs: events.append("switch")
    prefix = deploy_release.deploy_release(s3, glue, "bucket", "job", ROOT)
    assert events == ["script", "library", "switch"]
    update = glue.update_job.call_args.kwargs["JobUpdate"]
    assert update["Command"]["ScriptLocation"] == f"s3://bucket/{prefix}/banking_etl.py"
    assert update["DefaultArguments"]["--extra-py-files"] == f"s3://bucket/{prefix}/src.zip"
    assert update["DefaultArguments"]["--database"] == "custom"
    assert update["NumberOfWorkers"] == 2 and update["WorkerType"] == "G.1X"
    assert "Name" not in update and "CreatedOn" not in update and "MaxCapacity" not in update
    assert "AllocatedCapacity" not in update


def test_failed_release_upload_never_changes_active_job():
    s3, glue = fake_deploy_clients()
    s3.upload_fileobj.side_effect = RuntimeError("upload failed")
    with pytest.raises(RuntimeError):
        deploy_release.deploy_release(s3, glue, "bucket", "job", ROOT)
    glue.update_job.assert_not_called()


def test_failed_job_switch_keeps_old_artifacts_available():
    s3, glue = fake_deploy_clients()
    glue.update_job.side_effect = RuntimeError("job busy")
    with pytest.raises(RuntimeError):
        deploy_release.deploy_release(s3, glue, "bucket", "job", ROOT)
    s3.delete_object.assert_not_called()


def test_snapshot_cleanup_is_read_only_by_default_and_protects_current_database():
    from scripts.cleanup_snapshot_catalogs import cleanup
    storage = Storage()
    storage.data["published/current.json"] = json.dumps({
        "database": "custom_jr_current", "run_id": "jr_current", "counts_consistent": True}).encode()
    glue = Mock()
    glue.get_paginator.return_value.paginate.return_value = [{"DatabaseList": [
        {"Name": "custom_jr_old"}, {"Name": "custom_jr_current"}, {"Name": "another_project_jr_old"}]}]
    assert cleanup(glue, storage, "bucket", "custom") == ["custom_jr_old"]
    glue.delete_database.assert_not_called()
    assert cleanup(glue, storage, "bucket", "custom", delete=True) == ["custom_jr_old"]
    glue.delete_database.assert_called_once_with(Name="custom_jr_old")
    assert "published/current.json" in storage.data


def test_cleanup_of_different_project_never_removes_publication_pointer():
    from scripts.cleanup_snapshot_catalogs import cleanup
    storage = Mock()
    storage.get_object.return_value = {"Body": io.BytesIO(json.dumps({
        "database": "original_jr_current", "run_id": "jr_current", "counts_consistent": True}).encode())}
    glue = Mock()
    glue.get_paginator.return_value.paginate.return_value = [{"DatabaseList": [{"Name": "original_jr_current"}]}]
    assert cleanup(glue, storage, "bucket", "different", delete=True, include_published=True) == []
    storage.delete_object.assert_not_called()
    glue.delete_database.assert_not_called()
