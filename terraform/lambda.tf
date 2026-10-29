# S3 upload -> Lambda -> Glue job. The Lambda is only a trigger; validation stays in Glue.

data "archive_file" "start_glue_job" {
  type        = "zip"
  output_path = "${path.module}/../build/start_glue_job.zip"

  source {
    content  = file("${path.module}/../lambda_fn/start_glue_job.py")
    filename = "start_glue_job.py"
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "trigger" {
  name               = "${var.project_name}-trigger-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "trigger_logs" {
  role       = aws_iam_role.trigger.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "trigger" {
  statement {
    sid       = "StartTheEtlJob"
    actions   = ["glue:StartJobRun", "glue:GetJobRuns"]
    resources = [aws_glue_job.etl.arn]
  }

  statement {
    sid       = "NotifyFailedSubmissions"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }

  statement {
    sid       = "CheckRawFilesExist"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/raw/*"]
  }

  statement {
    sid       = "ClaimDataPackages"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/submissions/*"]
  }

  statement {
    sid       = "ReadEncryptedObjectMetadata"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }
}

resource "aws_iam_role_policy" "trigger" {
  name   = "${var.project_name}-trigger"
  role   = aws_iam_role.trigger.id
  policy = data.aws_iam_policy_document.trigger.json
}

resource "aws_lambda_function" "start_glue_job" {
  function_name    = "${var.project_name}-start-glue-job"
  role             = aws_iam_role.trigger.arn
  runtime          = "python3.12"
  handler          = "start_glue_job.handler"
  filename         = data.archive_file.start_glue_job.output_path
  source_code_hash = data.archive_file.start_glue_job.output_base64sha256
  timeout          = 60
  # Use the shared account pool; small account quotas can reject reservations.
  reserved_concurrent_executions = -1

  environment {
    variables = {
      BUCKET        = aws_s3_bucket.lake.bucket
      GLUE_JOB_NAME = aws_glue_job.etl.name
    }
  }
}

resource "aws_lambda_function_event_invoke_config" "trigger" {
  function_name                = aws_lambda_function.start_glue_job.function_name
  maximum_event_age_in_seconds = 21600
  maximum_retry_attempts       = 2

  destination_config {
    on_failure {
      destination = aws_sns_topic.alerts.arn
    }
  }
  depends_on = [aws_iam_role_policy.trigger]
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.start_glue_job.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.lake.arn
}

resource "aws_s3_bucket_notification" "raw_upload" {
  bucket = aws_s3_bucket.lake.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.start_glue_job.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/batches/"
    filter_suffix       = "/ready.json"
  }

  depends_on = [aws_lambda_permission.allow_s3]
}
