import copy
import io
import json
from unittest.mock import Mock

import pytest

from src import snapshot, validate
from scripts.current_snapshot import read_published_snapshot

METRICS = {"input_count": 1, "accepted_count": 1, "rejected_count": 0, "counts_consistent": True,
           "by_source_system": {"CARDS": {"input_count": 1, "accepted_count": 1,
                                         "rejected_count": 0, "counts_consistent": True}}}


class Catalog:
    def __init__(self, fail_register=False):
        self.databases = {"template"}
        self.tables = {("template", name): {"Name": name, "StorageDescriptor": {
            "Location": "s3://bucket/templates/", "Columns": [{"Name": "amount", "Type": "decimal(18,2)"}]},
            "PartitionKeys": [{"Name": "transaction_date", "Type": "string"}] if name == "curated_transactions" else []}
            for name in snapshot.TABLE_PATHS}
        self.fail_register = fail_register

    def create_database(self, DatabaseInput):
        if DatabaseInput["Name"] in self.databases:
            raise RuntimeError("database already exists")
        self.databases.add(DatabaseInput["Name"])

    def get_table(self, DatabaseName, Name):
        return {"Table": copy.deepcopy(self.tables[(DatabaseName, Name)])}

    def create_table(self, DatabaseName, TableInput):
        self.tables[(DatabaseName, TableInput["Name"])] = copy.deepcopy(TableInput)

    def batch_create_partition(self, **kwargs):
        if self.fail_register:
            raise RuntimeError("registration failed")
        return {"Errors": []}


class Storage:
    def __init__(self, fail_key=None):
        self.objects = {"published/current.json": b'{"database":"template_jr_old","run_id":"jr_old","counts_consistent":true}',
                        "snapshots/jr_old/curated/customers/data": b"old customer data"}
        self.fail_key = fail_key
        self.writes = []

    def put_object(self, Bucket, Key, Body, **kwargs):
        if Key == self.fail_key:
            raise RuntimeError("write failed")
        self.objects[Key] = Body
        self.writes.append(Key)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


@pytest.mark.parametrize("stage", ["customers", "accounts", "transactions", "partitions", "commit", "receipt", "pointer"])
def test_failure_at_any_stage_preserves_previous_published_snapshot(stage):
    storage = Storage(fail_key={"receipt": "runs/jr_new/metrics.json", "pointer": "published/current.json"}.get(stage))
    catalog = Catalog(fail_register=stage == "partitions")
    before = dict(storage.objects)
    result = {"metrics": METRICS, "transactions": ([{"transaction_date": "2025-12-31"}], [])}

    def write_outputs():
        for name in ("customers", "accounts", "transactions"):
            storage.put_object(Bucket="bucket", Key=f"snapshots/jr_new/curated/{name}/data", Body=b"new")
            if stage == name:
                raise RuntimeError("data write failed")

    def commit():
        if stage == "commit":
            raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError):
        snapshot.complete_run(catalog, storage, "bucket", "template", "jr_new", "raw/batches/new", result,
                              write_outputs, commit)
    assert storage.objects["published/current.json"] == before["published/current.json"]
    assert storage.objects["snapshots/jr_old/curated/customers/data"] == before["snapshots/jr_old/curated/customers/data"]


def test_success_publishes_only_after_commit_and_complete_catalog():
    storage, catalog = Storage(), Catalog()
    events = []
    result = {"metrics": METRICS, "transactions": ([{"transaction_date": "2030-01-01"}], [])}
    receipt = snapshot.complete_run(catalog, storage, "bucket", "template", "jr_new", "raw/batches/new", result,
                                    lambda: events.append("outputs"), lambda: events.append("commit"))
    assert events == ["outputs", "commit"]
    assert storage.writes == ["runs/jr_new/metrics.json", "published/current.json"]
    assert read_published_snapshot(storage, "bucket") == receipt
    assert receipt["database"] == "template_jr_new"
    assert len([key for key in catalog.tables if key[0] == receipt["database"]]) == 5
    assert all(table["StorageDescriptor"]["Location"].startswith("s3://bucket/snapshots/jr_new/")
               for (db, _), table in catalog.tables.items() if db == receipt["database"])


def test_existing_snapshot_database_is_never_rewritten():
    catalog, storage = Catalog(), Storage()
    catalog.databases.add("template_jr_new")
    write = Mock()
    with pytest.raises(RuntimeError, match="already exists"):
        snapshot.complete_run(catalog, storage, "bucket", "template", "jr_new", "raw/new",
                              {"metrics": METRICS, "transactions": ([], [])}, write, Mock())
    write.assert_not_called()
    assert storage.writes == []


@pytest.mark.parametrize("counts", [(0, 0, 0), (1, 0, 1), (3, 2, 1)])
def test_published_receipt_accepts_consistent_counts_including_zero(counts):
    receipt = {"database": "template_jr_test", "run_id": "jr_test", "counts_consistent": True,
               **dict(zip(("input_count", "accepted_count", "rejected_count"), counts))}
    receipt["by_source_system"] = {"CARDS": {field: receipt[field] for field in
                                           ("input_count", "accepted_count", "rejected_count", "counts_consistent")}}
    storage = Storage()
    storage.objects["published/current.json"] = json.dumps(receipt).encode()
    assert read_published_snapshot(storage, "bucket") == receipt


@pytest.mark.parametrize("changes", [
    {"input_count": 100},
    {"input_count": -1},
    {"accepted_count": -1, "rejected_count": 2},
    {"accepted_count": 2, "rejected_count": -1},
    {"input_count": "1"},
    {"accepted_count": True},
    {"rejected_count": 0.0},
    {"input_count": None},
    {"counts_consistent": "false"},
    {"counts_consistent": False},
])
def test_published_receipt_rejects_invalid_counts_even_with_success_flag(changes):
    receipt = {"database": "template_jr_test", "run_id": "jr_test", **METRICS, **changes}
    storage = Storage()
    storage.objects["published/current.json"] = json.dumps(receipt).encode()
    with pytest.raises(ValueError):
        read_published_snapshot(storage, "bucket")


@pytest.mark.parametrize("field", ["input_count", "accepted_count", "rejected_count"])
def test_published_receipt_requires_all_three_counts(field):
    receipt = {"database": "template_jr_test", "run_id": "jr_test", **METRICS}
    del receipt[field]
    storage = Storage()
    storage.objects["published/current.json"] = json.dumps(receipt).encode()
    with pytest.raises(ValueError, match="non-negative integers"):
        read_published_snapshot(storage, "bucket")


@pytest.mark.parametrize("receipt", [None, [], "invalid receipt"])
def test_published_receipt_rejects_invalid_json_structure(receipt):
    storage = Storage()
    storage.objects["published/current.json"] = json.dumps(receipt).encode()
    with pytest.raises(ValueError, match="Invalid published snapshot receipt"):
        read_published_snapshot(storage, "bucket")


def test_verification_rejects_inconsistent_receipt_before_calling_glue_or_athena():
    from scripts.verify_aws import verify
    receipt = {"database": "template_jr_test", "run_id": "jr_test", "counts_consistent": True,
               "input_count": 100, "accepted_count": 0, "rejected_count": 1}
    storage, glue, athena = Storage(), Mock(), Mock()
    storage.objects["published/current.json"] = json.dumps(receipt).encode()
    with pytest.raises(ValueError, match="input count differs"):
        verify(storage, glue, athena, "bucket", "job", "workgroup")
    assert glue.mock_calls == []
    assert athena.mock_calls == []


@pytest.mark.parametrize("changes", [
    {"accepted_count": -1, "rejected_count": 2},
    {"input_count": True},
    {"counts_consistent": 1},
    {"by_source_system": None},
    {"by_source_system": []},
    {"by_source_system": {}},
    {"by_source_system": {"CARDS": {"input_count": 100, "accepted_count": 0,
                                    "rejected_count": 1, "counts_consistent": True}}},
    {"by_source_system": {"CARDS": {"input_count": 1, "accepted_count": -1,
                                    "rejected_count": 2, "counts_consistent": True}}},
    {"by_source_system": {"CARDS": {"input_count": 2, "accepted_count": 2,
                                    "rejected_count": 0, "counts_consistent": True}}},
    {"by_source_system": {"CARDS": {"input_count": 1, "accepted_count": True,
                                    "rejected_count": 0, "counts_consistent": True}}},
    {"by_source_system": {"CARDS": {"input_count": 1, "accepted_count": 1,
                                    "rejected_count": 0.0, "counts_consistent": True}}},
    {"by_source_system": {"CARDS": {"input_count": 1, "accepted_count": 1,
                                    "rejected_count": 0, "counts_consistent": "true"}}},
    {"by_source_system": {"CARDS": {"input_count": 1, "accepted_count": 1, "counts_consistent": True}}},
    {"by_source_system": {"CARDS": "invalid"}},
    {"by_source_system": {" ": METRICS["by_source_system"]["CARDS"]}},
])
@pytest.mark.parametrize("consumer", ["validation", "publish", "complete_run", "reader"])
def test_all_snapshot_boundaries_reject_invalid_total_or_source_counts(changes, consumer):
    metrics = {**copy.deepcopy(METRICS), **copy.deepcopy(changes)}
    storage, catalog = Mock(), Mock()
    if consumer == "reader":
        receipt = {"database": "template_jr_test", "run_id": "jr_test", **metrics}
        storage.get_object.return_value = {"Body": io.BytesIO(json.dumps(receipt).encode())}
    with pytest.raises(ValueError):
        if consumer == "validation":
            validate.validate_metrics(metrics)
        elif consumer == "publish":
            snapshot.publish(storage, "bucket", "jr_test", "template_jr_test", "raw", metrics)
        elif consumer == "complete_run":
            snapshot.complete_run(catalog, storage, "bucket", "template", "jr_test", "raw",
                                  {"metrics": metrics}, Mock(), Mock())
        else:
            read_published_snapshot(storage, "bucket")
    assert catalog.mock_calls == []
    storage.put_object.assert_not_called()


def test_empty_pipeline_metrics_have_a_valid_empty_source_breakdown():
    from src.transform import run_pipeline
    metrics = run_pipeline([], [], [], "jr_empty", "now")["metrics"]
    assert metrics["by_source_system"] == {}
    validate.validate_metrics(metrics)
