# Stopping Users From Blowing Out AWS Costs — Research & Recommendations

**Prepared by:** Sean O'Reilly · **Date:** 1 July 2026
**Account audited:** `843448676102` (SMEC AI / Boab AI) · single standalone account, region default `ap-southeast-2`
**Context:** Andrew's request to make sure every API has spend limits so we can't get "a $1m bill for something that went awry."

---

## TL;DR — the one thing to understand

**AWS has no hard dollar cap.** There is no setting that says "stop everything at $X." Billing is post-paid and usage-based, so anyone who says "just set a spending limit on AWS" is describing something that doesn't exist. (Confirmed against AWS billing docs — the only true per-service spend limit is a niche one for SMS messaging.)

You prevent blow-outs by **layering three types of control**:

1. **Prevent** — stop the expensive thing from being created at all (IAM guardrails, lowered quotas)
2. **Detect** — know within minutes/hours, not at end-of-month (budgets, anomaly detection, Bedrock logging)
3. **React** — auto-remediate when a threshold breaches (Budget Actions, kill-switch Lambda)

The closest thing to a "cap" is a **Budget Action**: when forecast/actual spend crosses a threshold, AWS auto-applies a deny policy and/or stops instances. That's our brake — but it only stops *new* activity, it doesn't refund what's already run.

---

## Where we stand today (live audit)

| Control | Status | Gap |
|---|---|---|
| **Cost Anomaly Detection** | ✅ Active (ML, daily) → emails andrew@boab.ai at ≥$100 AND ≥40% | Good. Consider adding SNS + more recipients |
| **AWS Budget** | ⚠️ One budget (`Budget 1`, auto-adjust ~$53/mo), single alert at $100 → shared@smecai.au | Alert-only, one threshold, no per-service budgets |
| **Budget Actions (enforcement)** | ❌ **None** | Nothing auto-stops anything. Smoke alarm, no sprinkler |
| **IAM guardrails** | ❌ **`BoabDevelopers` group = `AdministratorAccess` (`*:*`)** | All 5 devs (Sean, Krish, Himel, Shahzaib, Anshu) can launch any resource, any size, any region |
| **AWS Organization / SCPs** | ❌ Standalone account, not in an org | Strongest preventive guardrail (SCPs) unavailable until an org is created |
| **Service Quotas** | ⚠️ Defaults (not lowered) | No ceiling on how many/how large resources can be spun up |
| **Bedrock model-invocation logging** | ❌ **Off** in ap-southeast-2 | No visibility into which principal is burning LLM tokens |
| **Bedrock model restriction** | ❌ Any model invokable (incl. Opus) | No allow-listing of cheaper models |
| **Root MFA** | ❌ Not enabled (1 MFA device across 12 users) | Governance/security gap (noted, not strictly cost) |

**Current spend is small (~$50–77/mo)** — top lines are WorkSpaces (~$28), EC2-Other (~$11), WAF (~$8), VPC (~$7), Bedrock LLMs (~$8 and growing). **The risk is not today's bill — it's the blast radius.** Five unrestricted admins + service accounts + no enforcement means a mistake or a runaway loop can run for a day (until the anomaly email lands) with nothing stopping it.

---

## How a user actually blows out the bill (threat model)

1. **Accidental expensive resource** — someone launches a GPU/large instance (`p5.48xlarge` ≈ $100/hr), a NAT gateway per subnet, or resources in the wrong region and forgets them.
2. **Runaway automation / agent loop** — a script or AI agent in a retry loop. For an AI company the sharpest edge is **Bedrock**: an agent looping on Opus can burn thousands of dollars of tokens in hours with no native cap.
3. **Compromised credential** — a leaked access key → crypto-mining GPU fleet. This is the classic "$X0,000 overnight" story.

All three are *high* blast-radius today because access is admin-everywhere and there's no automated brake.

---

## The framework: Prevent → Detect → React

### Layer 1 — Prevent (stop it before it happens)

**a) Permission boundary on `BoabDevelopers`** — the highest-leverage single change.
A permissions boundary caps the *maximum* a principal can do: effective permissions = identity policy ∩ boundary. Even a user holding `AdministratorAccess` can only do what the boundary also allows. Keep devs productive, remove the cost blast-radius. The boundary should:

- Deny expensive/GPU/metal instance families:
```json
{ "Sid": "DenyExpensiveInstances", "Effect": "Deny", "Action": "ec2:RunInstances",
  "Resource": "arn:aws:ec2:*:843448676102:instance/*",
  "Condition": { "StringLike": { "ec2:InstanceType":
    ["p*","g*","x*","u*","*.metal","*.24xlarge","*.16xlarge","*.12xlarge","*.8xlarge"] } } }
```
- Pin regions (deny anything outside our approved list, exempting global services):
```json
{ "Effect": "Deny", "NotAction": ["iam:*","cloudfront:*","route53:*","support:*","organizations:*"],
  "Resource": "*",
  "Condition": { "StringNotEquals": { "aws:RequestedRegion":
    ["ap-southeast-2","ap-southeast-4","us-east-1","eu-west-1"] } } }
```
- Deny commitment purchases that create large recurring cost:
```json
{ "Effect": "Deny", "Action": [
    "ec2:PurchaseReservedInstancesOffering","savingsplans:CreateSavingsPlan",
    "bedrock:CreateProvisionedModelThroughput" ], "Resource": "*" }
```
- Gate `iam:CreateRole` / `iam:PutUserPermissionsBoundary` so devs can't strip the boundary off a new role.

**b) Lower Service Quotas** — a hard ceiling on *quantity*. Most cost-relevant: running On-Demand vCPUs (measured in vCPUs regardless of instance type — new-account default ≈ 5), GPU/accelerated vCPUs (keep at/near 0 until needed), Elastic IPs, NAT gateways, VPC count. You can request values *below* default.

**c) Bedrock model allow-listing** — block the expensive models via IAM:
```json
{ "Effect": "Allow", "Action": ["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
  "Resource": [
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-*",
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-*",
    "arn:aws:bedrock:*:843448676102:inference-profile/au.anthropic.claude-sonnet-4-*" ] }
```
Omitting Opus ARNs blocks it. (Cross-region calls hit `inference-profile/...` ARNs — grant those too, per the AU-profile → Melbourne routing we already handle.)

**d) (Strategic) AWS Organizations + SCPs** — the only *uneditable* guardrail (can't be removed even by root/admin). Creating an org (enable **all features**, put the workload account in an OU) unlocks preventive region/service/instance-type denies org-wide. Gotcha: SCPs don't apply to the management account, so keep workloads in a member account.

### Layer 2 — Detect (know fast)

- **Tiered budgets** — replace the single $100 alert with 50/75/90/100% thresholds, and add a **Bedrock-specific budget** (per-service filter) so LLM runaway shows up on its own line. First 2 budgets are free, then ~$0.02/budget/day.
- **Keep Cost Anomaly Detection** (already on, free) — optionally route to SNS and add more recipients than just andrew@.
- **Enable Bedrock model-invocation logging** (currently off) → CloudWatch, then alarm on `InputTokenCount` / `OutputTokenCount` / `Invocations`. This is what catches an agent loop in near-real-time instead of on the monthly bill.
- **Attribution — know *who*.** Activate user-defined **cost allocation tags** in Billing, and enable the new (2026) **Bedrock IAM principal-based cost allocation** in CUR, which records the calling user/role per request. (Caveat: behind an API Gateway/proxy the recorded principal is the gateway's role, not the end user.)

### Layer 3 — React (auto-stop)

- **Budget Actions** — the closest thing to a cap. On a **forecast** threshold (e.g. 90% of forecast) auto-apply an IAM deny policy to the dev group and/or **stop tagged EC2/RDS instances**. Start in *manual-approval* mode for a couple of cycles, then flip to automatic once trusted. IAM/SCP actions auto-reset next period; stopped instances stay stopped. First 2 action-enabled budgets free, then ~$0.10/day.
- **Bedrock kill-switch** — a Lambda triggered by the CloudWatch token alarm that detaches `bedrock:InvokeModel` from the offending principal. Bedrock has no native cap, so this is the only fast brake.
- **Extend the existing auto-shutdown Lambda** (this repo already does age-based EC2/RDS/NAT shutdown) to close the gaps — it's the natural home for the kill-switch and for stopping untagged/over-age resources.

---

## Recommended actions (prioritized)

### Quick wins — this week, ~1 hour, low risk
1. **Tiered budget alerts** (50/75/90/100%) + a **Bedrock-only budget**. Pure alerting, zero risk.
2. **Enable Bedrock model-invocation logging** + a CloudWatch alarm on token/invocation count. Visibility for the #1 AI cost vector.
3. **Add a Budget Action** at 90%-of-forecast that stops non-protected EC2 / applies a deny policy — **start in manual-approval mode**.

### Medium — this month
4. **Permission boundary on `BoabDevelopers`** — deny GPU/large/metal instance types, pin regions, deny commitment purchases + Bedrock Provisioned Throughput. Biggest blast-radius reduction; devs keep working normally.
5. **Lower Service Quotas** for On-Demand/GPU vCPUs, EIPs, NAT gateways.
6. **Bedrock model allow-list** (block Opus, allow Haiku/Sonnet) via IAM.

### Strategic — this quarter
7. **Move to AWS Organizations + SCPs** — uneditable guardrails + multi-account blast-radius isolation. Also enables SCP-type Budget Actions.
8. **Bedrock principal-based cost allocation** in CUR for true per-user attribution.
9. **Enforce MFA** (root + all IAM users) — closes the compromised-credential path.

---

## The broader ask (all APIs, not just AWS)

Andrew's original message covered **every** API. This document is AWS-only. The same Prevent/Detect/React model maps to the rest — and crucially, **most SaaS/LLM providers *do* offer the hard cap AWS lacks:**

| Provider | Hard cap available? | Lever |
|---|---|---|
| **OpenRouter** (our default) | ✅ Prepaid credits = built-in ceiling | Per-key spend limits; keep balance low, top up deliberately |
| **Anthropic / OpenAI direct** | ✅ Workspace/org monthly spend limits | Set hard monthly cap per workspace |
| **Azure OpenAI** | ⚠️ Quota (TPM) limits + Azure budget alerts | Cap TPM per deployment |
| **ElevenLabs / VAPI / HeyGen / video gen** | ⚠️ Usage/tier caps | Per-account plan limits; our video-gen already has a human approval gate |

**Recommendation:** treat this AWS doc as phase 1 of the org-wide review Andrew asked Sean to own. Phase 2 = inventory every provider + owner and confirm each has a hard cap or prepaid ceiling. I can produce that inventory next.

---

## Sources

- AWS billing limits (no hard cap): https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/billing-limits.html
- Budgets + Budget Actions: https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-controls.html
- Cost Anomaly Detection: https://docs.aws.amazon.com/cost-management/latest/userguide/manage-ad.html
- Service Quotas / EC2 resource limits: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html
- Organizations + SCPs: https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html
- Permissions boundaries: https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html
- Bedrock IAM policy examples: https://docs.aws.amazon.com/bedrock/latest/userguide/security_iam_id-based-policy-examples.html
- Bedrock cost management / inference profiles: https://docs.aws.amazon.com/bedrock/latest/userguide/cost-mgmt-application-inference-profiles.html
- Bedrock principal-based cost allocation (2026): https://aws.amazon.com/blogs/aws-cloud-financial-management/track-amazon-bedrock-costs-by-caller-identity-with-iam-based-cost-allocation/
- Control Tower proactive controls: https://docs.aws.amazon.com/controltower/latest/controlreference/controls.html
