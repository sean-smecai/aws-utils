# =============================================================================
# Bedrock cost guardrail  (Bedrock has NO native $ cap -- this is the layering)
#
#   1. Model allow-list : IAM Deny of InvokeModel on any non-approved model
#                         (blocks Opus etc.). REAL-TIME, per request, no lag.
#   2. Kill-switch       : scheduled Lambda polls Bedrock token volume via
#                         CloudWatch GetMetricData (SEARCH across all models --
#                         metric ALARMS can't do SEARCH, GetMetricData can) and
#                         denies InvokeModel on every principal on a spike.
#                         NEAR-REAL-TIME volume brake (~one poll interval).
#
# Covers both AU Bedrock regions: ap-southeast-2 (Sydney) and ap-southeast-4
# (Melbourne -- AU inference profiles route to Melbourne). IAM is global, so the
# allow-list applies everywhere; only the poller Lambda is per-region.
#
# All resources are gated by enable_* flags (default false) -- inert until armed.
# =============================================================================

provider "aws" {
  alias  = "syd"
  region = "ap-southeast-2"
}

provider "aws" {
  alias  = "mel"
  region = "ap-southeast-4"
}

locals {
  # Approved model ARNs (foundation models + inference profiles) per keyword.
  # The allow-list policy denies InvokeModel on anything NOT in this set.
  bedrock_allowed_model_arns = flatten([
    for kw in var.bedrock_allowed_model_keywords : [
      "arn:aws:bedrock:*::foundation-model/*${kw}*",
      "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*${kw}*",
    ]
  ])

  bedrock_role_arns = [for r in var.bedrock_principal_roles : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${r}"]
  bedrock_user_arns = [for u in var.bedrock_principal_users : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/${u}"]

  killswitch_count = var.enable_bedrock_killswitch ? 1 : 0

  killswitch_env = {
    DENY_POLICY_ARN = aws_iam_policy.bedrock_killswitch_deny.arn
    TARGET_ROLES    = join(",", var.bedrock_principal_roles)
    TARGET_USERS    = join(",", var.bedrock_principal_users)
    SNS_TOPIC_ARN   = aws_sns_topic.notifications.arn
    DRY_RUN         = var.bedrock_killswitch_dry_run ? "true" : "false"
    TOKEN_THRESHOLD = tostring(var.bedrock_token_alarm_threshold)
    PERIOD_SECONDS  = tostring(var.bedrock_alarm_period_seconds)
  }
}

# ---- 1. Model allow-list ----------------------------------------------------

data "aws_iam_policy_document" "bedrock_model_allowlist" {
  statement {
    sid           = "DenyNonApprovedBedrockModels"
    effect        = "Deny"
    actions       = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    not_resources = local.bedrock_allowed_model_arns
  }
}

resource "aws_iam_policy" "bedrock_model_allowlist" {
  name        = "bedrock-model-allowlist"
  description = "Deny Bedrock InvokeModel on any model except approved (cheap) ones. Blocks Opus."
  policy      = data.aws_iam_policy_document.bedrock_model_allowlist.json
}

resource "aws_iam_role_policy_attachment" "bedrock_allowlist_roles" {
  for_each   = var.enable_bedrock_guardrail ? toset(var.bedrock_principal_roles) : toset([])
  role       = each.value
  policy_arn = aws_iam_policy.bedrock_model_allowlist.arn
}

resource "aws_iam_user_policy_attachment" "bedrock_allowlist_users" {
  for_each   = var.enable_bedrock_guardrail ? toset(var.bedrock_principal_users) : toset([])
  user       = each.value
  policy_arn = aws_iam_policy.bedrock_model_allowlist.arn
}

# ---- 2a. Kill-switch: deny policy + Lambda role -----------------------------

data "aws_iam_policy_document" "bedrock_killswitch_deny" {
  statement {
    sid       = "KillSwitchDenyBedrockInvoke"
    effect    = "Deny"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "bedrock_killswitch_deny" {
  name        = "bedrock-killswitch-deny"
  description = "Full Bedrock InvokeModel deny, attached by the kill-switch on a token spike."
  policy      = data.aws_iam_policy_document.bedrock_killswitch_deny.json
}

data "aws_iam_policy_document" "killswitch_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "bedrock_killswitch" {
  name               = "bedrock-killswitch-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.killswitch_assume.json
}

data "aws_iam_policy_document" "killswitch_perms" {
  statement {
    sid       = "AttachDenyToRoles"
    effect    = "Allow"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = local.bedrock_role_arns
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = [aws_iam_policy.bedrock_killswitch_deny.arn]
    }
  }
  statement {
    sid       = "AttachDenyToUsers"
    effect    = "Allow"
    actions   = ["iam:AttachUserPolicy", "iam:DetachUserPolicy"]
    resources = local.bedrock_user_arns
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = [aws_iam_policy.bedrock_killswitch_deny.arn]
    }
  }
  statement {
    sid       = "ReadBedrockMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:GetMetricData"]
    resources = ["*"]
  }
  statement {
    sid       = "Notify"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.notifications.arn]
  }
  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role_policy" "killswitch_perms" {
  name   = "bedrock-killswitch-perms"
  role   = aws_iam_role.bedrock_killswitch.id
  policy = data.aws_iam_policy_document.killswitch_perms.json
}

data "archive_file" "bedrock_killswitch_zip" {
  type        = "zip"
  source_file = "${path.module}/bedrock_killswitch.py"
  output_path = "${path.module}/bedrock_killswitch.zip"
}

# ---- 2b. Kill-switch poller: Sydney (ap-southeast-2) ------------------------

resource "aws_lambda_function" "bedrock_killswitch_syd" {
  count            = local.killswitch_count
  provider         = aws.syd
  function_name    = "bedrock-killswitch"
  filename         = data.archive_file.bedrock_killswitch_zip.output_path
  source_code_hash = data.archive_file.bedrock_killswitch_zip.output_base64sha256
  role             = aws_iam_role.bedrock_killswitch.arn
  handler          = "bedrock_killswitch.lambda_handler"
  runtime          = "python3.11"
  timeout          = 60
  environment {
    variables = local.killswitch_env
  }
}

resource "aws_cloudwatch_event_rule" "bedrock_killswitch_syd" {
  count               = local.killswitch_count
  provider            = aws.syd
  name                = "bedrock-killswitch-poll"
  description         = "Poll Bedrock token volume and trip the kill-switch on a spike (ap-southeast-2)"
  schedule_expression = var.bedrock_killswitch_poll_rate
}

resource "aws_cloudwatch_event_target" "bedrock_killswitch_syd" {
  count     = local.killswitch_count
  provider  = aws.syd
  rule      = aws_cloudwatch_event_rule.bedrock_killswitch_syd[0].name
  target_id = "BedrockKillSwitch"
  arn       = aws_lambda_function.bedrock_killswitch_syd[0].arn
}

resource "aws_lambda_permission" "bedrock_killswitch_syd" {
  count         = local.killswitch_count
  provider      = aws.syd
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bedrock_killswitch_syd[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.bedrock_killswitch_syd[0].arn
}

# ---- 2c. Kill-switch poller: Melbourne (ap-southeast-4) ---------------------

resource "aws_lambda_function" "bedrock_killswitch_mel" {
  count            = local.killswitch_count
  provider         = aws.mel
  function_name    = "bedrock-killswitch"
  filename         = data.archive_file.bedrock_killswitch_zip.output_path
  source_code_hash = data.archive_file.bedrock_killswitch_zip.output_base64sha256
  role             = aws_iam_role.bedrock_killswitch.arn
  handler          = "bedrock_killswitch.lambda_handler"
  runtime          = "python3.11"
  timeout          = 60
  environment {
    variables = local.killswitch_env
  }
}

resource "aws_cloudwatch_event_rule" "bedrock_killswitch_mel" {
  count               = local.killswitch_count
  provider            = aws.mel
  name                = "bedrock-killswitch-poll"
  description         = "Poll Bedrock token volume and trip the kill-switch on a spike (ap-southeast-4)"
  schedule_expression = var.bedrock_killswitch_poll_rate
}

resource "aws_cloudwatch_event_target" "bedrock_killswitch_mel" {
  count     = local.killswitch_count
  provider  = aws.mel
  rule      = aws_cloudwatch_event_rule.bedrock_killswitch_mel[0].name
  target_id = "BedrockKillSwitch"
  arn       = aws_lambda_function.bedrock_killswitch_mel[0].arn
}

resource "aws_lambda_permission" "bedrock_killswitch_mel" {
  count         = local.killswitch_count
  provider      = aws.mel
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bedrock_killswitch_mel[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.bedrock_killswitch_mel[0].arn
}
