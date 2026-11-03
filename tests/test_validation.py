import pytest

from src import validate


def tx(line, **kw):
    row = {"transaction_id": f"T{line:03d}", "account_id": "A1", "amount": "10.00",
           "currency": "EUR", "transaction_type": "PAYMENT", "transaction_date": "2026-10-01",
           "source_system": "CORE_BANKING", "_line": line}
    row.update(kw)
    return row


ACCOUNTS = {"A1", "A2"}


def reasons_of(rejected):
    return [r["error_reasons"] for r in rejected]


@pytest.mark.parametrize("control", ["\x00", "\x7f", "\u0085", "\u009f"])
@pytest.mark.parametrize("dataset,field", [
    ("customers", "customer_id"), ("accounts", "account_id"), ("accounts", "customer_id"),
    ("transactions", "transaction_id"), ("transactions", "account_id"),
])
def test_ids_reject_ascii_and_unicode_control_characters(control, dataset, field):
    identifier = f"X{control}Y"
    if dataset == "customers":
        row = {"customer_id": identifier, "name": "Alice", "country": "DE", "_line": 2}
        valid, rejected = validate.validate_customers([row])
    elif dataset == "accounts":
        row = {"account_id": "A1", "customer_id": "C1", "status": "Active", "_line": 2, field: identifier}
        valid, rejected = validate.validate_accounts([row], {row["customer_id"]})
    else:
        row = tx(2, **{field: identifier})
        valid, rejected = validate.validate_transactions([row], {row["account_id"]})
    assert valid == []
    assert reasons_of(rejected) == [[f"INVALID_{field.upper()}"]]


def test_valid_transaction_is_accepted():
    valid, rejected = validate.validate_transactions([tx(2)], ACCOUNTS)
    assert len(valid) == 1 and rejected == []


def test_missing_amount_rejected_for_empty_and_null_marker():
    rows = [tx(2, amount=""), tx(3, amount="NULL")]
    valid, rejected = validate.validate_transactions(rows, ACCOUNTS)
    assert valid == []
    assert reasons_of(rejected) == [["MISSING_AMOUNT"], ["MISSING_AMOUNT"]]


def test_invalid_amount_rejected():
    _, rejected = validate.validate_transactions([tx(2, amount="abc")], ACCOUNTS)
    assert reasons_of(rejected) == [["INVALID_AMOUNT"]]


def test_invalid_dates_rejected():
    rows = [tx(2, transaction_date="invalid_date"), tx(3, transaction_date="2026-13-45"),
            tx(4, transaction_date="31.10.2026")]
    _, rejected = validate.validate_transactions(rows, ACCOUNTS)
    assert reasons_of(rejected) == [["INVALID_DATE"]] * 3


@pytest.mark.parametrize("value", ["2026-1-02", "2026-01-2", "2026-1-2", "2026-02-29"])
def test_noncanonical_and_impossible_dates_are_rejected(value):
    valid, rejected = validate.validate_transactions([tx(2, transaction_date=value)], ACCOUNTS)
    assert valid == []
    assert reasons_of(rejected) == [["INVALID_DATE"]]


@pytest.mark.parametrize("value", ["2026-01-02", "2024-02-29", " 2026-01-02 "])
def test_canonical_dates_are_accepted_and_trimmed(value):
    valid, rejected = validate.validate_transactions([tx(2, transaction_date=value)], ACCOUNTS)
    assert rejected == []
    assert valid[0]["transaction_date"] == value.strip()


def test_unknown_account_rejected():
    _, rejected = validate.validate_transactions([tx(2, account_id="A999")], ACCOUNTS)
    assert reasons_of(rejected) == [["ACCOUNT_NOT_FOUND"]]


def test_negative_amount_depends_on_transaction_type():
    rows = [tx(2, amount="-5.00", transaction_type="PAYMENT"),
            tx(3, amount="-5.00", transaction_type="REFUND"),
            tx(4, amount="-5.00", transaction_type="REVERSAL")]
    valid, rejected = validate.validate_transactions(rows, ACCOUNTS)
    assert [r["_line"] for r in valid] == [3, 4]
    assert reasons_of(rejected) == [["AMOUNT_NOT_ALLOWED_FOR_TYPE"]]


def test_zero_amount_rejected():
    _, rejected = validate.validate_transactions([tx(2, amount="0.00")], ACCOUNTS)
    assert reasons_of(rejected) == [["AMOUNT_NOT_ALLOWED_FOR_TYPE"]]


def test_currency_and_type_checks():
    rows = [tx(2, currency="XXX"), tx(3, transaction_type="GIFT")]
    _, rejected = validate.validate_transactions(rows, ACCOUNTS)
    assert reasons_of(rejected) == [["INVALID_CURRENCY"], ["INVALID_TRANSACTION_TYPE"]]


def test_duplicate_keeps_lowest_line_and_flags_later_one():
    rows = [tx(2, transaction_id="T1"), tx(9, transaction_id="T1")]
    valid, rejected = validate.validate_transactions(rows, ACCOUNTS)
    assert [r["_line"] for r in valid] == [2]
    assert reasons_of(rejected) == [["DUPLICATE_TRANSACTION_ID"]]


def test_duplicate_is_independent_of_input_order():
    rows = [tx(9, transaction_id="T1"), tx(2, transaction_id="T1")]
    valid, _ = validate.validate_transactions(rows, ACCOUNTS)
    assert [r["_line"] for r in valid] == [2]


def test_conflicting_duplicate_gets_extra_reason():
    rows = [tx(2, transaction_id="T1", amount="10.00"), tx(3, transaction_id="T1", amount="99.00")]
    _, rejected = validate.validate_transactions(rows, ACCOUNTS)
    assert reasons_of(rejected) == [["DUPLICATE_TRANSACTION_ID", "DUPLICATE_CONFLICTING_CONTENT"]]


def test_multiple_errors_are_all_reported():
    _, rejected = validate.validate_transactions(
        [tx(2, account_id="A999", amount="abc", transaction_date="nope")], ACCOUNTS)
    assert set(rejected[0]["error_reasons"]) == {"ACCOUNT_NOT_FOUND", "INVALID_AMOUNT", "INVALID_DATE"}


def test_counts_always_reconcile():
    rows = [tx(2), tx(3, amount=""), tx(4, transaction_id="T002"), tx(5, account_id="Z")]
    valid, rejected = validate.validate_transactions(rows, ACCOUNTS)
    assert len(valid) + len(rejected) == len(rows)


def test_every_rejected_record_has_a_reason():
    rows = [tx(2, amount=""), tx(3, currency="XXX"), tx(4, account_id="Z")]
    _, rejected = validate.validate_transactions(rows, ACCOUNTS)
    assert all(r["error_reasons"] for r in rejected)


def test_accounts_need_existing_customer_and_known_status():
    rows = [{"account_id": "A1", "customer_id": "C1", "status": "Active", "_line": 2},
            {"account_id": "A2", "customer_id": "C9", "status": "Active", "_line": 3},
            {"account_id": "A3", "customer_id": "C1", "status": "Frozen", "_line": 4},
            {"account_id": "A1", "customer_id": "C1", "status": "Active", "_line": 5}]
    valid, rejected = validate.validate_accounts(rows, {"C1"})
    assert [r["account_id"] for r in valid] == ["A1"]
    assert reasons_of(rejected) == [["CUSTOMER_NOT_FOUND"], ["INVALID_STATUS"], ["DUPLICATE_ACCOUNT_ID"]]


def test_customers_required_fields_and_duplicates():
    rows = [{"customer_id": "C1", "name": "Ali", "country": "Germany", "_line": 2},
            {"customer_id": "C2", "name": "", "country": "France", "_line": 3},
            {"customer_id": "C1", "name": "Ali", "country": "Germany", "_line": 4}]
    valid, rejected = validate.validate_customers(rows)
    assert [r["customer_id"] for r in valid] == ["C1"]
    assert reasons_of(rejected) == [["MISSING_NAME"], ["DUPLICATE_CUSTOMER_ID"]]


@pytest.mark.parametrize("amount", ["10000000000000000.00", "-10000000000000000.00", "0.001", "1.001", "1e10000"])
def test_amounts_that_cannot_be_stored_exactly_are_rejected(amount):
    valid, rejected = validate.validate_transactions([tx(2, amount=amount)], ACCOUNTS)
    assert valid == []
    assert "INVALID_AMOUNT" in rejected[0]["error_reasons"]


@pytest.mark.parametrize("amount", ["9999999999999999.99", "0.01", "1.230", "1E2"])
def test_exact_decimal_amounts_are_accepted(amount):
    valid, rejected = validate.validate_transactions([tx(2, amount=amount)], ACCOUNTS)
    assert len(valid) == 1 and not rejected


@pytest.mark.parametrize("field,value", [("transaction_id", "T1\nT2"), ("account_id", "A1\nA2"),
                                         ("transaction_id", "T\t1"), ("account_id", "A\r1")])
def test_control_characters_in_transaction_identifiers_are_rejected(field, value):
    valid, rejected = validate.validate_transactions([tx(2, **{field: value})], {"A1", value})
    assert not valid
    assert "INVALID_" + field.upper() in rejected[0]["error_reasons"]


def test_control_characters_in_reference_identifiers_are_rejected():
    customers = [{"customer_id": "C1\nC2", "name": "Alice", "country": "DE", "_line": 2}]
    valid, rejected = validate.validate_customers(customers)
    assert not valid and "INVALID_CUSTOMER_ID" in rejected[0]["error_reasons"]
    accounts = [{"account_id": "A\n1", "customer_id": "C1", "status": "Active", "_line": 2}]
    valid, rejected = validate.validate_accounts(accounts, {"C1"})
    assert not valid and "INVALID_ACCOUNT_ID" in rejected[0]["error_reasons"]
