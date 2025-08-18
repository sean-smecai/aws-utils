variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "ap-southeast-4"
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
  description = "Slack webhook URL for notifications (optional)"
  type        = string
  default     = ""
}

variable "log_level" {
  description = "Logging level: minimal (summary only) or verbose (detailed)"
  type        = string
  default     = "minimal"
  validation {
    condition     = contains(["minimal", "verbose"], var.log_level)
    error_message = "Log level must be either 'minimal' or 'verbose'."
  }
}

variable "s3_bucket_exclusions" {
  description = "Comma-separated list of S3 bucket name patterns to exclude from cleanup"
  type        = string
  default     = "terraform-state,cloudtrail,logs,backup"
}

variable "elb_name_exclusions" {
  description = "Comma-separated list of load balancer name patterns to exclude from cleanup"
  type        = string
  default     = "production,critical"
}

variable "es_domain_exclusions" {
  description = "Comma-separated list of Elasticsearch domain name patterns to exclude from cleanup"
  type        = string
  default     = "production,logs"
}

variable "protection_enabled" {
  description = "Enable comprehensive protection system for critical resources"
  type        = bool
  default     = true
}

variable "config_source" {
  description = "Source for protection configuration: env, s3, or default"
  type        = string
  default     = "env"
  validation {
    condition     = contains(["env", "s3", "default"], var.config_source)
    error_message = "Config source must be 'env', 's3', or 'default'."
  }
}

variable "config_s3_bucket" {
  description = "S3 bucket containing protection configuration (if config_source is s3)"
  type        = string
  default     = ""
}

variable "config_s3_key" {
  description = "S3 key for protection configuration file"
  type        = string
  default     = "protection-config.json"
}

variable "override_enabled" {
  description = "Enable emergency override capability for protected resources"
  type        = bool
  default     = false
}