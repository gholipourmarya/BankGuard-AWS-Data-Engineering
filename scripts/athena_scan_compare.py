"""Compare identical accepted transactions as CSV and Parquet in Athena.

Usage: python scripts/athena_scan_compare.py [date] [workgroup] --database NAME
The Glue job prepares both datasets in one immutable snapshot database. Query result reuse is disabled. Never copy
raw transactions to comparison/csv/: errors and duplicates invalidate the comparison.
"""
import argparse
from datetime import date
import sys
import time


def build_queries(day):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("date must use YYYY-MM-DD")
    return {
        "CSV": f"""SELECT currency, COUNT(*) AS transaction_count,
                       SUM(TRY_CAST(amount AS DECIMAL(18,2))) AS total_amount
                   FROM comparison_transactions_csv
                   WHERE transaction_date = '{day}' GROUP BY currency ORDER BY currency""",
        "Parquet": f"""SELECT currency, COUNT(*) AS transaction_count, SUM(amount) AS total_amount
                       FROM curated_transactions
                       WHERE transaction_date = '{day}' GROUP BY currency ORDER BY currency""",
    }


def run(athena, sql, workgroup, database, timeout=300):
    qid = athena.start_query_execution(
        QueryString=sql, WorkGroup=workgroup, QueryExecutionContext={"Database": database},
        ResultReuseConfiguration={"ResultReuseByAgeConfiguration": {"Enabled": False}},
    )["QueryExecutionId"]
    deadline = time.monotonic() + timeout
    while True:
        execution = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = execution["Status"]["State"]
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"{state}: {execution['Status'].get('StateChangeReason', '')}")
        if state == "SUCCEEDED":
            rows = []
            for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
                rows.extend(tuple(cell.get("VarCharValue") for cell in row["Data"])
                            for row in page["ResultSet"]["Rows"])
            return execution["Statistics"], rows
        if time.monotonic() >= deadline:
            athena.stop_query_execution(QueryExecutionId=qid)
            raise TimeoutError(f"Athena query {qid} exceeded {timeout} seconds")
        time.sleep(1)


def compare(athena, day, workgroup, database, timeout=300):
    results = {name: run(athena, sql, workgroup, database, timeout)
               for name, sql in build_queries(day).items()}
    if results["CSV"][1] != results["Parquet"][1]:
        raise RuntimeError("CSV and Parquet results differ; scan comparison is invalid")
    # A header without data is not a useful performance measurement.
    if len(results["CSV"][1]) < 2:
        raise RuntimeError("No transactions for this date; select a date with accepted records")
    return {name: result[0] for name, result in results.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", nargs="?", default="2026-03-04")
    parser.add_argument("workgroup", nargs="?", default="bankguard")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", help="Immutable snapshot database, not the template database")
    source.add_argument("--bucket", help="Resolve published/current.json once from this bucket")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    build_queries(args.date)
    import boto3
    database = args.database
    if args.bucket:
        try:
            from .current_snapshot import read_published_snapshot
        except ImportError:
            from current_snapshot import read_published_snapshot
        database = read_published_snapshot(boto3.client("s3"), args.bucket)["database"]
    results = compare(boto3.client("athena"), args.date, args.workgroup, database, args.timeout)
    for name, stats in results.items():
        print(f"{name:8} scanned {stats['DataScannedInBytes']:>12,} bytes, "
              f"engine time {stats['EngineExecutionTimeInMillis']} ms")
    csv_bytes, parquet_bytes = (results[name]["DataScannedInBytes"] for name in ("CSV", "Parquet"))
    if parquet_bytes:
        print(f"CSV/Parquet scan ratio: {csv_bytes / parquet_bytes:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
