"""Bedrock cost kill-switch.

Triggered by a CloudWatch alarm (via SNS) when Bedrock token volume spikes.
Attaches a deny-InvokeModel managed policy to every configured Bedrock principal
(Amplify compute roles + static-key IAM users), halting further Bedrock spend
within ~minutes — the fastest hard stop AWS allows for Bedrock (there is no
native instant dollar cap).

Re-enable is deliberate/manual: detach the deny policy from each principal once
the cause is understood (see the runbook line in the SNS alert).
"""
import os
import boto3

DENY_POLICY_ARN = os.environ["DENY_POLICY_ARN"]
TARGET_ROLES = [r for r in os.environ.get("TARGET_ROLES", "").split(",") if r]
TARGET_USERS = [u for u in os.environ.get("TARGET_USERS", "").split(",") if u]
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

iam = boto3.client("iam")
sns = boto3.client("sns")


def lambda_handler(event, context):
    results = []
    for role in TARGET_ROLES:
        try:
            if DRY_RUN:
                results.append(f"[dry-run] would deny Bedrock on role {role}")
            else:
                iam.attach_role_policy(RoleName=role, PolicyArn=DENY_POLICY_ARN)
                results.append(f"DENIED Bedrock on role {role}")
        except Exception as e:  # noqa: BLE001 - report every principal, don't abort
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
        "BEDROCK KILL-SWITCH TRIGGERED\n"
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

    return {"statusCode": 200, "dry_run": DRY_RUN, "results": results}
