# --- Alerts: SNS topic, e-mail subscription, EventBridge rule, CloudWatch alarm ---

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email # the recipient must confirm the subscription by e-mail
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "AllowEventBridgeRule"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.glue_failed.arn]
    }
  }

  statement {
    sid       = "AllowCloudWatchAlarms"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

# Glue job ended in a failure state -> e-mail
resource "aws_cloudwatch_event_rule" "glue_failed" {
  name        = "${var.project_name}-glue-job-failed"
  description = "Glue job of ${var.project_name} failed, timed out or errored"

  event_pattern = jsonencode({
    source      = ["aws.glue"]
    detail-type = ["Glue Job State Change"]
    detail = {
      jobName = [aws_glue_job.etl.name]
      state   = ["FAILED", "TIMEOUT", "STOPPED"]
    }
  })
}

resource "aws_cloudwatch_event_target" "glue_failed_to_sns" {
  rule = aws_cloudwatch_event_rule.glue_failed.name
  arn  = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths = {
      job   = "$.detail.jobName"
      state = "$.detail.state"
      run   = "$.detail.jobRunId"
      msg   = "$.detail.message"
    }
    input_template = "\"BankGuard Glue job <job> is <state> (run <run>): <msg>\""
  }
}

# Data-quality drop: the job publishes PassRatePercent after every successful run
resource "aws_cloudwatch_metric_alarm" "pass_rate_low" {
  alarm_name          = "${var.project_name}-pass-rate-low"
  alarm_description   = "Data-quality pass rate below ${var.pass_rate_alarm_threshold} percent"
  namespace           = "BankGuard"
  metric_name         = "PassRatePercent"
  dimensions          = { JobName = aws_glue_job.etl.name, Dataset = "overall" }
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.pass_rate_alarm_threshold
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "dataset_pass_rate_low" {
  for_each            = toset(["customers", "accounts", "transactions"])
  alarm_name          = "${var.project_name}-${each.key}-pass-rate-low"
  alarm_description   = "${each.key} pass rate below ${var.pass_rate_alarm_threshold} percent"
  namespace           = "BankGuard"
  metric_name         = "PassRatePercent"
  dimensions          = { JobName = aws_glue_job.etl.name, Dataset = each.key }
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.pass_rate_alarm_threshold
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_dashboard" "bankguard" {
  dashboard_name = var.project_name

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Records per hour"
          region = var.region
          stat   = "Sum"
          period = 3600
          metrics = [
            ["BankGuard", "InputCount", "JobName", aws_glue_job.etl.name, "Dataset", "overall"],
            ["BankGuard", "AcceptedCount", "JobName", aws_glue_job.etl.name, "Dataset", "overall"],
            ["BankGuard", "RejectedCount", "JobName", aws_glue_job.etl.name, "Dataset", "overall"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Data-quality pass rate (%)"
          region = var.region
          stat   = "Minimum"
          period = 3600
          metrics = [
            ["BankGuard", "PassRatePercent", "JobName", aws_glue_job.etl.name, "Dataset", "overall"],
            ["BankGuard", "PassRatePercent", "JobName", aws_glue_job.etl.name, "Dataset", "customers"],
            ["BankGuard", "PassRatePercent", "JobName", aws_glue_job.etl.name, "Dataset", "accounts"],
            ["BankGuard", "PassRatePercent", "JobName", aws_glue_job.etl.name, "Dataset", "transactions"],
          ]
        }
      },
    ]
  })
}

# --- Cost control ---

resource "aws_budgets_budget" "monthly" {
  count        = var.alert_email != "" ? 1 : 0
  name         = "${var.project_name}-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# Lifecycle for raw files, legacy rejected outputs, temporary results and old versions.
# Snapshot objects stay until explicit cleanup/destroy; noncurrent versions expire after 30 days.
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  depends_on = [aws_s3_bucket_versioning.lake]

  rule {
    id     = "abort-incomplete-uploads-and-old-versions"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "raw-to-infrequent-access"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }

  rule {
    id     = "rejected-to-infrequent-access"
    status = "Enabled"

    filter {
      prefix = "rejected/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }

  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }
}
