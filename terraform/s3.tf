# One bucket: raw/, snapshots/, published/, releases/, runs/, monitor/, athena-results/
resource "aws_s3_bucket" "lake" {
  bucket        = local.bucket_name
  force_destroy = true # lets `terraform destroy` remove objects and versions (demo project)
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.lake.arn
    }

    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id

  versioning_configuration {
    status = "Enabled"
  }
}

locals {
  initial_release_id = sha256(join("\n", [
    filesha256("${path.module}/../src/validate.py"),
    filesha256("${path.module}/../src/transform.py"),
    filesha256("${path.module}/../src/snapshot.py"),
    filesha256("${path.module}/../glue/banking_etl.py"),
  ]))
}

# Validation code shipped to Glue as a Python library (modules at the zip root).
data "archive_file" "src" {
  type        = "zip"
  output_path = "${path.module}/../build/src.zip"

  source {
    content  = file("${path.module}/../src/validate.py")
    filename = "validate.py"
  }

  source {
    content  = file("${path.module}/../src/transform.py")
    filename = "transform.py"
  }

  source {
    content  = file("${path.module}/../src/snapshot.py")
    filename = "snapshot.py"
  }
}

resource "aws_s3_object" "src_zip" {
  bucket = aws_s3_bucket.lake.id
  key    = "releases/${local.initial_release_id}/src.zip"
  source = data.archive_file.src.output_path
  etag   = data.archive_file.src.output_md5

  # Bucket creation alone does not establish its encryption/versioning defaults.
  depends_on = [aws_s3_bucket_server_side_encryption_configuration.lake, aws_s3_bucket_versioning.lake]

  # Terraform uploads the first version; later code updates come from GitHub Actions.
  lifecycle {
    ignore_changes = [key, source, etag]
  }
}

resource "aws_s3_object" "glue_script" {
  bucket = aws_s3_bucket.lake.id
  key    = "releases/${local.initial_release_id}/banking_etl.py"
  source = "${path.module}/../glue/banking_etl.py"
  etag   = filemd5("${path.module}/../glue/banking_etl.py")

  depends_on = [aws_s3_bucket_server_side_encryption_configuration.lake, aws_s3_bucket_versioning.lake]

  lifecycle {
    ignore_changes = [key, source, etag]
  }
}

resource "aws_athena_workgroup" "bankguard" {
  name          = var.project_name
  force_destroy = true

  configuration {
    enforce_workgroup_configuration = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.bucket}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = aws_kms_key.lake.arn
      }
    }
  }
}
