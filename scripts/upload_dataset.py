"""Upload a complete, uniquely named data package and release its ready marker last."""
import argparse
import json
from pathlib import Path
import uuid

FILES = ("customers.csv", "accounts.csv", "transactions.csv")


def upload_dataset(s3, bucket, data_dir):
    paths = [Path(data_dir) / name for name in FILES]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    prefix = f"raw/batches/{uuid.uuid4().hex}"
    for path in paths:
        s3.upload_file(str(path), bucket, f"{prefix}/{path.name}")
    s3.put_object(Bucket=bucket, Key=f"{prefix}/ready.json", ContentType="application/json",
                  Body=json.dumps({"files": list(FILES)}).encode("utf-8"))
    return prefix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket")
    parser.add_argument("data_dir", nargs="?", default="data")
    args = parser.parse_args()
    import boto3
    print(upload_dataset(boto3.client("s3"), args.bucket, args.data_dir))


if __name__ == "__main__":
    main()
