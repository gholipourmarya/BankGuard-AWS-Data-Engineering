output "bucket_name" {
  value = aws_s3_bucket.lake.bucket
}

output "glue_job_name" {
  value = aws_glue_job.etl.name
}

output "kms_key_arn" {
  value = aws_kms_key.lake.arn
}

output "trigger_function_name" {
  value = aws_lambda_function.start_glue_job.function_name
}

output "glue_database" {
  description = "Catalog template database. Resolve published/current.json for the query database."
  value       = aws_glue_catalog_database.bankguard.name
}

output "athena_workgroup" {
  value = aws_athena_workgroup.bankguard.name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "github_deploy_role_arn" {
  description = "Set as repository variable AWS_DEPLOY_ROLE_ARN in GitHub"
  value       = one(aws_iam_role.github_deploy[*].arn)
}

output "mwaa_webserver_url" {
  description = "Airflow UI (empty unless enable_mwaa = true)"
  value       = one(aws_mwaa_environment.airflow[*].webserver_url)
}
