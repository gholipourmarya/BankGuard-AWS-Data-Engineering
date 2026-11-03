"""List runtime snapshot databases; delete them only with --delete after stopping ETL.

Run before terraform destroy. --include-published also removes the publication
pointer and current snapshot catalog. S3 data is removed by terraform destroy.
"""
import argparse

try:
    from .current_snapshot import read_published_snapshot
except ImportError:
    from current_snapshot import read_published_snapshot


def cleanup(glue, s3, bucket, template_database, delete=False, include_published=False):
    try:
        current = read_published_snapshot(s3, bucket)["database"]
    except s3.exceptions.NoSuchKey:
        current = None
    names = []
    for page in glue.get_paginator("get_databases").paginate():
        names.extend(db["Name"] for db in page.get("DatabaseList", [])
                     if db["Name"].startswith(f"{template_database}_jr_")
                     and (include_published or db["Name"] != current))
    if delete:
        if include_published and current in names:
            s3.delete_object(Bucket=bucket, Key="published/current.json")
        for name in names:
            glue.delete_database(Name=name)
    return sorted(names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket")
    parser.add_argument("template_database")
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--include-published", action="store_true")
    args = parser.parse_args()
    import boto3
    for name in cleanup(boto3.client("glue"), boto3.client("s3"), args.bucket, args.template_database,
                        args.delete, args.include_published):
        print(name)


if __name__ == "__main__":
    main()
