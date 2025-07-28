variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "ap-southeast-2"
}

variable "max_age_days" {
  description = "Maximum age in days before resources are shut down"
  type        = number
  default     = 3
}

variable "dry_run" {
  description = "Run in dry-run mode (no resources will be stopped)"
  type        = string
  default     = "false"
}

variable "notification_email" {
  description = "Email address for shutdown notifications"
  type        = string
}

variable "target_regions" {
  description = "List of AWS regions to scan for old resources"
  type        = list(string)
  default     = [
    "us-east-1",
    "us-west-2",
    "ap-southeast-2",
    "ap-southeast-4",
    "eu-west-1"
  ]
}

variable "schedule_expression" {
  description = "CloudWatch Events schedule expression"
  type        = string
  default     = "cron(0 22 * * ? *)"  # 10 PM UTC daily
}

variable "enable_slack_notifications" {
  description = "Enable Slack notifications"
  type        = bool
  default     = false
}

variable "slack_webhook_url" {
  description = "Slack webhook URL for notifications"
  type        = string
  default     = ""
  sensitive   = true
}