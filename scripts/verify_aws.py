"""Verify a published AWS snapshot and save real query results and timings.

Runs Athena queries (billable); does not upload data or start Glue jobs.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

try:
    from .athena_scan_compare import build_queries, run
    from .current_snapshot import read_published_snapshot
except ImportError:
    from athena_scan_compare import build_queries, run
    from current_snapshot import read_published_snapshot


def verify(s3, glue, athena, bucket, job, workgroup, expected_prefix=None):
    receipt = read_published_snapshot(s3, bucket)
    if expected_prefix and receipt["input_prefix"] != expected_prefix:
        raise ValueError("The expected input package has not been published")
    execution = glue.get_job_run(JobName=job, RunId=receipt["run_id"])["JobRun"]
    if execution["JobRunState"] != "SUCCEEDED":
        raise ValueError("Published Glue run has not reached SUCCEEDED")
    database = receipt["database"]
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "snapshot": receipt,
              "glue_run": execution, "queries": {}}

    def query(name, sql):
        stats, rows = run(athena, sql, workgroup, database)
        report["queries"][name] = {"sql": sql, "statistics": stats, "rows": rows}
        return rows

    counts = query("balance", "SELECT (SELECT COUNT(*) FROM curated_transactions) AS accepted, "
                   "(SELECT COUNT(*) FROM rejected_transactions) AS rejected")
    if tuple(map(int, counts[1])) != (receipt["accepted_count"], receipt["rejected_count"]):
        raise ValueError("Athena counts differ from the Glue receipt")
    sources = query("accepted_by_source", "SELECT source_system, COUNT(*) FROM curated_transactions "
                    "GROUP BY source_system ORDER BY source_system")
    if {row[0]: int(row[1]) for row in sources[1:]} != {
        key: value["accepted_count"] for key, value in receipt["by_source_system"].items()
        if value["accepted_count"]
    }:
        raise ValueError("Athena source-system counts differ from the Glue receipt")
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    for path in sorted(sql_dir.glob("*.sql")):
        if path.name == "compare_csv_vs_parquet.sql":
            continue
        for index, statement in enumerate(path.read_text().split(";"), 1):
            if statement.strip():
                query(f"{path.stem}_{index}", statement)
    dates = query("comparison_day", "SELECT MIN(transaction_date) FROM curated_transactions")
    day = dates[1][0]
    if not day:
        raise ValueError("No accepted transactions for the format comparison")
    comparison = {name: query(name, sql) for name, sql in build_queries(day).items()}
    if comparison["CSV"] != comparison["Parquet"] or len(comparison["CSV"]) < 2:
        raise ValueError("CSV and Parquet comparison has unequal or empty results")
    report["comparison_day"] = day
    report["counts_verified"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket")
    parser.add_argument("--job", default="bankguard-etl")
    parser.add_argument("--workgroup", default="bankguard")
    parser.add_argument("--expected-prefix")
    parser.add_argument("--out", default="build/aws-verification.json")
    args = parser.parse_args()
    import boto3
    report = verify(boto3.client("s3"), boto3.client("glue"), boto3.client("athena"),
                    args.bucket, args.job, args.workgroup, args.expected_prefix)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"Verified {report['snapshot']['run_id']}; evidence: {target}")
    for name in ("CSV", "Parquet"):
        print(name, report["queries"][name]["statistics"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
