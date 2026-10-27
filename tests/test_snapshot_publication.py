import copy
import io
import json
from unittest.mock import Mock

import pytest

from src import snapshot
from scripts.current_snapshot import read_published_snapshot

METRICS = {"input_count": 1, "accepted_count": 1, "rejected_count": 0, "counts_consistent": True}


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
