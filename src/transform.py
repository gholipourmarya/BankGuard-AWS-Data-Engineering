"""Pipeline orchestration on in-memory rows plus a local runner.

``run_pipeline`` has no I/O, so the Glue job and the local runner share it.
Local usage:  python -m src.transform [data_dir] [output_dir]
"""
import csv
import json
import os
import sys
from datetime import datetime, timezone

try:  # imported as part of the "src" package (local, Pytest)
    from . import validate
except ImportError:  # imported as a top-level module (Glue --extra-py-files)
    import validate


def read_csv_rows(path):
    """Read a CSV file; each row gets its source line number in ``_line``."""
    fields = {"customers.csv": validate.CUSTOMER_FIELDS, "accounts.csv": validate.ACCOUNT_FIELDS,
              "transactions.csv": validate.TRANSACTION_FIELDS}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return parse_csv_rows(f, fields.get(os.path.basename(path)))


def parse_csv_rows(stream, required_fields=None):
    """Shared local/Glue CSV reader, including quoted multiline fields.

    Invalid headers/CSV syntax fail the file explicitly. Wrong row widths are
    rejected by validation, preserving extra values and source line numbers.
    """
    reader = csv.reader(stream, strict=True)
    header = next(reader, None)
    if not header or any(not field for field in header) or len(header) != len(set(header)):
        raise ValueError("CSV header is missing, empty or contains duplicate columns")
    if required_fields is not None and set(header) != set(required_fields):
        raise ValueError(f"Expected CSV columns: {', '.join(required_fields)}")
    if any(field.startswith("_") or field in {"error_reasons", "extra_values"} for field in header):
        raise ValueError("CSV header contains reserved columns")
    rows = []
    while True:
        line = reader.line_num + 1
        values = next(reader, None)
        if values is None:
            break
        if not values:
            continue
        row = dict(zip(header, values))
        row["_line"] = line
        if len(values) != len(header):
            row["_csv_errors"] = ["INVALID_COLUMN_COUNT"]
            if len(values) > len(header):
                row["extra_values"] = values[len(header):]
        rows.append(row)
    return rows


def add_audit_columns(rows, run_id, source_file, ingestion_timestamp):
    out = []
    for row in rows:
        r = dict(row)
        r["run_id"] = run_id
        r["source_file"] = source_file
        r["ingestion_timestamp"] = ingestion_timestamp
        out.append(r)
    return out


def compute_metrics(input_count, accepted_count, rejected_rows):
    rejected_count = len(rejected_rows)
    reasons = {}
    for row in rejected_rows:
        for reason in row["error_reasons"]:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "input_count": input_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "counts_consistent": input_count == accepted_count + rejected_count,
        "pass_rate_percent": round(accepted_count / input_count * 100, 2) if input_count else 0.0,
        "rejections_by_reason": dict(sorted(reasons.items())),
    }


def _system(row):
    value = str(row.get("source_system", "")).strip()
    return value or "(missing)"


def metrics_by_source_system(transactions, valid, rejected):
    """Input, accepted and rejected per originating system; each must reconcile on its own."""
    stats = {}
    for name, rows in (("input_count", transactions), ("accepted_count", valid), ("rejected_count", rejected)):
        for row in rows:
            stats.setdefault(_system(row), {"input_count": 0, "accepted_count": 0, "rejected_count": 0})[name] += 1
    for s in stats.values():
        s["counts_consistent"] = s["input_count"] == s["accepted_count"] + s["rejected_count"]
        s["pass_rate_percent"] = round(s["accepted_count"] / s["input_count"] * 100, 2) if s["input_count"] else 0.0
    return dict(sorted(stats.items()))


def build_rework_report(rejected, max_examples=5):
    """One row per (source system, reason): what to fix and where to start.

    A record with several reasons appears once per reason, so the counts here can add up to
    more than the number of rejected records.
    """
    groups = {}
    for row in rejected:
        for reason in row["error_reasons"]:
            groups.setdefault((_system(row), reason), []).append(str(row.get("transaction_id", "")).strip() or "(no id)")
    report = []
    for (system, reason), ids in sorted(groups.items()):
        report.append({
            "source_system": system,
            "error_reason": reason,
            "rejected_records": len(ids),
            "example_transaction_ids": " ".join(list(dict.fromkeys(ids))[:max_examples]),
        })
    return report


def run_pipeline(customers, accounts, transactions, run_id, ingestion_timestamp):
    """Validate all three datasets in dependency order; return results and metrics.

    Only accounts that passed validation count as valid references for
    transactions, and only valid customers count for accounts.
    """
    v_cust, r_cust = validate.validate_customers(customers)
    v_acc, r_acc = validate.validate_accounts(accounts, {c["customer_id"].strip() for c in v_cust})
    v_tx, r_tx = validate.validate_transactions(transactions, {a["account_id"].strip() for a in v_acc})

    def audit(rows, name):
        return add_audit_columns(rows, run_id, name, ingestion_timestamp)

    return {
        "customers": (audit(v_cust, "customers.csv"), audit(r_cust, "customers.csv")),
        "accounts": (audit(v_acc, "accounts.csv"), audit(r_acc, "accounts.csv")),
        "transactions": (audit(v_tx, "transactions.csv"), audit(r_tx, "transactions.csv")),
        "metrics": {**compute_metrics(len(transactions), len(v_tx), r_tx),
                    "by_source_system": metrics_by_source_system(transactions, v_tx, r_tx)},
        "rework_report": build_rework_report(r_tx),
    }


def _clean(row):
    return {k: v for k, v in row.items() if k not in {"_line", "_csv_errors"}}


def write_local_outputs(result, output_dir):
    """Write valid rows as CSV and rejected rows as JSON lines (deterministic order)."""
    os.makedirs(output_dir, exist_ok=True)
    fields = {"customers": validate.CUSTOMER_FIELDS, "accounts": validate.ACCOUNT_FIELDS,
              "transactions": validate.TRANSACTION_FIELDS}
    for name in ("customers", "accounts", "transactions"):
        valid, rejected = result[name]
        fieldnames = (list(_clean(valid[0]).keys()) if valid else
                      fields[name] + ["run_id", "source_file", "ingestion_timestamp"])
        with open(os.path.join(output_dir, f"curated_{name}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(_clean(r) for r in valid)
        with open(os.path.join(output_dir, f"rejected_{name}.jsonl"), "w", encoding="utf-8") as f:
            for r in rejected:
                f.write(json.dumps({**_clean(r), "line": r["_line"]}, sort_keys=True) + "\n")
    report = result["rework_report"]
    with open(os.path.join(output_dir, "rework_report.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["source_system", "error_reason", "rejected_records", "example_transaction_ids"])
        w.writeheader()
        w.writerows(report)
    with open(os.path.join(output_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(result["metrics"], f, indent=2, sort_keys=True)


def main(data_dir="data", output_dir="output"):
    customers = read_csv_rows(os.path.join(data_dir, "customers.csv"))
    accounts = read_csv_rows(os.path.join(data_dir, "accounts.csv"))
    transactions = read_csv_rows(os.path.join(data_dir, "transactions.csv"))
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    result = run_pipeline(customers, accounts, transactions, run_id, started.isoformat())
    write_local_outputs(result, output_dir)
    print(json.dumps(result["metrics"], indent=2))
    return result


if __name__ == "__main__":
    main(*sys.argv[1:3])
