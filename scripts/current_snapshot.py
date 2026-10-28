"""Resolve the published BankGuard snapshot once, before starting any queries."""
import argparse
import json
from pathlib import Path
import sys

# Support both `python scripts/current_snapshot.py` and package imports.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.validate import validate_metrics


def read_published_snapshot(s3, bucket):
    with s3.get_object(Bucket=bucket, Key="published/current.json")["Body"] as body:
        receipt = json.load(body)
    if (not isinstance(receipt, dict)
            or any(not isinstance(receipt.get(field), str) or not receipt[field].strip()
                   for field in ("database", "run_id"))
            or receipt.get("counts_consistent") is not True):
        raise ValueError("Invalid published snapshot receipt")
    validate_metrics(receipt)
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
