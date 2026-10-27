"""Resolve the published BankGuard snapshot once, before starting any queries."""
import argparse
import json


def read_published_snapshot(s3, bucket):
    with s3.get_object(Bucket=bucket, Key="published/current.json")["Body"] as body:
        receipt = json.load(body)
    if not receipt.get("database") or not receipt.get("run_id") or not receipt.get("counts_consistent"):
        raise ValueError("Invalid published snapshot receipt")
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
