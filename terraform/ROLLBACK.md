# Rollback guide — cost guardrails

Everything added by the guardrail work is Terraform-managed and reversible.
There are two levels of rollback.

## What was deployed
| Control | How it's gated | Live effect |
|---|---|---|
| Cost-cap auto-shutdown | `dry_run`, `cost_cap_enabled` | Shuts down non-protected resources if MTD spend ≥ `monthly_cost_cap` ($500) |
| Bedrock model allow-list | `enable_bedrock_guardrail` | Denies InvokeModel on non-approved models for attached principals |
| Bedrock kill-switch | `enable_bedrock_killswitch`, `bedrock_killswitch_dry_run` | Token-spike alarm → Lambda denies Bedrock (dry-run = logs only) |

## Level 1 — fast disable (seconds, safe, keeps the code)
Detaches/removes all enforcement and returns the shutdown Lambda to dry-run.
Nothing gets stopped or denied afterwards.

```bash
cd terraform
terraform apply -var-file=terraform.tfvars -var-file=rollback.tfvars
```

Re-arm later with:
```bash
terraform apply -var-file=terraform.tfvars
```

Verify disabled:
```bash
aws lambda get-function-configuration --function-name aws-auto-shutdown \
  --query 'Environment.Variables.{DRY_RUN:DRY_RUN,COST_CAP_ENABLED:COST_CAP_ENABLED}'
# expect DRY_RUN=true, COST_CAP_ENABLED=false
aws iam list-attached-role-policies --role-name medical-llm-amplify-compute \
  --query "AttachedPolicies[?PolicyName=='bedrock-model-allowlist']"   # expect []
```

## Level 2 — full removal (delete all new resources + code)
Reverts to the exact pre-change deployment (origin/main): original shutdown
Lambda code, no cost cap, no Bedrock guardrail, age-based shutdown live.

```bash
# from the repo root
git checkout <pre-change-tag> -- terraform/    # tag: see `git tag | grep pre-implement`
cd terraform
terraform apply -var-file=terraform.tfvars
# then discard the guardrail commits on the branch, or git revert them
```

This destroys: the cost-cap event rule, the Bedrock allow-list/deny policies,
the kill-switch Lambdas/alarms/SNS, and reverts the Lambda code + IAM.

## Notes
- Level 1 is the emergency brake — instant, safe, no data loss.
- The Bedrock kill-switch ships in **dry-run** (logs only) until the token
  threshold is tuned, so it cannot deny anyone until deliberately armed.
- The model allow-list is **not attached** to apps that use Opus / Nova /
  DeepSeek (pdf-to-hl7, medaudit, pdf-to-coreplus) — attaching it there would
  break them. See the guardrail plan for the per-app decision.
