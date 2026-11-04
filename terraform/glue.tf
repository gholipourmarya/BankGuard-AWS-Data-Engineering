resource "aws_glue_catalog_database" "bankguard" {
  name = var.project_name
}

resource "aws_glue_job" "etl" {
  name                    = "${var.project_name}-etl"
  role_arn                = aws_iam_role.glue.arn
  glue_version            = "4.0"
  worker_type             = "G.1X"
  number_of_workers       = 2
  timeout                 = 30 # minutes
  job_run_queuing_enabled = true

  # Deploy workflow switches these two URIs together; preserve its active release.
  lifecycle {
    ignore_changes = [command[0].script_location, default_arguments["--extra-py-files"]]
  }

  execution_property {
    max_concurrent_runs = 1
  }

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_bucket.lake.bucket}/${aws_s3_object.glue_script.key}"
  }

  default_arguments = {
    "--bucket"                           = aws_s3_bucket.lake.bucket
    "--input_prefix"                     = "raw"
    "--database"                         = aws_glue_catalog_database.bankguard.name
    "--extra-py-files"                   = "s3://${aws_s3_bucket.lake.bucket}/${aws_s3_object.src_zip.key}"
    "--job-language"                     = "python"
    "--enable-metrics"                   = "true"
    "--enable-continuous-cloudwatch-log" = "true"
  }
}

locals {
  parquet_input  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
  parquet_output = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
  parquet_serde  = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
  audit_columns  = { run_id = "string", source_file = "string", ingestion_timestamp = "string" }

  curated_tables = {
    customers = merge({ customer_id = "string", name = "string", country = "string", email = "string", tel = "string" }, local.audit_columns)
    accounts  = merge({ account_id = "string", customer_id = "string", status = "string" }, local.audit_columns)
  }

  transaction_columns = merge({
    transaction_id   = "string"
    account_id       = "string"
    amount           = "decimal(18,2)"
    currency         = "string"
    transaction_type = "string"
    source_system    = "string"
  }, local.audit_columns)
}

resource "aws_glue_catalog_table" "curated" {
  for_each      = local.curated_tables
  database_name = aws_glue_catalog_database.bankguard.name
  name          = "curated_${each.key}"
  table_type    = "EXTERNAL_TABLE"
  parameters    = { classification = "parquet" }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.lake.bucket}/curated/${each.key}/"
    input_format  = local.parquet_input
    output_format = local.parquet_output

    ser_de_info {
      serialization_library = local.parquet_serde
    }

    dynamic "columns" {
      for_each = each.value
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

# The Glue job registers actual dates, with no artificial projection date bounds.
resource "aws_glue_catalog_table" "curated_transactions" {
  database_name = aws_glue_catalog_database.bankguard.name
  name          = "curated_transactions"
  table_type    = "EXTERNAL_TABLE"

  parameters = { classification = "parquet" }

  partition_keys {
    name = "transaction_date"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.lake.bucket}/curated/transactions/"
    input_format  = local.parquet_input
    output_format = local.parquet_output

    ser_de_info {
      serialization_library = local.parquet_serde
    }

    dynamic "columns" {
      for_each = local.transaction_columns
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

resource "aws_glue_catalog_table" "rejected_transactions" {
  database_name = aws_glue_catalog_database.bankguard.name
  name          = "rejected_transactions"
  table_type    = "EXTERNAL_TABLE"
  parameters    = { classification = "json" }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.lake.bucket}/rejected/transactions/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.IgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    dynamic "columns" {
      for_each = {
        transaction_id      = "string"
        account_id          = "string"
        amount              = "string"
        currency            = "string"
        transaction_type    = "string"
        transaction_date    = "string"
        source_system       = "string"
        error_reasons       = "array<string>"
        line                = "bigint"
        run_id              = "string"
        source_file         = "string"
        ingestion_timestamp = "string"
      }
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

# Used only for the CSV-versus-Parquet scan comparison (docs/cost-estimate.md).
# Glue exports the same accepted transactions as CSV here; never copy raw input here.
resource "aws_glue_catalog_table" "comparison_transactions_csv" {
  database_name = aws_glue_catalog_database.bankguard.name
  name          = "comparison_transactions_csv"
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification           = "csv"
    "skip.header.line.count" = "1"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.lake.bucket}/comparison/csv/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.serde2.OpenCSVSerde"
      parameters            = { "separatorChar" = ",", "quoteChar" = "\"", "escapeChar" = "\\" }
    }

    # An ordered list, not a map: CSV columns are matched by position and Terraform
    # iterates maps in alphabetical order.
    dynamic "columns" {
      for_each = [
        { name = "transaction_id", type = "string" },
        { name = "account_id", type = "string" },
        { name = "amount", type = "string" },
        { name = "currency", type = "string" },
        { name = "transaction_type", type = "string" },
        { name = "transaction_date", type = "string" },
        { name = "source_system", type = "string" },
      ]
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}
