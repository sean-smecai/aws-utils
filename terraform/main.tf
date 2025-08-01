terraform {
  required_version = ">= 1.0"
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  
  backend "s3" {
    bucket = "aws-utils-terraform-state-bucket"
    key    = "aws-utils/auto-shutdown/terraform.tfstate"
    region = "ap-southeast-2"
  }
}

provider "aws" {
  region = var.aws_region
}

# Data source for current AWS account
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Create S3 bucket for Lambda deployment packages
resource "aws_s3_bucket" "lambda_bucket" {
  bucket = "${data.aws_caller_identity.current.account_id}-aws-auto-shutdown-lambda"
  
  tags = {
    Name        = "AWS Auto Shutdown Lambda"
    Environment = "production"
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket_public_access_block" "lambda_bucket" {
  bucket = aws_s3_bucket.lambda_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lambda execution role
resource "aws_iam_role" "lambda_role" {
  name = "aws-auto-shutdown-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# Lambda policy for resource management
resource "aws_iam_role_policy" "lambda_policy" {
  name = "aws-auto-shutdown-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:StopInstances",
          "ec2:CreateTags",
          "ec2:DescribeRegions",
          "ec2:DescribeNatGateways",
          "ec2:DeleteNatGateway",
          "ec2:DescribeAddresses",
          "ec2:ReleaseAddress",
          "rds:DescribeDBInstances",
          "rds:StopDBInstance",
          "rds:AddTagsToResource",
          "rds:ListTagsForResource",
          "ecs:ListClusters",
          "ecs:ListServices",
          "ecs:DescribeServices",
          "ecs:UpdateService",
          "sts:GetCallerIdentity"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "sns:Publish"
        ]
        Resource = aws_sns_topic.notifications.arn
      }
    ]
  })
}

# Attach basic Lambda execution policy
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  role       = aws_iam_role.lambda_role.name
}

# SNS topic for notifications
resource "aws_sns_topic" "notifications" {
  name = "aws-auto-shutdown-notifications"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# Lambda function
resource "aws_lambda_function" "auto_shutdown" {
  filename         = "${path.module}/lambda_deployment.zip"
  function_name    = "aws-auto-shutdown"
  role            = aws_iam_role.lambda_role.arn
  handler         = "lambda_function.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime         = "python3.11"
  timeout         = 300  # 5 minutes
  memory_size     = 256

  environment {
    variables = {
      MAX_AGE_DAYS       = var.max_age_days
      DRY_RUN           = var.dry_run
      SNS_TOPIC_ARN     = aws_sns_topic.notifications.arn
      REGIONS           = join(",", var.target_regions)
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic,
    aws_cloudwatch_log_group.lambda_logs,
  ]
}

# CloudWatch Logs
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/aws-auto-shutdown"
  retention_in_days = 14
}

# EventBridge rule for daily execution
resource "aws_cloudwatch_event_rule" "daily_trigger" {
  name                = "aws-auto-shutdown-daily"
  description         = "Trigger auto-shutdown Lambda daily"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.daily_trigger.name
  target_id = "AutoShutdownLambda"
  arn       = aws_lambda_function.auto_shutdown.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auto_shutdown.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_trigger.arn
}

# Package Lambda function
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_function.py"
  output_path = "${path.module}/lambda_deployment.zip"
}