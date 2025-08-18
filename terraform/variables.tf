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

variable "scan_all_regions" {
  description = "Scan all AWS regions instead of specific target regions"
  type        = bool
  default     = false
}

variable "target_regions" {
  description = "List of AWS regions to scan for old resources (used if scan_all_regions is false)"
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

variable "enable_workspaces_monitoring" {
  description = "Enable monitoring and shutdown of Amazon WorkSpaces"
  type        = bool
  default     = false
}

variable "always_send_notification" {
  description = "Send email notification even when no resources are found"
  type        = bool
  default     = true
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

variable "cost_optimization_enabled" {
  description = "Enable cost-based scheduling and prioritization"
  type        = bool
  default     = true
}

variable "cost_threshold_high" {
  description = "Monthly cost threshold (USD) to consider resource as high-value"
  type        = number
  default     = 100
}

variable "cost_threshold_require_approval" {
  description = "Monthly cost threshold (USD) requiring approval before deletion"
  type        = number
  default     = 500
}

variable "business_hours_start" {
  description = "Start of business hours (24-hour format) in UTC"
  type        = number
  default     = 14  # 9 AM EST
  validation {
    condition     = var.business_hours_start >= 0 && var.business_hours_start <= 23
    error_message = "Business hours start must be between 0 and 23."
  }
}

variable "business_hours_end" {
  description = "End of business hours (24-hour format) in UTC"
  type        = number
  default     = 22  # 5 PM EST
  validation {
    condition     = var.business_hours_end >= 0 && var.business_hours_end <= 23
    error_message = "Business hours end must be between 0 and 23."
  }
}

variable "cost_optimized_cleanup_windows" {
  description = "Comma-separated list of hour ranges for cost-optimized cleanup (e.g., '2-6,22-23')"
  type        = string
  default     = "2-6,22-23"  # 2-6 AM and 10-11 PM UTC
}

variable "enable_cost_analysis" {
  description = "Include cost analysis in cleanup operations and notifications"
  type        = bool
  default     = true
}