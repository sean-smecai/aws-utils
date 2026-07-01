# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

AWS utilities repository focused on automated cloud resource management to prevent cost overruns. The primary component is a Terraform-deployed Lambda function that automatically shuts down AWS resources exceeding a specified age threshold.

## Architecture

### Core Components

1. **Lambda Function** (`terraform/lambda_function.py`)
   - Python 3.11 runtime with boto3
   - Multi-region resource scanning (configurable or all-regions mode)
   - Manages: EC2, RDS, ECS, NAT Gateways, Load Balancers, S3 Buckets, Elasticsearch/OpenSearch, WorkSpaces
   - Environment-based configuration
   - Protection system with blacklist/whitelist patterns and tag-based exemptions
   - Cost analysis and optimization features
   - SNS notifications with detailed action summaries

2. **Terraform Infrastructure** (`terraform/`)
   - S3 backend state management (bucket: `aws-utils-terraform-state-bucket`, region: `ap-southeast-2`)
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
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your email and preferences

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

# Execute shutdown based on report (dry-run)
./scripts/aws-auto-shutdown.sh \
  --dry-run \
  --max-age-days 3 \
  --report-file aws-resources-report.json

# Actually shutdown resources
./scripts/aws-auto-shutdown.sh \
  --execute \
  --max-age-days 3
```

## Configuration

### Required Configuration
- `terraform.tfvars` - Create from example, must set:
  - `notification_email` - Email for SNS notifications

### Key Terraform Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `max_age_days` | Days before resource shutdown | 3 |
| `dry_run` | Test mode ("true"/"false") | "false" |
| `scan_all_regions` | Scan ALL AWS regions automatically | false |
| `target_regions` | Specific regions to scan | us-east-1, us-west-2, ap-southeast-2, ap-southeast-4, eu-west-1 |
| `enable_workspaces_monitoring` | Monitor Amazon WorkSpaces | false |
| `enable_amplify_monitoring` | Track Amplify app request usage | true |
| `always_send_notification` | Email even when no resources found | true |
| `schedule_expression` | CloudWatch Events schedule | "cron(0 22 * * ? *)" |
| `log_level` | "minimal" or "verbose" | "minimal" |
| `protection_enabled` | Enable resource protection system | true |

### Lambda Environment Variables
- `MAX_AGE_DAYS`, `DRY_RUN`, `SNS_TOPIC_ARN` - Core settings
- `REGIONS`, `SCAN_ALL_REGIONS` - Region configuration
- `LOG_LEVEL` - Logging verbosity
- `ENABLE_AMPLIFY_MONITORING` - Enable/disable read-only Amplify usage tracking
- `PROTECTION_ENABLED` - Enable/disable protection system
- `S3_BUCKET_EXCLUSIONS`, `ELB_NAME_EXCLUSIONS`, `ES_DOMAIN_EXCLUSIONS` - Exclusion patterns

### Protection System Configuration

Resources can be protected via:
- **Blacklist patterns**: `*production*`, `*critical*`, `*do-not-delete*`
- **Protected tags**: `Environment=production`, `Protected=true`, `ManagedBy=terraform`
- **Instance types**: t2.micro, t3.micro (free tier protection)

## Resource Management

### Shutdown Functions
- `shutdown_old_ec2_instances()` - Stops EC2 instances, adds shutdown tags
- `shutdown_old_rds_instances()` - Stops RDS databases
- `shutdown_old_ecs_services()` - Scales ECS services to 0
- `delete_old_nat_gateways()` - Deletes NAT gateways, releases Elastic IPs
- Load balancer, S3, Elasticsearch cleanup (with exclusion patterns)
- WorkSpaces shutdown (when enabled)

### Resource Age Calculation
- EC2: `LaunchTime`
- RDS: `InstanceCreateTime`
- ECS: Service `CreatedAt`
- NAT: `CreateTime`

## Development Guidelines

### Lambda Function Updates
1. Edit `terraform/lambda_function.py`
2. Deploy with `terraform apply` (auto-packages)
3. Test with `dry_run = "true"` first
4. Monitor CloudWatch Logs for errors

### Adding New Resource Types
1. Add function `shutdown_old_<resource_type>()` in `lambda_function.py`
2. Add protection rules in `DEFAULT_PROTECTION_RULES` dict
3. Update IAM policy in `main.tf` (add required permissions)
4. Add to `lambda_handler()` execution flow
5. Update summary collection and SNS formatting
6. Test with `DRY_RUN=true`

## Troubleshooting

### Common Issues

1. **Lambda not executing**
   - Verify EventBridge rule: `aws events describe-rule --name aws-auto-shutdown-daily`
   - Check IAM role permissions
   - Review CloudWatch Logs

2. **Not receiving notifications**
   - Confirm SNS subscription via email
   - Check spam folder
   - Check topic: `aws sns list-subscriptions-by-topic --topic-arn <arn>`

3. **Resources not stopping**
   - Ensure `dry_run = "false"`
   - Check resource age exceeds `max_age_days`
   - Verify Lambda has permissions for target regions
   - Check protection configuration for exclusion patterns

4. **Terraform state issues**
   - S3 backend bucket: `aws-utils-terraform-state-bucket`
   - Region: `ap-southeast-2`
   - State file: `aws-utils/auto-shutdown/terraform.tfstate`
   - Force unlock: `terraform force-unlock <lock-id>`

## Task Master AI Instructions
**Import Task Master's development workflow commands and guidelines, treat as if import is in the main CLAUDE.md file.**
@./.taskmaster/CLAUDE.md
