# Amazon MWAA (managed Airflow), opt-in: terraform apply -var="enable_mwaa=true"
#
# COST WARNING: an MWAA environment is billed per hour for as long as it exists, and the NAT
# gateway in this file is billed per hour as well. Apply it to produce evidence (screenshot of
# a successful DAG run), then run `terraform destroy` or re-apply with enable_mwaa=false.
# Deployed successfully on 2026-10-28; see docs/evidence/2026-10-28/airflow-environment.json.

locals {
  mwaa_name = "${var.project_name}-airflow"
}

data "aws_availability_zones" "available" {
  state = "available"
}

# --- Network: private subnets for MWAA, NAT gateway for outbound access (pip, AWS APIs) ---

resource "aws_vpc" "mwaa" {
  count                = var.enable_mwaa ? 1 : 0
  cidr_block           = "10.192.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project_name}-mwaa" }
}

resource "aws_internet_gateway" "mwaa" {
  count  = var.enable_mwaa ? 1 : 0
  vpc_id = aws_vpc.mwaa[0].id
}

resource "aws_subnet" "public" {
  count             = var.enable_mwaa ? 2 : 0
  vpc_id            = aws_vpc.mwaa[0].id
  cidr_block        = cidrsubnet(aws_vpc.mwaa[0].cidr_block, 8, 10 + count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${var.project_name}-mwaa-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = var.enable_mwaa ? 2 : 0
  vpc_id            = aws_vpc.mwaa[0].id
  cidr_block        = cidrsubnet(aws_vpc.mwaa[0].cidr_block, 8, 20 + count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${var.project_name}-mwaa-private-${count.index}" }
}

resource "aws_eip" "nat" {
  count  = var.enable_mwaa ? 1 : 0
  domain = "vpc"
}

resource "aws_nat_gateway" "mwaa" {
  count         = var.enable_mwaa ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  depends_on = [aws_internet_gateway.mwaa]
}

resource "aws_route_table" "public" {
  count  = var.enable_mwaa ? 1 : 0
  vpc_id = aws_vpc.mwaa[0].id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.mwaa[0].id
  }
}

resource "aws_route_table" "private" {
  count  = var.enable_mwaa ? 1 : 0
  vpc_id = aws_vpc.mwaa[0].id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.mwaa[0].id
  }
}

resource "aws_route_table_association" "public" {
  count          = var.enable_mwaa ? 2 : 0
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_route_table_association" "private" {
  count          = var.enable_mwaa ? 2 : 0
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[0].id
}

# Security group: MWAA components talk to each other (self-reference); nothing else may connect in.
resource "aws_security_group" "mwaa" {
  count       = var.enable_mwaa ? 1 : 0
  name        = "${var.project_name}-mwaa"
  description = "MWAA environment: allow traffic only within the group"
  vpc_id      = aws_vpc.mwaa[0].id

  ingress {
    description = "All traffic from members of this group"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "Outbound access (AWS APIs, package index) through the NAT gateway"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- DAG bucket (MWAA requires versioning and blocked public access) ---

resource "aws_s3_bucket" "mwaa" {
  count         = var.enable_mwaa ? 1 : 0
  bucket        = "${var.project_name}-mwaa-${data.aws_caller_identity.current.account_id}-${var.region}"
  force_destroy = true # demo setting, see README
}

resource "aws_s3_bucket_public_access_block" "mwaa" {
  count                   = var.enable_mwaa ? 1 : 0
  bucket                  = aws_s3_bucket.mwaa[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "mwaa" {
  count  = var.enable_mwaa ? 1 : 0
  bucket = aws_s3_bucket.mwaa[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mwaa" {
  count  = var.enable_mwaa ? 1 : 0
  bucket = aws_s3_bucket.mwaa[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_object" "dag" {
  count  = var.enable_mwaa ? 1 : 0
  bucket = aws_s3_bucket.mwaa[0].id
  key    = "dags/bankguard_pipeline.py"
  source = "${path.module}/../airflow/dags/bankguard_pipeline.py"
  etag   = filemd5("${path.module}/../airflow/dags/bankguard_pipeline.py")

  depends_on = [aws_s3_bucket_versioning.mwaa]
}

# Generated configuration: the DAG imports these names, so no Airflow Variables need to be set by hand.
resource "aws_s3_object" "dag_config" {
  count  = var.enable_mwaa ? 1 : 0
  bucket = aws_s3_bucket.mwaa[0].id
  key    = "dags/bankguard_config.py"

  content = <<-EOT
    BUCKET = "${aws_s3_bucket.lake.bucket}"
    GLUE_JOB = "${aws_glue_job.etl.name}"
    WORKGROUP = "${aws_athena_workgroup.bankguard.name}"
    DATABASE = "${aws_glue_catalog_database.bankguard.name}"
  EOT

  depends_on = [aws_s3_bucket_versioning.mwaa]
}

# Use the same validation implementation in local scripts, Glue and Airflow.
resource "aws_s3_object" "dag_validation" {
  count  = var.enable_mwaa ? 1 : 0
  bucket = aws_s3_bucket.mwaa[0].id
  key    = "dags/bankguard_validation.py"
  source = "${path.module}/../src/validate.py"
  etag   = filemd5("${path.module}/../src/validate.py")

  depends_on = [aws_s3_bucket_versioning.mwaa]
}

# --- Execution role: what the Airflow workers may do ---

data "aws_iam_policy_document" "mwaa_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["airflow.amazonaws.com", "airflow-env.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "mwaa" {
  count              = var.enable_mwaa ? 1 : 0
  name               = "${var.project_name}-mwaa-execution"
  assume_role_policy = data.aws_iam_policy_document.mwaa_assume.json
}

data "aws_iam_policy_document" "mwaa" {
  # Permissions MWAA itself needs (publish metrics, read DAGs, logs, internal queues)
  statement {
    sid       = "PublishMetrics"
    actions   = ["airflow:PublishMetrics"]
    resources = ["arn:aws:airflow:${var.region}:${data.aws_caller_identity.current.account_id}:environment/${local.mwaa_name}"]
  }

  statement {
    sid       = "ReadDags"
    actions   = ["s3:GetObject*", "s3:GetBucket*", "s3:List*"]
    resources = var.enable_mwaa ? [aws_s3_bucket.mwaa[0].arn, "${aws_s3_bucket.mwaa[0].arn}/*"] : ["*"]
  }

  statement {
    sid       = "PublicAccessBlockCheck"
    actions   = ["s3:GetAccountPublicAccessBlock"]
    resources = ["*"]
  }

  statement {
    sid = "AirflowLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:CreateLogGroup",
      "logs:PutLogEvents",
      "logs:GetLogEvents",
      "logs:GetLogRecord",
      "logs:GetLogGroupFields",
      "logs:GetQueryResults",
    ]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:airflow-${local.mwaa_name}-*"]
  }

  statement {
    sid       = "DescribeLogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }

  statement {
    sid       = "AirflowMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }

  statement {
    sid = "CeleryQueues"
    actions = [
      "sqs:ChangeMessageVisibility",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ReceiveMessage",
      "sqs:SendMessage",
    ]
    resources = ["arn:aws:sqs:${var.region}:*:airflow-celery-*"]
  }

  statement {
    sid           = "QueueEncryption"
    actions       = ["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKey*", "kms:Encrypt"]
    not_resources = ["arn:aws:kms:*:${data.aws_caller_identity.current.account_id}:key/*"]

    condition {
      test     = "StringLike"
      variable = "kms:ViaService"
      values   = ["sqs.${var.region}.amazonaws.com"]
    }
  }

  # Permissions the BankGuard DAG needs
  statement {
    sid       = "RunEtlJob"
    actions   = ["glue:GetJob", "glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns"]
    resources = [aws_glue_job.etl.arn]
  }

  statement {
    sid       = "ReadLakeAndWriteQueryResults"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetObject", "s3:PutObject"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
  }

  statement {
    sid       = "UseLakeKey"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }

  statement {
    sid = "RunAthenaQueries"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
    ]
    resources = [aws_athena_workgroup.bankguard.arn]
  }

  statement {
    sid       = "ReadCatalog"
    actions   = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartition", "glue:GetPartitions"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "mwaa" {
  count  = var.enable_mwaa ? 1 : 0
  name   = "${var.project_name}-mwaa-execution"
  role   = aws_iam_role.mwaa[0].id
  policy = data.aws_iam_policy_document.mwaa.json
}

# --- The environment ---

resource "aws_mwaa_environment" "airflow" {
  count                 = var.enable_mwaa ? 1 : 0
  name                  = local.mwaa_name
  airflow_version       = var.mwaa_airflow_version != "" ? var.mwaa_airflow_version : null
  environment_class     = var.mwaa_environment_class
  execution_role_arn    = aws_iam_role.mwaa[0].arn
  source_bucket_arn     = aws_s3_bucket.mwaa[0].arn
  dag_s3_path           = "dags"
  min_workers           = 1
  max_workers           = 1
  webserver_access_mode = "PUBLIC_ONLY" # login with IAM; use PRIVATE_ONLY if a VPN or bastion is available

  network_configuration {
    security_group_ids = [aws_security_group.mwaa[0].id]
    subnet_ids         = aws_subnet.private[*].id
  }

  logging_configuration {
    dag_processing_logs {
      enabled   = true
      log_level = "WARNING"
    }

    scheduler_logs {
      enabled   = true
      log_level = "INFO"
    }

    task_logs {
      enabled   = true
      log_level = "INFO"
    }

    webserver_logs {
      enabled   = true
      log_level = "WARNING"
    }

    worker_logs {
      enabled   = true
      log_level = "INFO"
    }
  }

  depends_on = [
    aws_s3_object.dag,
    aws_s3_object.dag_config,
    aws_s3_object.dag_validation,
    aws_s3_bucket_public_access_block.mwaa,
    aws_s3_bucket_server_side_encryption_configuration.mwaa,
    aws_iam_role_policy.mwaa,
    aws_nat_gateway.mwaa,
    aws_route_table_association.private,
  ]
}
