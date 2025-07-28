# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository contains AWS utilities, primarily focused on automated resource management. The main component is a Terraform-deployed Lambda function that automatically shuts down AWS resources older than a specified number of days to prevent cost overruns.

## Architecture

### Core Components

1. **Lambda Function** (`terraform/lambda_function.py`)
   - Python 3.11 runtime
   - Scans multiple AWS regions for old resources
   - Supports EC2, RDS, ECS services, and NAT Gateways
   - Configurable age threshold and dry-run mode
   - SNS email notifications for actions taken

2. **Terraform Infrastructure** (`terraform/`)
   - Lambda deployment with EventBridge scheduling
   - IAM roles with least-privilege permissions
   - SNS topic for email notifications
   - S3 bucket for Lambda deployment packages
   - CloudWatch Logs with 14-day retention

### Resource Management Capabilities

The Lambda function manages:
- EC2 instances (stops instances, adds shutdown tags)
- RDS database instances (stops databases)
- ECS services (scales to 0 tasks)
- NAT Gateways (deletes gateways)

## Common Commands

### Terraform Deployment

```bash
# Navigate to terraform directory
cd terraform

# Initialize Terraform
terraform init

# Plan deployment
terraform plan

# Apply configuration
terraform apply

# Destroy resources
terraform destroy
```

### Testing and Monitoring

```bash
# Test Lambda function manually
aws lambda invoke \
  --function-name aws-auto-shutdown \
  --payload '{}' \
  response.json

# View Lambda logs
aws logs tail /aws/lambda/aws-auto-shutdown --follow

# Check CloudWatch log streams
aws logs describe-log-streams \
  --log-group-name /aws/lambda/aws-auto-shutdown \
  --order-by LastEventTime \
  --descending
```

## Configuration

Key configuration files:
- `terraform.tfvars` - Main configuration (create from `terraform.tfvars.example`)
- Environment variables set in Lambda:
  - `MAX_AGE_DAYS` - Days before shutdown (default: 3)
  - `DRY_RUN` - Test mode without stopping resources
  - `SNS_TOPIC_ARN` - Notification topic
  - `REGIONS` - Comma-separated list of regions to scan

## Development Guidelines

### Lambda Function Updates
- Modify `lambda_function.py` directly
- Run `terraform apply` to deploy changes
- Lambda package is automatically zipped during deployment

### Adding New Resource Types
1. Create new function following pattern: `shutdown_old_<resource_type>()`
2. Add appropriate IAM permissions in `main.tf`
3. Update summary dictionary and notification formatting
4. Test in dry-run mode first

### Testing Approach
- Always test with `dry_run = "true"` first
- Check CloudWatch Logs for execution details
- Verify SNS notifications are received
- Use small `max_age_days` value for testing