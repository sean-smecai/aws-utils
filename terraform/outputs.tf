output "lambda_function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.auto_shutdown.function_name
}

output "lambda_function_arn" {
  description = "ARN of the Lambda function"
  value       = aws_lambda_function.auto_shutdown.arn
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic for notifications"
  value       = aws_sns_topic.notifications.arn
}

output "eventbridge_rule_name" {
  description = "Name of the EventBridge rule"
  value       = aws_cloudwatch_event_rule.daily_trigger.name
}

output "cloudwatch_log_group" {
  description = "CloudWatch Logs group name"
  value       = aws_cloudwatch_log_group.lambda_logs.name
}

output "schedule_expression" {
  description = "Schedule expression for the Lambda trigger"
  value       = aws_cloudwatch_event_rule.daily_trigger.schedule_expression
}