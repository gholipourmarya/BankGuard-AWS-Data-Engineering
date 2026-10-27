"""Pure validation functions for BankGuard.

No AWS and no third-party dependencies: the same module is tested with Pytest
and imported by the Glue job, so the tests cover the code that runs in the cloud.

Input rows are dicts of strings. Each row carries its line number in the source
file under the key ``_line`` (header = line 1, first data row = line 2).
Every function returns ``(valid_rows, rejected_rows)``. A rejected row is a copy
of the input plus an ``error_reasons`` list; nothing is dropped silently, so
``len(valid) + len(rejected) == len(input)`` always holds.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext

ALLOWED_CURRENCIES = {"EUR", "USD"}
ALLOWED_TRANSACTION_TYPES = {"DEPOSIT", "WITHDRAWAL", "PAYMENT", "TRANSFER", "REFUND", "REVERSAL"}
NEGATIVE_ALLOWED_TYPES = {"REFUND", "REVERSAL"}
ALLOWED_ACCOUNT_STATUS = {"Active", "Closed"}
ALLOWED_SOURCE_SYSTEMS = {"CORE_BANKING", "CARDS", "ONLINE_BANKING"}

TRANSACTION_FIELDS = ["transaction_id", "account_id", "amount", "currency", "transaction_type", "transaction_date",
                      "source_system"]
ACCOUNT_FIELDS = ["account_id", "customer_id", "status"]
CUSTOMER_FIELDS = ["customer_id", "name", "country"]

_MISSING_MARKERS = {"", "null", "none", "nan", "n/a"}


def is_missing(value):
    """True for None, empty strings and common NULL markers."""
    return value is None or str(value).strip().lower() in _MISSING_MARKERS


def parse_amount(value):
    """Return an exact DECIMAL(18,2), or None; never silently round money."""
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite() or amount.copy_abs() >= Decimal("10000000000000000"):
            return None
        with localcontext() as context:
            context.prec = 18
            stored = amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return stored if stored == amount else None


def parse_date(value):
    """Return a date for strict YYYY-MM-DD input, otherwise None."""
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
    return parsed if parsed.isoformat() == text else None


def _missing_reasons(row, fields):
    reasons = list(row.get("_csv_errors", [])) + [
        "MISSING_" + f.upper() for f in fields if is_missing(row.get(f))]
    for field in fields:
        if field.endswith("_id") and not is_missing(row.get(field)):
            if any(ord(char) < 32 or ord(char) == 127 for char in str(row[field]).strip()):
                reasons.append("INVALID_" + field.upper())
    return reasons


def _reject(row, reasons):
    rejected = dict(row)
    rejected["error_reasons"] = reasons
    return rejected


def _normalize(row):
    """Store accepted strings exactly as compared during validation.

    Copy the row so rejected records and the original input retain their values.
    """
    return {key: value.strip() if isinstance(value, str) else value for key, value in row.items()}


def _content(row, fields):
    return tuple(str(row.get(f, "")).strip() for f in fields)


def _by_line(rows):
    return sorted(rows, key=lambda r: r["_line"])


def validate_customers(rows):
    """Required fields and unique customer_id (lowest line number is kept)."""
    valid, rejected, seen = [], [], set()
    for row in _by_line(rows):
        reasons = _missing_reasons(row, CUSTOMER_FIELDS)
        key = str(row.get("customer_id", "")).strip()
        if key and key in seen:
            reasons.append("DUPLICATE_CUSTOMER_ID")
        elif key:
            seen.add(key)
        (rejected.append(_reject(row, reasons)) if reasons else valid.append(_normalize(row)))
    return valid, rejected


def validate_accounts(rows, valid_customer_ids):
    """Required fields, known status, existing customer, unique account_id."""
    valid, rejected, seen = [], [], set()
    for row in _by_line(rows):
        reasons = _missing_reasons(row, ACCOUNT_FIELDS)
        status = str(row.get("status", "")).strip()
        if status and status not in ALLOWED_ACCOUNT_STATUS:
            reasons.append("INVALID_STATUS")
        customer_id = str(row.get("customer_id", "")).strip()
        if customer_id and customer_id not in valid_customer_ids:
            reasons.append("CUSTOMER_NOT_FOUND")
        key = str(row.get("account_id", "")).strip()
        if key and key in seen:
            reasons.append("DUPLICATE_ACCOUNT_ID")
        elif key:
            seen.add(key)
        (rejected.append(_reject(row, reasons)) if reasons else valid.append(_normalize(row)))
    return valid, rejected


def validate_transactions(rows, valid_account_ids):
    """Validate transactions against the MVP rules plus currency/type checks.

    Duplicate policy: for a repeated transaction_id the occurrence with the lowest
    line number is the original. It is judged on its own merits. Every later
    occurrence is rejected with DUPLICATE_TRANSACTION_ID, and additionally with
    DUPLICATE_CONFLICTING_CONTENT if its content differs from the original.
    """
    valid, rejected, first_seen = [], [], {}
    for row in _by_line(rows):
        reasons = _missing_reasons(row, TRANSACTION_FIELDS)

        tx_id = str(row.get("transaction_id", "")).strip()
        if tx_id:
            if tx_id in first_seen:
                reasons.append("DUPLICATE_TRANSACTION_ID")
                if _content(row, TRANSACTION_FIELDS) != _content(first_seen[tx_id], TRANSACTION_FIELDS):
                    reasons.append("DUPLICATE_CONFLICTING_CONTENT")
            else:
                first_seen[tx_id] = row

        ttype = str(row.get("transaction_type", "")).strip()
        if ttype and ttype not in ALLOWED_TRANSACTION_TYPES:
            reasons.append("INVALID_TRANSACTION_TYPE")

        source = str(row.get("source_system", "")).strip()
        if source and source not in ALLOWED_SOURCE_SYSTEMS:
            reasons.append("INVALID_SOURCE_SYSTEM")

        currency = str(row.get("currency", "")).strip()
        if currency and currency not in ALLOWED_CURRENCIES:
            reasons.append("INVALID_CURRENCY")

        if not is_missing(row.get("amount")):
            amount = parse_amount(row["amount"])
            if amount is None:
                reasons.append("INVALID_AMOUNT")
            elif amount == 0 or (amount < 0 and ttype not in NEGATIVE_ALLOWED_TYPES):
                reasons.append("AMOUNT_NOT_ALLOWED_FOR_TYPE")

        if not is_missing(row.get("transaction_date")) and parse_date(row["transaction_date"]) is None:
            reasons.append("INVALID_DATE")

        account_id = str(row.get("account_id", "")).strip()
        if account_id and account_id not in valid_account_ids:
            reasons.append("ACCOUNT_NOT_FOUND")

        (rejected.append(_reject(row, reasons)) if reasons else valid.append(_normalize(row)))
    return valid, rejected
