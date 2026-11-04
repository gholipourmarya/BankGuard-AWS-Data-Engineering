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
    from .current_snapshot import read_published_snapshot, read_run_snapshot
except ImportError:
    from athena_scan_compare import build_queries, run
    from current_snapshot import read_published_snapshot, read_run_snapshot

from src.validate import source_system_label
from src.snapshot import snapshot_database


def sql_statements(text):
    """Split report SQL without treating quoted/commented semicolons as separators."""
    statements, buffer = [], []
    quote, line_comment, block_depth = None, False, 0
    index = 0
    while index < len(text):
        char = text[index]
        pair = text[index:index + 2]
        if line_comment:
            if char in "\r\n":
                line_comment = False
                buffer.append(char)
        elif block_depth:
            if pair == "/*":
                block_depth += 1
                index += 1
            elif pair == "*/":
                block_depth -= 1
                index += 1
        elif quote:
            buffer.append(char)
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    buffer.append(text[index + 1])
                    index += 1
                else:
                    quote = None
        elif pair == "--":
            line_comment = True
            buffer.append(" ")
            index += 1
        elif pair == "/*":
            block_depth = 1
            buffer.append(" ")
            index += 1
        elif char in "'\"`":
            quote = char
            buffer.append(char)
        elif char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
        else:
            buffer.append(char)
        index += 1
    if quote or block_depth:
        raise ValueError("Unterminated SQL quote or block comment")
    statement = "".join(buffer).strip()
    if statement:
        statements.append(statement)
    return statements


def count_rejected_rows(s3, bucket, prefix):
    """Count actual Spark JSON rows, ignoring success markers and sidecar files."""
    count = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            filename = key.rsplit("/", 1)[-1]
            if filename.startswith(("_", ".")) or not filename.endswith(".json"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"]
            # StreamingBody.__enter__ returns its raw stream, which has no iter_lines.
            with body:
                for line in body.iter_lines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if (not isinstance(row, dict) or not isinstance(row.get("error_reasons"), list)
                            or not row["error_reasons"]):
                        raise ValueError(f"Invalid rejected row in {key}")
                    count += 1
    return count


def verify(s3, glue, athena, bucket, job, workgroup, expected_prefix=None, run_id=None):
    receipt = read_run_snapshot(s3, bucket, run_id) if run_id is not None else read_published_snapshot(s3, bucket)
    if expected_prefix and receipt["input_prefix"] != expected_prefix:
        raise ValueError("The expected input package has not been published")
    execution = glue.get_job_run(JobName=job, RunId=receipt["run_id"])["JobRun"]
    if execution["JobRunState"] != "SUCCEEDED":
        raise ValueError("Published Glue run has not reached SUCCEEDED")
    if execution.get("Id") != receipt["run_id"] or execution.get("JobName") != job:
        raise ValueError("Glue execution does not identify the requested job and run")
    # GetJobRun returns run overrides, not the complete resolved arguments.
    # Preserve Glue's precedence: fixed arguments > run overrides > defaults.
    definition = glue.get_job(JobName=job)["Job"]
    arguments = {**definition.get("DefaultArguments", {}), **execution.get("Arguments", {}),
                 **definition.get("NonOverridableArguments", {})}
    if any(not isinstance(arguments.get(key), str) or not arguments[key]
           for key in ("--bucket", "--database", "--input_prefix")):
        raise ValueError("Glue job is missing required snapshot arguments")
    if arguments["--bucket"] != bucket:
        raise ValueError("Glue bucket differs from the snapshot bucket")
    if receipt.get("input_prefix") != arguments["--input_prefix"].rstrip("/"):
        raise ValueError("Snapshot input prefix differs from the Glue input prefix")
    if receipt["database"] != snapshot_database(arguments["--database"], receipt["run_id"]):
        raise ValueError("Snapshot database does not belong to this Glue run")
    if receipt.get("data_prefix") != f"snapshots/{receipt['run_id']}/":
        raise ValueError("Snapshot data prefix does not belong to this Glue run")
    database = receipt["database"]
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "snapshot": receipt,
              "glue_run": execution, "queries": {},
              "receipt_key": f"runs/{run_id}/metrics.json" if run_id is not None else "published/current.json"}

    def query(name, sql):
        stats, rows = run(athena, sql, workgroup, database)
        report["queries"][name] = {"sql": sql, "statistics": stats, "rows": rows}
        return rows

    counts = query("balance", "SELECT (SELECT COUNT(*) FROM curated_transactions) AS accepted, "
                   "(SELECT COUNT(*) FROM rejected_transactions) AS rejected")
    if tuple(map(int, counts[1])) != (receipt["accepted_count"], receipt["rejected_count"]):
        raise ValueError("Athena counts differ from the Glue receipt")
    report["verified_datasets"] = ["transactions"]
    if "by_dataset" in receipt:
        references = query("reference_balance", "SELECT (SELECT COUNT(*) FROM curated_customers) AS customers_accepted, "
                           "(SELECT COUNT(*) FROM curated_accounts) AS accounts_accepted")
        accepted_counts = tuple(map(int, references[1]))
        if len(accepted_counts) != 2:
            raise ValueError("Invalid Athena reference counts")
        for name, accepted in zip(("customers", "accounts"), accepted_counts):
            rejected = count_rejected_rows(s3, bucket, f"snapshots/{receipt['run_id']}/rejected/{name}/")
            expected = receipt["by_dataset"][name]
            if (accepted, rejected) != (expected["accepted_count"], expected["rejected_count"]):
                raise ValueError(f"Snapshot {name} counts differ from the Glue receipt")
            report["verified_datasets"].append(name)
    for name, table, field in (("accepted", "curated_transactions", "accepted_count"),
                               ("rejected", "rejected_transactions", "rejected_count")):
        sources = query(f"{name}_by_source", f"SELECT source_system, COUNT(*) FROM {table} "
                        "GROUP BY source_system ORDER BY source_system")
        actual = {}
        for source, count in sources[1:]:
            # Rejected rows retain their original whitespace/missing fields.
            label = source_system_label(source)
            actual[label] = actual.get(label, 0) + int(count)
        expected = {key: value[field] for key, value in receipt["by_source_system"].items() if value[field]}
        if actual != expected:
            raise ValueError(f"Athena {name} source-system counts differ from the Glue receipt")
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    for path in sorted(sql_dir.glob("*.sql")):
        if path.name == "compare_csv_vs_parquet.sql":
            continue
        for index, statement in enumerate(sql_statements(path.read_text()), 1):
            query(f"{path.stem}_{index}", statement)
    dates = query("comparison_day", "SELECT MIN(transaction_date) FROM curated_transactions")
    day = dates[1][0]
    if not day:
        if receipt["accepted_count"]:
            raise ValueError("Accepted transactions have no comparison date")
        report.update({"counts_verified": True, "comparison_day": None,
                       "comparison_status": "skipped",
                       "comparison_skip_reason": "No accepted transactions for the format comparison"})
        return report
    comparison = {name: query(name, sql) for name, sql in build_queries(day).items()}
    if comparison["CSV"] != comparison["Parquet"] or len(comparison["CSV"]) < 2:
        raise ValueError("CSV and Parquet comparison has unequal or empty results")
    report["comparison_day"] = day
    report["comparison_status"] = "completed"
    report["counts_verified"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket")
    parser.add_argument("--job", default="bankguard-etl")
    parser.add_argument("--workgroup", default="bankguard")
    parser.add_argument("--expected-prefix")
    parser.add_argument("--run-id", help="Verify this immutable Glue run instead of the latest published snapshot")
    parser.add_argument("--out", default="build/aws-verification.json")
    args = parser.parse_args()
    import boto3
    report = verify(boto3.client("s3"), boto3.client("glue"), boto3.client("athena"),
                    args.bucket, args.job, args.workgroup, args.expected_prefix, args.run_id)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"Verified {report['snapshot']['run_id']}; evidence: {target}")
    if report.get("comparison_status") == "skipped":
        print(f"Format comparison skipped: {report['comparison_skip_reason']}")
    else:
        for name in ("CSV", "Parquet"):
            print(name, report["queries"][name]["statistics"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
