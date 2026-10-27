"""Build an isolated catalog and publish one complete snapshot with a single S3 PUT.

Callers write every output and commit the Glue job before publishing. Readers
resolve published/current.json once and use that immutable database throughout.
"""
import copy
import json
import re

TABLE_PATHS = {
    "curated_customers": "curated/customers/",
    "curated_accounts": "curated/accounts/",
    "curated_transactions": "curated/transactions/",
    "rejected_transactions": "rejected/transactions/",
    "comparison_transactions_csv": "comparison/csv/",
}


def snapshot_database(template_database, run_id):
    if not re.fullmatch(r"jr_[a-zA-Z0-9_]+", run_id):
        raise ValueError("Expected an AWS Glue run ID")
    name = f"{template_database}_{run_id}"
    if len(name) > 255:
        raise ValueError("Snapshot database name exceeds 255 characters")
    return name


def create_catalog(glue, template_database, bucket, run_id):
    database = snapshot_database(template_database, run_id)
    # Deliberately fail if this run ID already exists. Never rewrite a published run.
    glue.create_database(DatabaseInput={"Name": database,
                                       "Description": f"BankGuard immutable snapshot {run_id}"})
    for name, path in TABLE_PATHS.items():
        template = glue.get_table(DatabaseName=template_database, Name=name)["Table"]
        storage = copy.deepcopy(template["StorageDescriptor"])
        storage["Location"] = f"s3://{bucket}/snapshots/{run_id}/{path}"
        glue.create_table(DatabaseName=database, TableInput={
            "Name": name, "TableType": "EXTERNAL_TABLE", "StorageDescriptor": storage,
            "Parameters": dict(template.get("Parameters", {})),
            "PartitionKeys": copy.deepcopy(template.get("PartitionKeys", [])),
        })
    return database


def register_partitions(glue, database, bucket, run_id, rows):
    dates = sorted({row["transaction_date"] for row in rows})
    if not dates:
        return
    descriptor = glue.get_table(DatabaseName=database, Name="curated_transactions")["Table"]["StorageDescriptor"]
    for offset in range(0, len(dates), 100):
        partitions = []
        for day in dates[offset:offset + 100]:
            storage = copy.deepcopy(descriptor)
            storage["Location"] = f"s3://{bucket}/snapshots/{run_id}/curated/transactions/transaction_date={day}/"
            partitions.append({"Values": [day], "StorageDescriptor": storage})
        response = glue.batch_create_partition(DatabaseName=database, TableName="curated_transactions",
                                               PartitionInputList=partitions)
        if response.get("Errors"):
            raise RuntimeError(f"Could not register snapshot partitions: {response['Errors']}")


def publish(s3, bucket, run_id, database, input_prefix, metrics):
    if not metrics.get("counts_consistent"):
        raise ValueError("Cannot publish inconsistent counts")
    if metrics["input_count"] != metrics["accepted_count"] + metrics["rejected_count"]:
        raise ValueError("Cannot publish inconsistent counts")
    receipt = {**metrics, "run_id": run_id, "database": database, "input_prefix": input_prefix,
               "data_prefix": f"snapshots/{run_id}/"}
    body = json.dumps(receipt, sort_keys=True).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=f"runs/{run_id}/metrics.json", Body=body, ContentType="application/json")
    # The only publication step. A failed run never overwrites the previous datasets.
    s3.put_object(Bucket=bucket, Key="published/current.json", Body=body, ContentType="application/json")
    return receipt


def complete_run(glue, s3, bucket, template_database, run_id, input_prefix, result, write_outputs, commit):
    """Keep the old publication intact if any preparation or write step fails."""
    metrics = result["metrics"]
    if not metrics.get("counts_consistent"):
        raise ValueError("Cannot publish inconsistent counts")
    database = create_catalog(glue, template_database, bucket, run_id)
    write_outputs()
    register_partitions(glue, database, bucket, run_id, result["transactions"][0])
    commit()
    return publish(s3, bucket, run_id, database, input_prefix, metrics)
