"""Resolve the published BankGuard snapshot once, before starting any queries."""
import argparse
import json
from pathlib import Path
import re
import sys

# Support both `python scripts/current_snapshot.py` and package imports.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.validate import validate_metrics


def _read_receipt(s3, bucket, key):
    with s3.get_object(Bucket=bucket, Key=key)["Body"] as body:
        receipt = json.load(body)
    if (not isinstance(receipt, dict)
            or any(not isinstance(receipt.get(field), str) or not receipt[field].strip()
                   for field in ("database", "run_id"))
            or receipt.get("counts_consistent") is not True):
        raise ValueError("Invalid published snapshot receipt")
    validate_metrics(receipt)
    return receipt


def read_published_snapshot(s3, bucket):
    return _read_receipt(s3, bucket, "published/current.json")


def read_run_snapshot(s3, bucket, run_id):
    """Read a fixed run's receipt even after a newer snapshot is published."""
    if not isinstance(run_id, str) or not re.fullmatch(r"jr_[a-zA-Z0-9_]+", run_id):
        raise ValueError("Expected an AWS Glue run ID")
    receipt = _read_receipt(s3, bucket, f"runs/{run_id}/metrics.json")
    if receipt["run_id"] != run_id:
        raise ValueError("Snapshot receipt does not identify the requested run")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket")
    parser.add_argument("--database-only", action="store_true")
    args = parser.parse_args()
    import boto3
    receipt = read_published_snapshot(boto3.client("s3"), args.bucket)
    print(receipt["database"] if args.database_only else json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
