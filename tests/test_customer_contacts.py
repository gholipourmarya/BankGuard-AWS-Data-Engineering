import copy
import csv
import io
import json
from unittest.mock import Mock

import pytest

from src import transform, validate, snapshot
from tests.test_glue_output import load_function
from tests.test_snapshot_publication import Catalog


def customer(**changes):
    return {"customer_id": "C1", "name": "Alice", "country": "DE", "_line": 2, **changes}


@pytest.mark.parametrize("email", ["bad", "a@@example.com", "a@-example.com", "a@example..com",
                                    ".a@example.com", "a..b@example.com", "a b@example.com",
                                    "a\n@example.com", "a" * 65 + "@example.com"])
def test_invalid_email_is_rejected_without_changing_source(email):
    row = customer(email=email)
    valid, rejected = validate.validate_customers([row])
    assert not valid
    assert rejected[0]["error_reasons"] == ["INVALID_EMAIL"]
    assert rejected[0]["email"] == row["email"] == email


@pytest.mark.parametrize("email", ["alice@example.com", " alice+bank@example.co.uk ", "", "NULL", None])
def test_valid_and_missing_email_are_accepted(email):
    valid, rejected = validate.validate_customers([customer(email=email)])
    assert len(valid) == 1 and not rejected


@pytest.mark.parametrize("tel", ["abc", "123", "+49+301234567", "1234567890123456", "+49\n301234567",
                                 "((((1234567", "1234567))))", "(1234567", "1234567)", "12()34567",
                                 "((123))4567", "(12-34)567", "---1234567---", "+ 1234567"])
def test_invalid_telephone_is_rejected(tel):
    valid, rejected = validate.validate_customers([customer(tel=tel)])
    assert not valid
    assert rejected[0]["error_reasons"] == ["INVALID_TEL"]


@pytest.mark.parametrize("tel", ["+49 30 23125 00001", "030 / 123-4567", "+1 (202) 555-0123",
                                 "+49 (0)30 1234567", "(030)1234567", "", None])
def test_valid_and_missing_telephone_are_accepted(tel):
    valid, rejected = validate.validate_customers([customer(tel=tel)])
    assert len(valid) == 1 and not rejected


@pytest.mark.parametrize("columns,values", [
    ("customer_id,name,country", "C1,Alice,DE"),
    ("email,country,customer_id,name", "alice@example.com,DE,C1,Alice"),
    ("customer_id,name,country,tel", "C1,Alice,DE,+49 30 1234567"),
    ("tel,name,customer_id,email,country", "+49 30 1234567,Alice,C1,alice@example.com,DE"),
])
def test_local_and_glue_readers_support_old_and_new_headers(tmp_path, columns, values):
    data = f"{columns}\n{values}\n"
    path = tmp_path / "customers.csv"
    path.write_text(data)
    local = transform.read_csv_rows(path)
    s3 = Mock()
    s3.get_object.return_value = {"Body": io.BytesIO(data.encode())}
    read = load_function("glue/banking_etl.py", "read_raw_csv", {
        "s3": s3, "BUCKET": "bucket", "INPUT_PREFIX": "raw", "io": io,
        "transform": transform, "log": Mock(),
    })
    assert read("customers.csv") == local
    assert {"email", "tel"} <= local[0].keys()
    result = transform.run_pipeline(local, [], [], "review", "now")
    transform.write_local_outputs(result, tmp_path / "out")
    with (tmp_path / "out/curated_customers.csv").open(newline="") as stream:
        output = list(csv.DictReader(stream))
    assert output[0]["email"] == local[0]["email"]
    assert output[0]["tel"] == local[0]["tel"]


@pytest.mark.parametrize("header", ["name,country", "customer_id,name,country,unknown"])
def test_legacy_compatibility_does_not_allow_invalid_headers(tmp_path, header):
    path = tmp_path / "customers.csv"
    path.write_text(header + "\n")
    with pytest.raises(ValueError, match="Expected CSV columns"):
        transform.read_csv_rows(path)


def test_old_customer_row_with_extra_cell_is_still_rejected(tmp_path):
    path = tmp_path / "customers.csv"
    path.write_text("customer_id,name,country\nC1,Alice,DE,surplus\n")
    valid, rejected = validate.validate_customers(transform.read_csv_rows(path))
    assert not valid
    assert rejected[0]["error_reasons"] == ["INVALID_COLUMN_COUNT"]
    assert rejected[0]["extra_values"] == ["surplus"]


def reference_error_result():
    customers = [customer(), customer(customer_id="C2", email="invalid", _line=3)]
    accounts = [{"account_id": "A1", "customer_id": "C1", "status": "Active", "_line": 2},
                {"account_id": "A2", "customer_id": "C1", "status": "Frozen", "_line": 3}]
    transactions = [{"transaction_id": "T1", "account_id": "A1", "amount": "10.00",
                     "currency": "EUR", "transaction_type": "PAYMENT", "transaction_date": "2026-01-01",
                     "source_system": "CARDS", "_line": 2}]
    return transform.run_pipeline(customers, accounts, transactions, "review", "now")


def test_reference_errors_reach_metrics_reports_and_cloudwatch(tmp_path):
    result = reference_error_result()
    metrics = result["metrics"]
    validate.validate_metrics(metrics)
    assert metrics["pass_rate_percent"] == 100
    assert metrics["by_dataset"]["customers"]["rejected_count"] == 1
    assert metrics["by_dataset"]["accounts"]["rejected_count"] == 1
    assert metrics["overall"]["input_count"] == 5
    assert metrics["overall"]["rejected_count"] == 2
    assert metrics["overall"]["pass_rate_percent"] == 60
    transform.write_local_outputs(result, tmp_path)
    with (tmp_path / "dataset_rework_report.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {(row["dataset"], row["error_reason"], row["example_ids"]) for row in rows} == {
        ("customers", "INVALID_EMAIL", "C2"), ("accounts", "INVALID_STATUS", "A2")}
    cloudwatch = Mock()
    put = load_function("glue/banking_etl.py", "put_metrics", {
        "boto3": Mock(client=Mock(return_value=cloudwatch)), "log": Mock(), "JOB_NAME": "job",
    })
    put(metrics)
    values = {item["MetricName"]: item["Value"]
              for item in cloudwatch.put_metric_data.call_args.kwargs["MetricData"]
              if {dimension["Name"]: dimension["Value"] for dimension in item["Dimensions"]}["Dataset"] == "overall"}
    assert values == {"InputCount": 5, "AcceptedCount": 3, "RejectedCount": 2, "PassRatePercent": 60}


def test_glue_writes_reference_rework_report():
    result = reference_error_result()
    s3 = Mock()
    write = load_function("glue/banking_etl.py", "write_outputs", {
        "s3": s3, "BUCKET": "bucket", "OUTPUT_PREFIX": "snapshots/jr_new", "json": json,
        "transform": transform, "write_parquet": Mock(), "write_rejected": Mock(),
    })
    write(result)
    request = s3.put_object.call_args.kwargs
    assert request["Key"] == "snapshots/jr_new/dataset_rework_report.json"
    assert json.loads(request["Body"]) == result["dataset_rework_report"]


@pytest.mark.parametrize("change", ["missing_dataset", "wrong_overall", "wrong_transactions", "missing_overall"])
def test_invalid_dataset_balances_cannot_be_published(change):
    metrics = reference_error_result()["metrics"]
    if change == "missing_dataset":
        del metrics["by_dataset"]["customers"]
    elif change == "wrong_overall":
        metrics["overall"].update(input_count=4, accepted_count=2)
    elif change == "wrong_transactions":
        metrics["by_dataset"]["transactions"].update(input_count=2, accepted_count=2)
    else:
        del metrics["overall"]
    s3 = Mock()
    with pytest.raises(ValueError):
        snapshot.publish(s3, "bucket", "jr_new", "db", "raw", metrics)
    s3.put_object.assert_not_called()


@pytest.mark.parametrize("has_contacts", [False, True])
def test_new_snapshot_exposes_contacts_with_old_or_new_template(has_contacts):
    catalog = Catalog()
    descriptor = catalog.tables[("template", "curated_customers")]["StorageDescriptor"]
    descriptor["Columns"] = [{"Name": field, "Type": "string"} for field in
                             (validate.CUSTOMER_FIELDS if has_contacts else validate.CUSTOMER_REQUIRED_FIELDS)]
    before = copy.deepcopy(catalog.tables)
    database = snapshot.create_catalog(catalog, "template", "bucket", "jr_contacts")
    columns = catalog.tables[(database, "curated_customers")]["StorageDescriptor"]["Columns"]
    assert {item["Name"]: item["Type"] for item in columns} == dict.fromkeys(validate.CUSTOMER_FIELDS, "string")
    assert len(columns) == len(validate.CUSTOMER_FIELDS)
    for key, table in before.items():
        assert catalog.tables[key] == table


def test_wrong_contact_catalog_type_stops_publication():
    catalog = Catalog()
    catalog.tables[("template", "curated_customers")]["StorageDescriptor"]["Columns"].append(
        {"Name": "tel", "Type": "bigint"})
    s3 = Mock()
    with pytest.raises(ValueError, match="tel must have type string"):
        snapshot.complete_run(catalog, s3, "bucket", "template", "jr_new", "raw", reference_error_result(),
                              Mock(), Mock())
    s3.put_object.assert_not_called()
