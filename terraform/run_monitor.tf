# State Change events do not document ERROR/EXPIRED. Poll them independently.
data "archive_file" "run_monitor" {
  type        = "zip"
  output_path = "${path.module}/../build/check_glue_runs.zip"
  source {
    content  = file("${path.module}/../lambda_fn/check_glue_runs.py")
    filename = "check_glue_runs.py"
  }
}

resource "aws_iam_role" "run_monitor" {
  name               = "${var.project_name}-run-monitor"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "run_monitor_logs" {
  role       = aws_iam_role.run_monitor.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "run_monitor" {
  statement {
    actions   = ["glue:GetJobRuns"]
    resources = [aws_glue_job.etl.arn]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/monitor/notified/*"]
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
}

resource "aws_iam_role_policy" "run_monitor" {
  role   = aws_iam_role.run_monitor.id
  policy = data.aws_iam_policy_document.run_monitor.json
}

resource "aws_lambda_function" "run_monitor" {
  function_name                  = "${var.project_name}-check-glue-runs"
  role                           = aws_iam_role.run_monitor.arn
  runtime                        = "python3.12"
  handler                        = "check_glue_runs.handler"
  filename                       = data.archive_file.run_monitor.output_path
  source_code_hash               = data.archive_file.run_monitor.output_base64sha256
  timeout                        = 120
  reserved_concurrent_executions = 1
  environment {
    variables = {
      BUCKET        = aws_s3_bucket.lake.bucket
      GLUE_JOB_NAME = aws_glue_job.etl.name
      ALERT_TOPIC   = aws_sns_topic.alerts.arn
    }
  }
  depends_on = [aws_iam_role_policy.run_monitor, aws_iam_role_policy_attachment.run_monitor_logs]
}

resource "aws_cloudwatch_event_rule" "check_runs" {
  name                = "${var.project_name}-check-glue-runs"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "check_runs" {
  rule = aws_cloudwatch_event_rule.check_runs.name
  arn  = aws_lambda_function.run_monitor.arn
}

resource "aws_lambda_permission" "check_runs" {
  statement_id  = "AllowScheduledRunCheck"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.run_monitor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.check_runs.arn
}

resource "aws_cloudwatch_metric_alarm" "run_monitor_errors" {
  alarm_name          = "${var.project_name}-run-monitor-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.run_monitor.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
