variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-central-1"
}

variable "project_name" {
  description = "Prefix for resource names"
  type        = string
  default     = "bankguard"
}

variable "alert_email" {
  description = "E-mail address for failure alarms and budget notifications (empty = no subscription)"
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD"
  type        = string
  default     = "10"
}

variable "pass_rate_alarm_threshold" {
  description = "Alarm if the data-quality pass rate of a run falls below this percentage"
  type        = number
  default     = 90
}

variable "github_repository" {
  description = "GitHub repository allowed to deploy, as owner/name (empty = no OIDC role)"
  type        = string
  default     = ""
}

variable "github_oidc_provider_arn" {
  description = "ARN of an existing GitHub OIDC provider in this account (empty = create one)"
  type        = string
  default     = ""
}

variable "github_deploy_branch" {
  description = "Only this branch of github_repository may assume the deployment role"
  type        = string
  default     = "main"
}

variable "github_oidc_subject_prefix" {
  description = "Exact repo:OWNER@ID/REPO@ID prefix returned by GitHub OIDC settings (empty = legacy name format)"
  type        = string
  default     = ""
}

variable "enable_mwaa" {
  description = "Create the Amazon MWAA environment with its VPC and NAT gateway. Billed hourly while it exists: apply for evidence, then destroy"
  type        = bool
  default     = false
}

variable "mwaa_environment_class" {
  description = "MWAA environment class (mw1.micro is the smallest)"
  type        = string
  default     = "mw1.micro"
}

variable "mwaa_airflow_version" {
  description = "Airflow version for MWAA (empty = latest supported). Check the versions MWAA supports before pinning one"
  type        = string
  default     = ""
}
