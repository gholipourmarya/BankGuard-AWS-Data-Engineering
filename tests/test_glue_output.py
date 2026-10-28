"""Exercise Glue output functions without starting an AWS job or Spark session."""
import ast
import copy
from decimal import Decimal
import io
import json
from pathlib import Path
from unittest.mock import Mock

from botocore.exceptions import IncompleteReadError
from botocore.response import StreamingBody
from src import snapshot, transform

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_function(relative_path, name, namespace):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("failure", [None, "invalid_header", "truncated_body"])
def test_s3_csv_reader_materializes_rows_and_closes_body(failure):
    content = (b"wrong,header\n" if failure == "invalid_header" else
               '\ufeffcustomer_id,name,country\r\nC1,"Ada\r\nLovelace",DE\r\n'.encode("utf-8"))
    raw = io.BytesIO(content)
    body = StreamingBody(raw, len(content) + (1 if failure == "truncated_body" else 0))
    s3 = Mock()
    s3.get_object.return_value = {"Body": body}
    read = load_function("glue/banking_etl.py", "read_raw_csv", {
        "s3": s3, "BUCKET": "test-bucket", "INPUT_PREFIX": "raw/batches/test",
        "io": io, "transform": transform, "log": Mock(),
    })
    if failure == "invalid_header":
        with pytest.raises(ValueError, match="Expected CSV columns"):
            read("customers.csv")
    elif failure == "truncated_body":
        with pytest.raises(IncompleteReadError):
            read("customers.csv")
    else:
        rows = read("customers.csv")
        assert rows == [{"customer_id": "C1", "name": "Ada\r\nLovelace", "country": "DE", "_line": 2}]
    assert raw.closed
    s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="raw/batches/test/customers.csv")


def test_empty_rejected_output_overwrites_previous_run():
    spark = Mock()
    write = load_function("glue/banking_etl.py", "write_rejected",
                          {"spark": spark, "BUCKET": "test-bucket", "OUTPUT_PREFIX": "snapshots/jr_test", "log": Mock()})
    write([], "transactions")
    spark.createDataFrame.assert_called_once_with([], "error_reasons array<string>")
    writer = spark.createDataFrame.return_value.write
    writer.mode.assert_called_once_with("overwrite")
    writer.mode.return_value.json.assert_called_once_with("s3://test-bucket/snapshots/jr_test/rejected/transactions/")


def test_partitioned_snapshots_use_static_overwrite_even_when_empty():
    spark = Mock()
    write = load_function("glue/banking_etl.py", "write_parquet", {
        "spark": spark, "BUCKET": "bucket", "OUTPUT_PREFIX": "snapshots/jr_test", "AUDIT": [], "Decimal": Decimal,
        "StructType": list, "StructField": lambda *args: args,
        "StringType": lambda: "string", "DecimalType": lambda *args: args,
    })
    write([], ["amount", "transaction_date"], "transactions", partition_by="transaction_date")
    df = spark.createDataFrame.return_value
    option = df.write.mode.return_value.option
    option.assert_called_once_with("partitionOverwriteMode", "static")
    option.return_value.partitionBy.return_value.parquet.assert_called_once_with(
        "s3://bucket/snapshots/jr_test/curated/transactions/")
    df.select.assert_called_once_with("amount", "transaction_date")
    df.select.return_value.write.mode.return_value.option.return_value.csv.assert_called_once_with(
        "s3://bucket/snapshots/jr_test/comparison/csv/")


def test_catalog_registers_actual_dates_without_2026_bounds():
    catalog = Mock()
    descriptor = {"Columns": [{"Name": "amount", "Type": "decimal(18,2)"}], "Location": "root"}
    catalog.get_table.return_value = {"Table": {"StorageDescriptor": descriptor}}
    catalog.batch_create_partition.return_value = {"Errors": []}
    snapshot.register_partitions(catalog, "custom_jr_test", "bucket", "jr_test", [
        {"transaction_date": "2025-12-31"}, {"transaction_date": "2030-01-01"},
        {"transaction_date": "2030-01-01"}])
    call = catalog.batch_create_partition.call_args.kwargs
    assert call["DatabaseName"] == "custom_jr_test"
    assert [p["Values"] for p in call["PartitionInputList"]] == [["2025-12-31"], ["2030-01-01"]]
    assert call["PartitionInputList"][0]["StorageDescriptor"]["Location"].endswith("transaction_date=2025-12-31/")
    assert descriptor["Location"] == "root"
    catalog.batch_create_partition.return_value = {"Errors": [{"ErrorDetail": {"ErrorCode": "AccessDeniedException"}}]}
    with pytest.raises(RuntimeError, match="register"):
        snapshot.register_partitions(catalog, "custom_jr_test", "bucket", "jr_test", [{"transaction_date": "2030-01-01"}])


@pytest.mark.parametrize("accepted,rejected", [(0, 0), (0, 3), (2, 1)])
def test_airflow_accepts_receipt_for_current_run_including_zero_rows(accepted, rejected):
    hook = Mock()
    hook.read_key.return_value = json.dumps({"run_id": "jr_current", "database": "custom_jr_current", "counts_consistent": True,
                                            "input_count": accepted + rejected,
                                            "accepted_count": accepted, "rejected_count": rejected})
    verify = load_function("airflow/dags/bankguard_pipeline.py", "verify_outputs", {
        "S3Hook": Mock(return_value=hook), "BUCKET": "bucket", "json": json,
        "AirflowFailException": RuntimeError, "DATABASE": "custom",
    })
    verify(Mock(xcom_pull=Mock(return_value="jr_current")))
    hook.read_key.assert_called_once_with(key="runs/jr_current/metrics.json", bucket_name="bucket")


def test_airflow_rejects_old_run_receipt():
    hook = Mock()
    hook.read_key.return_value = json.dumps({"run_id": "jr_old"})
    verify = load_function("airflow/dags/bankguard_pipeline.py", "verify_outputs", {
        "S3Hook": Mock(return_value=hook), "BUCKET": "bucket", "json": json,
        "AirflowFailException": RuntimeError, "DATABASE": "custom",
    })
    with pytest.raises(RuntimeError, match="inconsistent"):
        verify(Mock(xcom_pull=Mock(return_value="jr_current")))
