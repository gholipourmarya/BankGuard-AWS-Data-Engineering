# GitHub Actions -> AWS without stored access keys (OpenID Connect).
# GitHub presents a short-lived signed token; AWS checks it against this trust policy.
# The role uploads immutable ETL releases and updates the existing Glue job.
# It may pass only that job's fixed execution role; resource creation stays with Terraform.

locals {
  github_enabled        = var.github_repository != ""
  create_oidc           = local.github_enabled && var.github_oidc_provider_arn == ""
  github_provider_arn   = var.github_oidc_provider_arn != "" ? var.github_oidc_provider_arn : one(aws_iam_openid_connect_provider.github[*].arn)
  github_subject_prefix = var.github_oidc_subject_prefix != "" ? var.github_oidc_subject_prefix : "repo:${var.github_repository}"
}

resource "aws_iam_openid_connect_provider" "github" {
  count           = local.create_oidc ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_trust" {
  count = local.github_enabled ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Limit deployment to one explicitly selected branch of this repository.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_subject_prefix}:ref:refs/heads/${var.github_deploy_branch}"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count              = local.github_enabled ? 1 : 0
  name               = "${var.project_name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_trust[0].json
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "UploadCodeArtifacts"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/releases/*"]
  }

  statement {
    sid       = "UseLakeKeyForUploads"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }

  statement {
    sid       = "ListReleases"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["releases/*"]
    }
  }

  statement {
    sid       = "PreserveEtlExecutionRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.glue.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["glue.amazonaws.com"]
    }
  }

  statement {
    sid       = "RunAndWatchEtlJob"
    actions   = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:GetJob", "glue:UpdateJob"]
    resources = [aws_glue_job.etl.arn]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = local.github_enabled ? 1 : 0
  name   = "${var.project_name}-github-deploy"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy.json
}
