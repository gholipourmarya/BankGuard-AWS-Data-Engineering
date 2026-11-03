import copy
import csv
import json
import os
import tempfile

import pytest

from src import generate_data, transform, validate


def _run(data_dir, run_id="r1"):
    c = transform.read_csv_rows(os.path.join(data_dir, "customers.csv"))
    a = transform.read_csv_rows(os.path.join(data_dir, "accounts.csv"))
    t = transform.read_csv_rows(os.path.join(data_dir, "transactions.csv"))
    return transform.run_pipeline(c, a, t, run_id, run_id), len(t)


def test_counts_reconcile_on_generated_data():
    with tempfile.TemporaryDirectory() as d:
        generate_data.generate(d, n_transactions=2000, seed=1)
        result, n = _run(d)
        m = result["metrics"]
        assert m["input_count"] == n
        assert m["input_count"] == m["accepted_count"] + m["rejected_count"]
        assert m["counts_consistent"]


def test_all_injected_errors_are_detected_without_false_positives():
    with tempfile.TemporaryDirectory() as d:
        generate_data.generate(d, n_transactions=5000, seed=7)
        with open(os.path.join(d, "injected_errors.json"), encoding="utf-8") as f:
            truth = json.load(f)
        result, _ = _run(d)
        _, rejected = result["transactions"]
        found = {(r["_line"], reason) for r in rejected for reason in r["error_reasons"]}
        expected = {(e["line"], e["expected_reason"]) for e in truth}
        assert expected <= found, f"missed: {sorted(expected - found)[:5]}"
        assert found == expected, f"unexpected rejections: {sorted(found - expected)[:5]}"


def test_generated_error_distribution_is_present():
    with tempfile.TemporaryDirectory() as d:
        generate_data.generate(d, n_transactions=5000, seed=3)
        with open(os.path.join(d, "injected_errors.json"), encoding="utf-8") as f:
            reasons = {e["expected_reason"] for e in json.load(f)}
        assert set(generate_data.ERROR_RATES) <= reasons


def test_generator_is_deterministic():
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        generate_data.generate(d1, n_transactions=500, seed=5)
        generate_data.generate(d2, n_transactions=500, seed=5)
        for name in ("customers.csv", "accounts.csv", "transactions.csv", "injected_errors.json"):
            with open(os.path.join(d1, name), "rb") as a, open(os.path.join(d2, name), "rb") as b:
                assert a.read() == b.read()


def test_rerun_produces_identical_outputs_except_audit_columns():
    with tempfile.TemporaryDirectory() as d:
        generate_data.generate(d, n_transactions=1000, seed=9)
        r1, _ = _run(d, "run-1")
        r2, _ = _run(d, "run-2")

        def strip(rows):
            return [{k: v for k, v in r.items() if k not in ("run_id", "ingestion_timestamp")} for r in rows]

        for name in ("customers", "accounts", "transactions"):
            assert strip(r1[name][0]) == strip(r2[name][0])
            assert strip(r1[name][1]) == strip(r2[name][1])
        assert r1["metrics"] == r2["metrics"]


def test_audit_columns_added():
    with tempfile.TemporaryDirectory() as d:
        generate_data.generate(d, n_transactions=300, seed=2)
        result, _ = _run(d, "run-x")
        row = result["transactions"][0][0]
        assert row["run_id"] == "run-x" and row["source_file"] == "transactions.csv"
        assert row["ingestion_timestamp"] == "run-x"


def test_local_outputs_written():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as out:
        generate_data.generate(d, n_transactions=300, seed=4)
        result, _ = _run(d)
        transform.write_local_outputs(result, out)
        for name in ("curated_transactions.csv", "rejected_transactions.jsonl", "metrics.json"):
            assert os.path.exists(os.path.join(out, name))


def test_empty_rerun_replaces_previous_curated_rows(tmp_path):
    result, _ = _run("examples")
    transform.write_local_outputs(result, tmp_path)
    empty = transform.run_pipeline([], [], [], "empty", "empty")
    transform.write_local_outputs(empty, tmp_path)
    for name in ("customers", "accounts", "transactions"):
        with (tmp_path / f"curated_{name}.csv").open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert list(reader) == []
            assert "run_id" in reader.fieldnames
        assert (tmp_path / f"rejected_{name}.jsonl").read_text() == ""
    assert json.loads((tmp_path / "metrics.json").read_text())["input_count"] == 0


def test_accepted_ids_join_after_trimming_without_changing_input():
    customers = [{"customer_id": " C1 ", "name": " Alice ", "country": " DE ", "_line": 2}]
    accounts = [{"account_id": " A1 ", "customer_id": " C1", "status": " Active ", "_line": 2}]
    transactions = [{"transaction_id": " T1 ", "account_id": "A1 ", "amount": "10.00",
                     "currency": " EUR ", "transaction_type": " PAYMENT ",
                     "transaction_date": "2026-01-02", "source_system": " CARDS ", "_line": 2}]
    duplicate = {**transactions[0], "transaction_id": "T1", "_line": 3}
    transactions.append(duplicate)
    original = copy.deepcopy((customers, accounts, transactions))
    result = transform.run_pipeline(customers, accounts, transactions, "run", "run")
    customer, account, transaction = [result[name][0][0] for name in ("customers", "accounts", "transactions")]
    assert customer["customer_id"] == account["customer_id"] == "C1"
    assert account["account_id"] == transaction["account_id"] == "A1"
    assert transaction["transaction_id"] == "T1"
    assert transaction["source_system"] == "CARDS"
    rejected = result["transactions"][1]
    assert len(rejected) == 1
    assert rejected[0]["error_reasons"] == ["DUPLICATE_TRANSACTION_ID"]
    assert rejected[0]["account_id"] == duplicate["account_id"]
    assert (customers, accounts, transactions) == original


def test_extra_csv_cells_are_rejected_and_preserved(tmp_path):
    path = tmp_path / "transactions.csv"
    path.write_text("transaction_id,account_id,amount,currency,transaction_type,transaction_date,source_system\n"
                    "T1,A1,abc,EUR,PAYMENT,2026-01-02,CARDS,extra\n")
    result = transform.run_pipeline([], [], transform.read_csv_rows(path), "r", "r")
    transform.write_local_outputs(result, tmp_path / "out")
    row = json.loads((tmp_path / "out/rejected_transactions.jsonl").read_text())
    assert row["extra_values"] == ["extra"]
    assert "INVALID_COLUMN_COUNT" in row["error_reasons"]
    assert row["line"] == 2
    assert result["metrics"]["input_count"] == result["metrics"]["rejected_count"] == 1


def test_csv_multiline_and_blank_lines_keep_physical_line_numbers(tmp_path):
    path = tmp_path / "customers.csv"
    path.write_text('customer_id,name,country\nC1,"Alice\nSmith",DE\n\nC2,Bob,FR\n')
    rows = transform.read_csv_rows(path)
    assert rows[0]["name"] == "Alice\nSmith"
    assert [row["_line"] for row in rows] == [2, 5]


def test_negative_generator_count_fails_before_writing(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="zero or greater"):
        generate_data.generate(tmp_path / "data", n_transactions=-1)
    assert not (tmp_path / "data").exists()


def test_duplicate_csv_headers_fail_explicitly(tmp_path):
    import pytest
    path = tmp_path / "customers.csv"
    path.write_text("customer_id,name,name\nC1,Alice,DE\n")
    with pytest.raises(ValueError, match="duplicate"):
        transform.read_csv_rows(path)


def test_injected_unknown_accounts_cannot_collide_with_large_reference_sets():
    import random
    from src import validate
    rng = random.Random(42)
    _, accounts = generate_data.build_reference_data(rng, 100000)
    rows = generate_data.build_transactions(rng, accounts, 200)
    truth = generate_data.inject_errors(rng, rows)
    for line, row in enumerate(rows, start=2):
        row["_line"] = line
    _, rejected = validate.validate_transactions(rows, {a["account_id"] for a in accounts})
    expected = {(item["line"], item["expected_reason"]) for item in truth}
    actual = {(r["_line"], reason) for r in rejected for reason in r["error_reasons"]}
    assert actual == expected


@pytest.mark.parametrize("transaction_type", ["REFUND", "REVERSAL"])
@pytest.mark.parametrize("currency", ["EUR", "USD"])
def test_conflicting_refund_duplicates_do_not_create_unreported_amount_errors(transaction_type, currency):
    import random
    from src import validate

    rows = [{"transaction_id": f"T{i}", "account_id": "A1", "amount": "-1.00",
             "currency": currency, "transaction_type": transaction_type,
             "transaction_date": "2026-10-01", "source_system": "CARDS"} for i in range(200)]
    truth = generate_data.inject_errors(random.Random(42), rows)
    for line, row in enumerate(rows, start=2):
        row["_line"] = line
    _, rejected = validate.validate_transactions(rows, {"A1"})
    expected = {(item["line"], item["expected_reason"]) for item in truth}
    actual = {(row["_line"], reason) for row in rejected for reason in row["error_reasons"]}
    assert actual == expected
    conflicting = [row for row in rejected if "DUPLICATE_CONFLICTING_CONTENT" in row["error_reasons"]]
    assert conflicting
    assert all(validate.parse_amount(row["amount"]) == validate.parse_amount("-1.00") for row in conflicting)


@pytest.mark.parametrize("source", [None, "", " \t "])
def test_missing_source_labels_are_consistent_for_rejected_rows(source):
    row = {"transaction_id": "T1", "account_id": "A1", "amount": "1.00", "currency": "EUR",
           "transaction_type": "PAYMENT", "transaction_date": "2026-01-01", "source_system": source, "_line": 2}
    result = transform.run_pipeline([], [], [row], "jr_test", "now")
    assert result["metrics"]["by_source_system"] == {"(missing)": {
        "input_count": 1, "accepted_count": 0, "rejected_count": 1,
        "counts_consistent": True, "pass_rate_percent": 0.0}}
    validate.validate_metrics(result["metrics"])
