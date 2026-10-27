"""AWS Glue 4.0 (Spark) job: validate raw CSVs, write curated Parquet and rejected JSON.

NOT YET RUN ON AWS. Written against the Glue 4.0 API and the locally tested
modules ``validate`` and ``transform`` (shipped via --extra-py-files src.zip).

Design notes
- Each run reprocesses the complete CSV files in its input prefix (snapshot semantics).
- CSV parsing is shared with the local runner and preserves source line numbers.
- Data volume is small (tens of thousands of rows), so rows are collected to the
  driver and validated by the shared pure-Python functions. For much larger
  volumes the rules would need to be expressed as Spark transformations.
- Each run writes its own snapshot and catalog database. Only a complete run
  changes published/current.json; readers keep using the prior snapshot on failure.
- CSV and Parquet comparison outputs contain exactly the same accepted transactions.
"""
import io
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql.types import DecimalType, StringType, StructField, StructType

import snapshot  # from src.zip
import transform  # from src.zip (imports validate itself)

# Glue resolves its reserved JOB_RUN_ID automatically; requesting it again
# would register the same argparse option twice.
args = getResolvedOptions(sys.argv, ["JOB_NAME", "bucket", "input_prefix", "database"])
BUCKET = args["bucket"]
RUN_ID = args["JOB_RUN_ID"]
OUTPUT_PREFIX = f"snapshots/{RUN_ID}"
INPUT_PREFIX = args["input_prefix"].rstrip("/")
s3 = boto3.client("s3")

sc = SparkContext.getOrCreate()
glue_context = GlueContext(sc)
spark = glue_context.spark_session
spark.conf.set("spark.sql.sources.partitionOverwriteMode", "static")
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

AUDIT = ["run_id", "source_file", "ingestion_timestamp"]


def log(event, **fields):
    """One JSON line per event; picked up by CloudWatch Logs."""
    print(json.dumps({"event": event, **fields}, sort_keys=True))


def read_raw_csv(name):
    fields = {"customers.csv": transform.validate.CUSTOMER_FIELDS,
              "accounts.csv": transform.validate.ACCOUNT_FIELDS,
              "transactions.csv": transform.validate.TRANSACTION_FIELDS}
    with s3.get_object(Bucket=BUCKET, Key=f"{INPUT_PREFIX}/{name}")["Body"] as body:
        with io.TextIOWrapper(body, encoding="utf-8-sig", newline="") as stream:
            return transform.parse_csv_rows(stream, fields[name])


def write_parquet(rows, fields, name, partition_by=None):
    schema = StructType([
        StructField(f, DecimalType(18, 2) if f == "amount" else StringType(), True)
        for f in fields + AUDIT
    ])
    data = [tuple(Decimal(r[f].strip()) if f == "amount" else r.get(f) for f in fields + AUDIT) for r in rows]
    df = spark.createDataFrame(data, schema)
    writer = df.write.mode("overwrite").option("partitionOverwriteMode", "static")
    if partition_by:
        writer = writer.partitionBy(partition_by)
    writer.parquet(f"s3://{BUCKET}/{OUTPUT_PREFIX}/curated/{name}/")
    if name == "transactions":
        # Export the very same rows, with the same decimal precision, for a fair comparison.
        df.select(*fields).write.mode("overwrite").option("header", "true").csv(
            f"s3://{BUCKET}/{OUTPUT_PREFIX}/comparison/csv/")


def write_rejected(rows, name):
    path = f"s3://{BUCKET}/{OUTPUT_PREFIX}/rejected/{name}/"
    if not rows:
        log("no_rejected_rows", dataset=name)
        # An explicit schema lets Spark overwrite previous rejects even when
        # there are no new rows from which to infer a schema.
        spark.createDataFrame([], "error_reasons array<string>").write.mode("overwrite").json(path)
        return
    lines = [json.dumps({**{k: v for k, v in r.items() if k not in {"_line", "_csv_errors"}}, "line": r["_line"]}) for r in rows]
    spark.read.json(sc.parallelize(lines)).coalesce(1).write.mode("overwrite").json(path)


def write_outputs(result):
    write_parquet(result["customers"][0], ["customer_id", "name", "country"], "customers")
    write_parquet(result["accounts"][0], ["account_id", "customer_id", "status"], "accounts")
    write_parquet(result["transactions"][0],
                  ["transaction_id", "account_id", "amount", "currency", "transaction_type", "transaction_date",
                   "source_system"],
                  "transactions", partition_by="transaction_date")
    for name in ("customers", "accounts", "transactions"):
        write_rejected(result[name][1], name)


def put_metrics(metrics):
    try:
        boto3.client("cloudwatch").put_metric_data(
            Namespace="BankGuard",
            MetricData=[
                {"MetricName": "InputCount", "Value": metrics["input_count"], "Unit": "Count"},
                {"MetricName": "AcceptedCount", "Value": metrics["accepted_count"], "Unit": "Count"},
                {"MetricName": "RejectedCount", "Value": metrics["rejected_count"], "Unit": "Count"},
                {"MetricName": "PassRatePercent", "Value": metrics["pass_rate_percent"], "Unit": "Percent"},
            ],
        )
    except Exception as exc:  # metrics must never break the ETL itself
        log("put_metric_data_failed", error=str(exc))


try:
    started = datetime.now(timezone.utc)
    run_id = RUN_ID
    catalog = boto3.client("glue")
    log("job_started", run_id=run_id, bucket=BUCKET)

    result = transform.run_pipeline(
        read_raw_csv("customers.csv"), read_raw_csv("accounts.csv"), read_raw_csv("transactions.csv"),
        run_id, started.isoformat(),
    )
    for name in ("customers", "accounts", "transactions"):
        for rows in result[name]:
            for row in rows:
                row["source_file"] = f"{INPUT_PREFIX}/{name}.csv"
    metrics = result["metrics"]
    if not metrics["counts_consistent"]:
        raise RuntimeError(f"input_count != accepted + rejected: {metrics}")

    snapshot.complete_run(catalog, s3, BUCKET, args["database"], run_id, INPUT_PREFIX, result,
                          lambda: write_outputs(result), job.commit)
    put_metrics(metrics)
    log("job_succeeded", run_id=run_id, seconds=round((datetime.now(timezone.utc) - started).total_seconds(), 1), **metrics)
except Exception as exc:
    log("job_failed", error=str(exc))
    raise
