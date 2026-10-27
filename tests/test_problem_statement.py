"""One test per sentence of the problem statement in the README."""
import os
import tempfile

from src import generate_data, transform, validate

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples")

# Source line -> reasons, written by hand from the example files.
EXPECTED_REJECTIONS = {
    4: ["ACCOUNT_NOT_FOUND"],                  # T003: account A999 does not exist
    5: ["MISSING_AMOUNT"],                     # T004: amount is NULL
    6: ["DUPLICATE_TRANSACTION_ID"],           # T001 again
    7: ["INVALID_AMOUNT"],                     # T005: "abc"
    8: ["INVALID_DATE"],                       # T006: "invalid_date"
    10: ["AMOUNT_NOT_ALLOWED_FOR_TYPE"],       # T008: negative PAYMENT
}
EXPECTED_VALID_LINES = [2, 3, 9, 11, 12]       # incl. T007: refund on a closed account is accepted


def _run_examples():
    rows = {n: transform.read_csv_rows(os.path.join(EXAMPLES, f"{n}.csv"))
            for n in ("customers", "accounts", "transactions")}
    return transform.run_pipeline(rows["customers"], rows["accounts"], rows["transactions"], "r", "r"), rows


def test_example_files_give_exactly_the_expected_result():
    result, _ = _run_examples()
    valid, rejected = result["transactions"]
    assert [r["_line"] for r in valid] == EXPECTED_VALID_LINES
    assert {r["_line"]: r["error_reasons"] for r in rejected} == EXPECTED_REJECTIONS


def test_no_record_is_discarded_silently():
    """Every input line ends up exactly once in valid or rejected."""
    with tempfile.TemporaryDirectory() as d:
        generate_data.generate(d, n_transactions=3000, seed=11)
        rows = transform.read_csv_rows(os.path.join(d, "transactions.csv"))
        accounts = transform.read_csv_rows(os.path.join(d, "accounts.csv"))
        customers = transform.read_csv_rows(os.path.join(d, "customers.csv"))
        result = transform.run_pipeline(customers, accounts, rows, "r", "r")
        valid, rejected = result["transactions"]
        seen = [r["_line"] for r in valid] + [r["_line"] for r in rejected]
        assert sorted(seen) == sorted(r["_line"] for r in rows)
        assert len(seen) == len(set(seen))


def test_every_rejection_says_why():
    result, _ = _run_examples()
    for dataset in ("customers", "accounts", "transactions"):
        for r in result[dataset][1]:
            assert r["error_reasons"] and all(isinstance(x, str) and x for x in r["error_reasons"])


def test_only_valid_rows_reach_the_curated_output():
    """Distorted reports: nothing invalid may be in the data that reports read."""
    result, _ = _run_examples()
    valid, _ = result["transactions"]
    accounts = {a["account_id"] for a in result["accounts"][0]}
    ids = [r["transaction_id"] for r in valid]
    assert len(ids) == len(set(ids))                       # no duplicate IDs
    for r in valid:
        assert r["account_id"] in accounts                 # no unknown accounts
        assert validate.parse_amount(r["amount"]) is not None
        assert validate.parse_date(r["transaction_date"]) is not None


def test_each_source_system_reconciles_on_its_own():
    result, _ = _run_examples()
    by_system = result["metrics"]["by_source_system"]
    assert set(by_system) == {"CORE_BANKING", "CARDS", "ONLINE_BANKING"}
    assert by_system["CORE_BANKING"]["input_count"] == 5 and by_system["CORE_BANKING"]["rejected_count"] == 2
    assert by_system["CARDS"]["accepted_count"] == 1 and by_system["ONLINE_BANKING"]["rejected_count"] == 2
    assert all(s["counts_consistent"] for s in by_system.values())
    assert sum(s["input_count"] for s in by_system.values()) == result["metrics"]["input_count"]


def test_rework_report_tells_each_system_what_to_fix():
    result, _ = _run_examples()
    report = {(r["source_system"], r["error_reason"]): r for r in result["rework_report"]}
    assert report[("ONLINE_BANKING", "INVALID_DATE")]["example_transaction_ids"] == "T006"
    assert report[("CARDS", "INVALID_AMOUNT")]["rejected_records"] == 1
    assert report[("CORE_BANKING", "DUPLICATE_TRANSACTION_ID")]["example_transaction_ids"] == "T001"
    assert sum(r["rejected_records"] for r in result["rework_report"]) == 6  # one reason each here


def test_missing_or_unknown_source_system_is_rejected():
    base = {"transaction_id": "T1", "account_id": "A1", "amount": "1.00", "currency": "EUR",
            "transaction_type": "PAYMENT", "transaction_date": "2026-10-01", "_line": 2}
    _, rejected = validate.validate_transactions(
        [dict(base, source_system=""), dict(base, transaction_id="T2", source_system="FAX", _line=3)], {"A1"})
    assert [r["error_reasons"] for r in rejected] == [["MISSING_SOURCE_SYSTEM"], ["INVALID_SOURCE_SYSTEM"]]
