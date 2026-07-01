"""Bedrock cost kill-switch (scheduled poller).

Runs on an EventBridge schedule (per AU region). Each run it sums Bedrock
input+output tokens across ALL models over the recent window via CloudWatch
GetMetricData (which, unlike metric alarms, supports SEARCH expressions). If the
total exceeds TOKEN_THRESHOLD it attaches a deny-InvokeModel policy to every
configured Bedrock principal, halting further spend within ~one poll interval —
the fastest hard stop AWS allows for Bedrock (there is no native $ cap).

DRY_RUN=true logs what it WOULD deny without changing anything.
Re-enable after a trip is manual: detach the deny policy from each principal.
"""
import os
from datetime import datetime, timezone, timedelta

import boto3

DENY_POLICY_ARN = os.environ["DENY_POLICY_ARN"]
TARGET_ROLES = [r for r in os.environ.get("TARGET_ROLES", "").split(",") if r]
TARGET_USERS = [u for u in os.environ.get("TARGET_USERS", "").split(",") if u]
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
TOKEN_THRESHOLD = float(os.environ.get("TOKEN_THRESHOLD", "2000000"))
PERIOD_SECONDS = int(os.environ.get("PERIOD_SECONDS", "300"))

iam = boto3.client("iam")
sns = boto3.client("sns")
cw = boto3.client("cloudwatch")  # this Lambda's own region


def get_recent_bedrock_tokens():
    """Sum input+output Bedrock tokens across all models over the last period."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=PERIOD_SECONDS * 3)
    expr = (
        "SUM(SEARCH('{AWS/Bedrock,ModelId} MetricName=\"InputTokenCount\"', 'Sum', %d)) + "
        "SUM(SEARCH('{AWS/Bedrock,ModelId} MetricName=\"OutputTokenCount\"', 'Sum', %d))"
    ) % (PERIOD_SECONDS, PERIOD_SECONDS)
    resp = cw.get_metric_data(
        MetricDataQueries=[{"Id": "total", "Expression": expr, "ReturnData": True}],
        StartTime=start,
        EndTime=end,
    )
    values = resp["MetricDataResults"][0].get("Values", [])
    return max(values) if values else 0.0


def trip(tokens):
    results = []
    for role in TARGET_ROLES:
        try:
            if DRY_RUN:
                results.append(f"[dry-run] would deny Bedrock on role {role}")
            else:
                iam.attach_role_policy(RoleName=role, PolicyArn=DENY_POLICY_ARN)
                results.append(f"DENIED Bedrock on role {role}")
        except Exception as e:  # noqa: BLE001
            results.append(f"ERROR role {role}: {e}")
    for user in TARGET_USERS:
        try:
            if DRY_RUN:
                results.append(f"[dry-run] would deny Bedrock on user {user}")
            else:
                iam.attach_user_policy(UserName=user, PolicyArn=DENY_POLICY_ARN)
                results.append(f"DENIED Bedrock on user {user}")
        except Exception as e:  # noqa: BLE001
            results.append(f"ERROR user {user}: {e}")

    msg = (
        f"BEDROCK KILL-SWITCH TRIGGERED\n"
        f"Recent Bedrock tokens: {tokens:,.0f} (threshold {TOKEN_THRESHOLD:,.0f})\n"
        + ("DRY RUN - no changes made\n" if DRY_RUN else "Bedrock InvokeModel DENIED for all configured principals.\n")
        + "\n".join(f"  - {r}" for r in results)
        + f"\n\nRe-enable: detach policy\n  {DENY_POLICY_ARN}\nfrom each role/user above once the runaway is resolved."
    )
    print(msg)
    if SNS_TOPIC_ARN:
        try:
            sns.publish(TopicArn=SNS_TOPIC_ARN, Subject="BEDROCK KILL-SWITCH TRIGGERED", Message=msg)
        except Exception as e:  # noqa: BLE001
            print(f"SNS publish failed: {e}")
    return results


def lambda_handler(event, context):
    # 'test_tokens' lets you force a trip for a safe (dry-run) test.
    tokens = float(event["test_tokens"]) if isinstance(event, dict) and "test_tokens" in event else get_recent_bedrock_tokens()

    if tokens < TOKEN_THRESHOLD:
        print(f"Bedrock tokens {tokens:,.0f} under threshold {TOKEN_THRESHOLD:,.0f} - no action")
        return {"statusCode": 200, "tripped": False, "tokens": tokens, "threshold": TOKEN_THRESHOLD}

    results = trip(tokens)
    return {"statusCode": 200, "tripped": True, "dry_run": DRY_RUN, "tokens": tokens, "results": results}
