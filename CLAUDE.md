# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

AWS utilities repository focused on automated cloud resource management to prevent cost overruns. The primary component is a Terraform-deployed Lambda function that automatically shuts down AWS resources exceeding a specified age threshold.

## Architecture

### Core Components

1. **Lambda Function** (`terraform/lambda_function.py`)
   - Python 3.11 runtime
   - Multi-region resource scanning
   - Manages EC2, RDS, ECS, NAT Gateways
   - Environment-based configuration
   - SNS notifications for all actions
   - Optimized logging (minimal/verbose modes)

2. **Terraform Infrastructure** (`terraform/`)
   - S3 backend state management (bucket: `aws-utils-terraform-state-bucket`)
   - EventBridge scheduled execution (default: 10 PM UTC daily)
   - IAM roles with least-privilege permissions
   - Private S3 bucket for Lambda packages
   - CloudWatch Logs with 14-day retention

3. **Supporting Scripts** (`scripts/`)
   - `aws-shutdown-report.py` - Generate resource age reports with cost estimates
   - `aws-auto-shutdown.sh` - Manual shutdown script with report integration

## Common Commands

### Terraform Deployment

```bash
# Initial setup
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your email and preferences

# Deploy infrastructure
terraform init
terraform plan
terraform apply

# Destroy all resources
terraform destroy
```

### Lambda Testing & Monitoring

```bash
# Manual Lambda invocation
aws lambda invoke \
  --function-name aws-auto-shutdown \
  --payload '{}' \
  response.json
cat response.json

# Monitor execution logs
aws logs tail /aws/lambda/aws-auto-shutdown --follow

# View recent log streams
aws logs describe-log-streams \
  --log-group-name /aws/lambda/aws-auto-shutdown \
  --order-by LastEventTime \
  --descending \
  --limit 5
```

### Manual Operations

```bash
# Generate resource report
python scripts/aws-shutdown-report.py \
  --max-age-days 3 \
  --output aws-resources-report.json

# Execute shutdown based on report
./scripts/aws-auto-shutdown.sh \
  --dry-run \
  --max-age-days 3 \
  --report-file aws-resources-report.json

# Actually shutdown resources (removes --dry-run)
./scripts/aws-auto-shutdown.sh \
  --execute \
  --max-age-days 3
```

## Configuration

### Required Configuration
- `terraform.tfvars` - Create from example, must set:
  - `notification_email` - Email for SNS notifications

### Key Environment Variables (Lambda)
- `MAX_AGE_DAYS` - Resource age threshold (default: 3)
- `DRY_RUN` - Test mode flag ("true"/"false", default: "false")
- `SNS_TOPIC_ARN` - Auto-populated by Terraform
- `REGIONS` - Comma-separated regions (default: "us-east-1,us-west-2,ap-southeast-2")
- `LOG_LEVEL` - "minimal" (default) or "verbose"

### Cost Optimization Settings
- `log_level = "minimal"` - Stays within free tier (~$0-0.50/month)
- `log_level = "verbose"` - May exceed free tier (~$1-3/month)
- CloudWatch retention: 14 days (configurable in `main.tf`)

## Resource Management Details

### Shutdown Functions
- `shutdown_old_ec2_instances()` - Stops EC2 instances, adds tags
- `shutdown_old_rds_instances()` - Stops RDS databases
- `shutdown_old_ecs_services()` - Scales ECS services to 0
- `delete_old_nat_gateways()` - Deletes NAT gateways

### Resource Age Calculation
- EC2: Based on `LaunchTime`
- RDS: Based on `InstanceCreateTime`
- ECS: Based on service `CreatedAt`
- NAT: Based on `CreateTime`

## Development Guidelines

### Lambda Function Updates
1. Edit `terraform/lambda_function.py`
2. Deploy with `terraform apply` (auto-packages)
3. Test with dry-run first
4. Monitor CloudWatch Logs

### Adding New Resource Types
1. Add function `shutdown_old_<resource_type>()` in `lambda_function.py`
2. Update IAM policy in `main.tf` (add required permissions)
3. Add to `lambda_handler()` execution flow
4. Update summary collection and SNS formatting
5. Test with `DRY_RUN=true`

### Testing Best Practices
- Always start with `dry_run = "true"` in terraform.tfvars
- Use short `max_age_days` (e.g., 1) for testing
- Verify SNS email confirmation before production use
- Check CloudWatch Logs for detailed execution info
- Test in a single region first before multi-region

## Troubleshooting

### Common Issues

1. **Lambda not executing**
   - Verify EventBridge rule is enabled
   - Check IAM role permissions
   - Review CloudWatch Logs for errors

2. **Not receiving notifications**
   - Confirm SNS subscription via email
   - Check spam folder
   - Verify `notification_email` in terraform.tfvars

3. **Resources not stopping**
   - Ensure `dry_run = "false"`
   - Check resource age exceeds `max_age_days`
   - Verify Lambda has permissions for target regions

4. **Terraform state issues**
   - S3 backend bucket: `aws-utils-terraform-state-bucket`
   - Region: `ap-southeast-2`
   - State file: `aws-utils/auto-shutdown/terraform.tfstate`

## Task Master AI Instructions
**Import Task Master's development workflow commands and guidelines, treat as if import is in the main CLAUDE.md file.**
@./.taskmaster/CLAUDE.md
