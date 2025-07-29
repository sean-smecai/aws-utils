# AWS Auto-Shutdown Terraform Deployment

This Terraform configuration deploys an AWS Lambda function that automatically shuts down resources older than a specified number of days.

## Features

- **Lambda Function**: Runs daily to check and stop old resources
- **EventBridge Schedule**: Configurable schedule (default: 10 PM UTC daily)
- **SNS Notifications**: Email alerts when resources are shut down
- **Multi-Region Support**: Scans multiple AWS regions
- **Dry-Run Mode**: Test without actually stopping resources

## Resources Managed

The Lambda function will stop/delete:
- EC2 instances running > 3 days
- RDS database instances > 3 days
- ECS services (scales to 0 tasks)
- NAT Gateways > 3 days
- Unassociated Elastic IPs

## Prerequisites

1. AWS CLI configured with appropriate credentials
2. Terraform >= 1.0
3. IAM permissions to create Lambda, IAM roles, EventBridge rules, and SNS topics

## Quick Start

1. **Clone and navigate to the directory**:
   ```bash
   cd /Users/sean/Projects/aws-utils/terraform
   ```

2. **Copy and configure variables**:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   # Edit terraform.tfvars with your settings
   ```

3. **Initialize Terraform**:
   ```bash
   terraform init
   ```

4. **Review the plan**:
   ```bash
   terraform plan
   ```

5. **Apply the configuration**:
   ```bash
   terraform apply
   ```

6. **Confirm SNS subscription**:
   Check your email and confirm the SNS subscription to receive notifications.

## Configuration

### Required Variables

- `notification_email`: Email address for shutdown notifications

### Optional Variables

- `aws_region`: AWS region for Lambda deployment (default: ap-southeast-2)
- `max_age_days`: Days before shutdown (default: 3)
- `dry_run`: Test mode without stopping resources (default: "false")
- `target_regions`: List of regions to scan (default: us-east-1, us-west-2, ap-southeast-2, ap-southeast-4, eu-west-1)
- `schedule_expression`: CloudWatch Events schedule (default: "cron(0 22 * * ? *)")
- `log_level`: Logging verbosity - "minimal" or "verbose" (default: "minimal")

### Free Tier Optimization

The configuration is optimized to stay within AWS free tier limits:

- **CloudWatch Logs**: 1-day retention to minimize log ingestion costs
- **Log Level**: Set to "minimal" by default - only logs resource actions and final summary
- **Lambda**: Efficient execution time and memory usage

**Cost comparison:**
- `log_level = "minimal"`: ~$0-0.50/month (stays in free tier)
- `log_level = "verbose"`: ~$1-3/month (may exceed free tier for CloudWatch logs)

To enable detailed logging for debugging:
```hcl
log_level = "verbose"
```

### Schedule Examples

```hcl
# Daily at 10 PM UTC
schedule_expression = "cron(0 22 * * ? *)"

# Daily at 8 AM Sydney time (10 PM UTC)
schedule_expression = "cron(0 22 * * ? *)"

# Every 12 hours
schedule_expression = "rate(12 hours)"

# Weekdays only at 6 PM UTC
schedule_expression = "cron(0 18 ? * MON-FRI *)"
```

## Testing

1. **Deploy in dry-run mode first**:
   ```hcl
   dry_run = "true"
   ```

2. **Manually invoke the Lambda**:
   ```bash
   aws lambda invoke \
     --function-name aws-auto-shutdown \
     --payload '{}' \
     response.json
   
   cat response.json
   ```

3. **Check CloudWatch Logs**:
   ```bash
   aws logs tail /aws/lambda/aws-auto-shutdown --follow
   ```

## Monitoring

### CloudWatch Logs
View Lambda execution logs:
```bash
aws logs describe-log-streams \
  --log-group-name /aws/lambda/aws-auto-shutdown \
  --order-by LastEventTime \
  --descending
```

### SNS Notifications
You'll receive email notifications when:
- Resources are identified for shutdown
- Resources are actually stopped (when not in dry-run mode)
- Errors occur during execution

## Cost Estimation

- **Lambda**: ~$0.20/month (assuming 1 execution/day, 256MB, 5-minute runtime)
- **CloudWatch Logs**: ~$0.50/month
- **SNS**: Free tier covers 1,000 email notifications/month
- **Total**: < $1/month

## Updating

To update the Lambda function code:

1. Edit `lambda_function.py`
2. Run:
   ```bash
   terraform apply
   ```

To change the schedule or other settings:

1. Update `terraform.tfvars`
2. Run:
   ```bash
   terraform apply
   ```

## Destroying

To remove all resources:

```bash
terraform destroy
```

## Troubleshooting

### Lambda not executing
- Check EventBridge rule is enabled
- Verify Lambda permissions
- Check CloudWatch Logs for errors

### Not receiving notifications
- Confirm SNS subscription in your email
- Check spam folder
- Verify email address in terraform.tfvars

### Resources not being stopped
- Ensure `dry_run = "false"`
- Check Lambda has required IAM permissions
- Verify regions are correctly specified

## Security Notes

- Lambda function has minimal required permissions
- S3 bucket for Lambda code is private
- IAM role follows least-privilege principle
- Sensitive values (email, Slack webhook) are handled as variables