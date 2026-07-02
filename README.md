# AWS Utilities - Auto-Shutdown Service

Automated AWS resource management to prevent cost overruns. This repository contains a Terraform-deployed Lambda function that automatically shuts down AWS resources exceeding a specified age threshold.

## Overview

The AWS Auto-Shutdown service helps manage cloud costs by automatically cleaning up old, potentially forgotten resources across multiple AWS regions. It runs on a configurable schedule and sends notifications about its actions.

### Key Features

- **Automated Resource Management**: Stops/deletes resources older than configured threshold
- **Multi-Region Support**: Scans and manages resources across multiple AWS regions
- **Amplify Usage Tracking**: Read-only nightly snapshot of request counts per Amplify app
- **Cost Optimization**: Intelligent scheduling with cost impact analysis
- **Protection System**: Whitelist/blacklist patterns to protect critical resources
- **Comprehensive Logging**: Structured JSON logging with CloudWatch integration
- **Flexible Notifications**: Email alerts via SNS with detailed cleanup summaries
- **Dry-Run Mode**: Test cleanup operations without actually affecting resources

### Supported Resource Types

- **EC2 Instances**: Stops instances older than threshold
- **RDS Databases**: Stops database instances
- **ECS Services**: Scales services to 0 tasks
- **NAT Gateways**: Deletes gateways and releases Elastic IPs
- **Load Balancers**: Removes ELB/ALB/NLB load balancers
- **S3 Buckets**: Deletes buckets (with safety checks)
- **Elasticsearch/OpenSearch**: Deletes domains
- **Amazon WorkSpaces**: Stops running WorkSpaces (when enabled)
- **Amplify Apps**: **Read-only** — reports CloudWatch request counts per app, never modifies them

## Quick Start

### Prerequisites

1. AWS CLI configured with appropriate credentials
2. Terraform >= 1.0
3. Python 3.11+ (for local testing)
4. IAM permissions for Lambda, EventBridge, SNS, and resource management

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/aws-utils.git
   cd aws-utils/terraform
   ```

2. **Configure your settings**:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   # Edit terraform.tfvars with your email and preferences
   ```

3. **Deploy the infrastructure**:
   ```bash
   terraform init
   terraform plan
   terraform apply
   ```

4. **Confirm SNS subscription**:
   Check your email and confirm the subscription to receive notifications.

## Architecture

### Architecture Diagram

```mermaid
graph TB
    subgraph TriggerLayer[Trigger Layer]
        EB[EventBridge Scheduler<br/>Daily @ 10PM UTC]
    end

    subgraph ComputeLayer[Compute Layer]
        LF[Lambda Function<br/>Python 3.11<br/>aws-auto-shutdown]
    end

    subgraph AWSRegions[AWS Regions]
        direction TB
        R1[Region 1<br/>us-east-1]
        R2[Region 2<br/>us-west-2]
        R3[Region N<br/>eu-north-1]
        RD[Dynamic Discovery<br/>All Regions]
    end

    subgraph TargetResources[Target Resources]
        direction LR
        EC2[EC2 Instances]
        RDS[RDS Databases]
        ECS[ECS Services]
        NAT[NAT Gateways]
        ELB[Load Balancers]
        S3B[S3 Buckets]
        ES[Elasticsearch]
        WS[WorkSpaces]
    end

    subgraph ProtectionSystem[Protection System]
        PF[Protection Filters<br/>- Blacklist Patterns<br/>- Protected Tags<br/>- Free Tier Check]
    end

    subgraph NotificationLayer[Notification Layer]
        SNS[SNS Topic]
        EMAIL[Email Notifications<br/>- Summary Report<br/>- Cost Analysis<br/>- Resources Affected]
    end

    subgraph MonitoringStorage[Monitoring & Storage]
        CW[CloudWatch Logs<br/>14-day retention]
        CWM[CloudWatch Metrics<br/>Performance & Costs]
        S3[S3 Backend<br/>Terraform State]
    end

    EB -->|Triggers| LF
    LF -->|Discovers| RD
    RD -->|Scans| R1
    RD -->|Scans| R2
    RD -->|Scans| R3
    
    R1 --> EC2
    R1 --> RDS
    R1 --> ECS
    R1 --> NAT
    R1 --> ELB
    R1 --> S3B
    R1 --> ES
    R1 --> WS
    
    EC2 -->|Check| PF
    RDS -->|Check| PF
    ECS -->|Check| PF
    NAT -->|Check| PF
    ELB -->|Check| PF
    S3B -->|Check| PF
    ES -->|Check| PF
    WS -->|Check| PF
    
    PF -->|Stop/Delete| EC2
    PF -->|Stop| RDS
    PF -->|Scale to 0| ECS
    PF -->|Delete| NAT
    PF -->|Delete| ELB
    PF -->|Delete| S3B
    PF -->|Delete| ES
    PF -->|Stop| WS
    
    LF -->|Publishes| SNS
    SNS -->|Sends| EMAIL
    LF -->|Logs| CW
    LF -->|Metrics| CWM
    
    style EB fill:#ff9900,stroke:#333,stroke-width:2px,color:#fff
    style LF fill:#ff9900,stroke:#333,stroke-width:2px,color:#fff
    style SNS fill:#ff4b4b,stroke:#333,stroke-width:2px,color:#fff
    style PF fill:#4CAF50,stroke:#333,stroke-width:2px,color:#fff
    style RD fill:#2196F3,stroke:#333,stroke-width:2px,color:#fff
```

### Resource Flow Diagram

```mermaid
sequenceDiagram
    participant EB as EventBridge
    participant Lambda as Lambda Function
    participant Regions as AWS Regions
    participant Resources as Resources
    participant Protection as Protection System
    participant SNS as SNS Topic
    participant User as User Email

    EB->>Lambda: Trigger (Daily)
    Lambda->>Lambda: Load Configuration
    
    alt Scan All Regions = true
        Lambda->>Regions: Discover All Regions
        Regions-->>Lambda: Return Region List
    else Use Target Regions
        Lambda->>Lambda: Use Configured Regions
    end
    
    loop For Each Region
        Lambda->>Resources: Scan EC2, RDS, ECS, etc.
        Resources-->>Lambda: Return Resource List
        
        loop For Each Resource
            Lambda->>Protection: Check Protection Rules
            Protection-->>Lambda: Allow/Block Decision
            
            alt Resource Not Protected & Age > Threshold
                Lambda->>Resources: Stop/Delete Resource
                Resources-->>Lambda: Confirmation
            end
        end
    end
    
    Lambda->>Lambda: Generate Summary Report
    Lambda->>SNS: Send Notification
    SNS->>User: Email Report
    
    alt No Resources Found
        SNS->>User: "All Clear" Status Email
    else Resources Stopped
        SNS->>User: Detailed Action Report
    end
```

### Components

1. **Lambda Function** (`terraform/lambda_function.py`)
   - Python 3.11 runtime
   - Executes cleanup logic across regions
   - Implements cost analysis and protection checks
   - Sends SNS notifications

2. **EventBridge Scheduler**
   - Triggers Lambda on schedule (default: 10 PM UTC daily)
   - Configurable via `schedule_expression` variable

3. **SNS Topic**
   - Sends email notifications for all cleanup actions
   - Includes cost impact analysis in notifications

4. **CloudWatch**
   - Stores execution logs with configurable retention
   - Custom metrics for monitoring
   - Alarms for errors and performance issues

5. **S3 Backend**
   - Terraform state management
   - Bucket: `aws-utils-terraform-state-bucket`
   - Region: `ap-southeast-2`

## Configuration

### Required Settings

Create `terraform.tfvars` with:
```hcl
notification_email = "your-email@example.com"
```

### Key Configuration Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `max_age_days` | Days before resource shutdown | 3 |
| `dry_run` | Test mode without stopping resources | "false" |
| `scan_all_regions` | Automatically scan ALL AWS regions | false |
| `target_regions` | AWS regions to scan (if scan_all_regions is false) | ["us-east-1", "us-west-2", "ap-southeast-2", "ap-southeast-4", "eu-west-1"] |
| `enable_workspaces_monitoring` | Monitor and stop Amazon WorkSpaces | false |
| `enable_amplify_monitoring` | Read-only Amplify app usage tracking in the nightly email (~$0.60/mo) | true |
| `always_send_notification` | Send email even when no resources found | true |
| `schedule_expression` | CloudWatch Events schedule | "cron(0 22 * * ? *)" |
| `log_level` | Logging verbosity (minimal/verbose) | "minimal" |
| `protection_enabled` | Enable resource protection system | true |
| `cost_optimization_enabled` | Enable cost-based scheduling | true |

### Protection Configuration

Protect critical resources using patterns and tags:

```json
{
  "ec2": {
    "blacklist_patterns": ["*production*", "*critical*"],
    "protected_tags": {
      "Environment": ["production", "prod"],
      "Protected": ["true"]
    }
  }
}
```

### Cost Optimization Settings

- `cost_threshold_high`: Monthly cost to consider resource high-value ($100)
- `cost_threshold_require_approval`: Requires approval before deletion ($500)
- `business_hours_start/end`: Avoid cleanup during business hours
- `cost_optimized_cleanup_windows`: Preferred cleanup time windows

### Schedule Examples

```hcl
# Daily at 10 PM UTC
schedule_expression = "cron(0 22 * * ? *)"

# Every 12 hours
schedule_expression = "rate(12 hours)"

# Weekdays only at 6 PM UTC
schedule_expression = "cron(0 18 ? * MON-FRI *)"
```

## Amplify App Usage Tracking

The Lambda also reports request traffic for every AWS Amplify app it can see, so you can tell which apps are still being used and which are deployed-and-forgotten. **This feature is strictly read-only — it never tags, stops, or deletes Amplify apps.**

### What it does

On each nightly run, the Lambda iterates the regions in `target_regions` (or every region if `scan_all_regions = true`), lists all Amplify apps, and queries the CloudWatch `AWS/AmplifyHosting/Requests` metric per app for the trailing 90 days. It buckets the daily datapoints into three windows — **7 days / 30 days / 90 days** — and adds a section to the SNS email sorted by `requests_30d` descending. Zero-traffic apps cluster at the bottom of the list.

New Amplify apps are picked up automatically on the next run; deleted apps drop out. No per-app configuration or per-repo instrumentation is required.

### Sample email output

```
Amplify App Usage (Requests: 7d / 30d / 90d):
  - pdf-to-coreplus (d3rgegijwscmel) in ap-southeast-2 Updated: 2026-05-04
      1234 / 4567 / 12890
      https://ap-southeast-2.console.aws.amazon.com/amplify/apps/d3rgegijwscmel/branches
  - medical-llm (d7ev0xll7z5fm) in ap-southeast-2 Updated: 2026-05-12
      0 / 0 / 0
      https://ap-southeast-2.console.aws.amazon.com/amplify/apps/d7ev0xll7z5fm/branches
  ...
```

The `Updated:` timestamp is the app's last configuration change (from `list-apps`), not the last successful deploy — useful for telling "genuinely idle" apart from "touched recently but no traffic."

### How to see the usage

There are six ways to view Amplify usage data, from easiest to most direct:

**1. Open the auto-managed CloudWatch dashboard (best for browsing)**

The Lambda calls `cloudwatch:PutDashboard` on every nightly run, rebuilding a dashboard called **`Amplify-App-Usage`** in the Lambda's running region. It always reflects the current set of Amplify apps — new apps appear automatically, deleted apps drop off. Three widgets: daily requests time series (30 days), 30-day total bar chart, 7-day total bar chart. Open it at:

```
https://ap-southeast-2.console.aws.amazon.com/cloudwatch/home?region=ap-southeast-2#dashboards:name=Amplify-App-Usage
```

The dashboard is recreated every run, so manual edits will be overwritten — change widget shape via `put_amplify_usage_dashboard()` in `lambda_function.py`. Cost: $0 for the first 3 dashboards in your account, $3/mo each after that.

**2. Wait for the nightly email**

The SNS email is sent once per day (10pm UTC by default). The Amplify section appears near the bottom of the body, sorted by 30-day request count descending.

**3. Trigger a run on-demand**

```bash
aws lambda invoke \
  --function-name aws-auto-shutdown \
  --payload '{}' \
  --region ap-southeast-2 \
  response.json
```

The email arrives ~30–60 seconds later. The full per-app data is also in `response.json` under `summary.amplify_apps`. To extract it:

```bash
jq '.summary.amplify_apps | sort_by(-.requests_30d)' response.json
```

**4. Query CloudWatch directly for a specific app**

To see request counts for one app without running the Lambda:

```bash
APP_ID="d3rgegijwscmel"   # replace with your appId from `aws amplify list-apps`
aws cloudwatch get-metric-statistics \
  --namespace AWS/AmplifyHosting \
  --metric-name Requests \
  --dimensions Name=App,Value=$APP_ID \
  --start-time "$(date -u -v-30d +%Y-%m-%dT%H:%M:%S)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%S)" \
  --period 86400 \
  --statistics Sum \
  --region ap-southeast-2 \
  --query 'Datapoints | sort_by(@, &Timestamp)[].[Timestamp, Sum]' \
  --output table
```

**5. Browse the custom CloudWatch metrics**

The Lambda publishes `AmplifyAppsScanned` and `AmplifyAppsWithZeroTraffic30d` per region under the custom namespace. View trends over time at:

```
https://ap-southeast-2.console.aws.amazon.com/cloudwatch/home?region=ap-southeast-2#metricsV2:graph=~()
```

Then filter for namespace `AWS/AutoShutdown` and pick `AmplifyAppsScanned` or `AmplifyAppsWithZeroTraffic30d`.

**6. Tail the Lambda logs**

The structured JSON logs also contain the per-app data (useful when debugging):

```bash
aws logs tail /aws/lambda/aws-auto-shutdown --follow --region ap-southeast-2 \
  | grep -i amplify
```

### Enabling and disabling

Enabled by default. To turn off (e.g. if you don't want the extra section in your email):

```hcl
# In terraform.tfvars
enable_amplify_monitoring = false
```

Apply with `terraform apply`. Affects only the email/metrics — no resources change.

### Custom CloudWatch metrics

Each run publishes two metrics under the existing custom namespace, per region:

- `AmplifyAppsScanned` — number of apps successfully queried
- `AmplifyAppsWithZeroTraffic30d` — apps with zero requests over the trailing 30 days

You can graph either in CloudWatch or build an alarm (e.g. alert when `AmplifyAppsWithZeroTraffic30d` exceeds N).

### IAM permissions used

The Lambda's execution role is granted these **read-only** actions:

- `amplify:ListApps`
- `amplify:ListBranches`
- `cloudwatch:GetMetricStatistics`

### Cost

Roughly **$0.60/month** in the default configuration — almost entirely custom-metric storage ($0.30/metric/month × 2 new metrics). API calls cost less than $0.01/month at any realistic app count. Disabling `enable_amplify_monitoring` drops this to $0.

## Deployment

### Initial Deployment

```bash
cd terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

### Updating the Lambda Function

1. Edit `terraform/lambda_function.py`
2. Apply changes:
   ```bash
   terraform apply
   ```

### Updating Configuration

1. Modify `terraform.tfvars`
2. Apply changes:
   ```bash
   terraform apply
   ```

## Testing

### 1. Dry-Run Testing

Always test with dry-run mode first:
```hcl
# In terraform.tfvars
dry_run = "true"
max_age_days = 1  # Test with short threshold
```

### 2. Manual Invocation

```bash
# Invoke Lambda manually
aws lambda invoke \
  --function-name aws-auto-shutdown \
  --payload '{}' \
  response.json

# Check results
cat response.json
```

### 3. Monitor Logs

```bash
# Follow CloudWatch logs
aws logs tail /aws/lambda/aws-auto-shutdown --follow

# View recent log streams
aws logs describe-log-streams \
  --log-group-name /aws/lambda/aws-auto-shutdown \
  --order-by LastEventTime \
  --descending \
  --limit 5
```

### 4. Verify Protection

Test that protected resources are skipped:
1. Tag a test resource with `Protected=true`
2. Run Lambda in dry-run mode
3. Verify resource appears in protection logs

## Troubleshooting

### Lambda Not Executing

- **Check EventBridge rule**: 
  ```bash
  aws events describe-rule --name aws-auto-shutdown-daily
  ```
- **Verify IAM permissions**: Review Lambda execution role
- **Check CloudWatch Logs**: Look for error messages

### Not Receiving Notifications

- Confirm SNS subscription in your email
- Check spam/junk folder
- Verify email in `terraform.tfvars`
- Check SNS topic:
  ```bash
  aws sns list-subscriptions-by-topic \
    --topic-arn arn:aws:sns:region:account:aws-auto-shutdown-notifications
  ```

### Resources Not Being Stopped

- Ensure `dry_run = "false"` in configuration
- Check resource age exceeds `max_age_days`
- Verify Lambda has permissions for target regions
- Review protection configuration for exclusion patterns
- Check scheduling constraints (business hours, cost windows)

### Terraform State Issues

If you encounter state lock issues:
```bash
# Force unlock (use carefully)
terraform force-unlock <lock-id>

# Refresh state
terraform refresh
```

## Cost Estimation

### AWS Free Tier Usage

Configuration optimized for free tier:
- **Lambda**: ~$0.20/month (1 execution/day, 256MB, 5-min runtime)
- **CloudWatch Logs**: 
  - Minimal logging: ~$0-0.50/month (stays in free tier)
  - Verbose logging: ~$1-3/month (may exceed free tier)
- **SNS**: Free tier covers 1,000 email notifications/month
- **Amplify monitoring** (if `enable_amplify_monitoring = true`): ~$0.60/month for two custom CloudWatch metrics
- **Total**: < $2/month with minimal logging and Amplify monitoring enabled (< $1/month without)

### Cost Optimization Tips

1. Use `log_level = "minimal"` to stay within free tier
2. Set CloudWatch log retention to 1 day
3. Optimize Lambda memory allocation (256MB is sufficient)
4. Use scheduling to run during off-peak hours

## Development

### Project Structure

```
aws-utils/
├── terraform/
│   ├── lambda_function.py    # Lambda function code
│   ├── main.tf               # Terraform infrastructure
│   ├── variables.tf          # Variable definitions
│   ├── outputs.tf            # Output definitions
│   └── terraform.tfvars      # Your configuration (git-ignored)
├── scripts/
│   ├── aws-shutdown-report.py    # Generate resource reports
│   └── aws-auto-shutdown.sh      # Manual shutdown script
├── CLAUDE.md                 # AI assistant context
└── README.md                 # This file
```

### Adding New Resource Types

1. Add cleanup function in `lambda_function.py`:
   ```python
   def cleanup_old_<resource_type>(region, max_age_days, dry_run=True):
       # Implementation
   ```

2. Update IAM permissions in `main.tf`

3. Add to `lambda_handler()` execution flow

4. Update SNS notification formatting

5. Test thoroughly with dry-run mode

### Manual Scripts

Generate resource reports:
```bash
python scripts/aws-shutdown-report.py \
  --max-age-days 3 \
  --output aws-resources-report.json
```

Manual shutdown with report:
```bash
./scripts/aws-auto-shutdown.sh \
  --dry-run \
  --max-age-days 3 \
  --report-file aws-resources-report.json
```

## Security Notes

- Lambda function uses minimal required IAM permissions
- S3 bucket for Lambda packages is private with public access blocked
- IAM role follows least-privilege principle
- Sensitive values handled as Terraform variables
- Protection system prevents accidental deletion of critical resources
- All actions are logged for audit purposes

## Cleanup

To remove all deployed resources:

```bash
cd terraform
terraform destroy
```

This will remove:
- Lambda function and execution role
- EventBridge schedule rule
- SNS topic and subscriptions
- CloudWatch log groups and alarms
- S3 bucket for Lambda deployments

## Contributing

1. Fork the repository
2. Create a feature branch
3. Test changes thoroughly with dry-run mode
4. Submit a pull request with detailed description

## License

MIT License - See LICENSE file for details

## Support

For issues, questions, or suggestions:
- Open an issue on GitHub
- Check existing issues for solutions
- Review CloudWatch logs for debugging