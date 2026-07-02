# --- Bedrock cost guardrail: model allow-list + runaway kill-switch ---

variable "enable_bedrock_guardrail" {
  description = "Attach the Bedrock model allow-list (deny non-approved models) to the configured principals."
  type        = bool
  default     = false
}

variable "enable_bedrock_killswitch" {
  description = "Deploy the Bedrock token-spike CloudWatch alarms + kill-switch Lambda (both AU regions)."
  type        = bool
  default     = false
}

variable "bedrock_killswitch_dry_run" {
  description = "If true, the kill-switch only LOGS what it would deny (no policy attached). Start true."
  type        = bool
  default     = true
}

variable "bedrock_principal_roles" {
  description = "IAM role names that invoke Bedrock (Amplify compute roles). Allow-list + kill-switch target these."
  type        = list(string)
  default = [
    "medaudit-amplify-compute",
    "pdf-to-coreplus-amplify-compute",
    "AmplifyComputeRole-ddv0o3k8wcjhr", # pdf-to-hl7
    "medical-llm-amplify-compute",
    "docmediflow-amplify-compute",
  ]
}

variable "bedrock_principal_users" {
  description = "IAM user names holding static Bedrock keys. TODO: confirm + add genotype-health's key user."
  type        = list(string)
  default = [
    "remede-wellness-bedrock-service",
  ]
}

variable "bedrock_allowed_model_keywords" {
  description = "Model-name keywords ALLOWED to be invoked. Anything else (e.g. Opus) is denied. Cheap tier only."
  type        = list(string)
  default     = ["haiku", "sonnet"]
}

variable "bedrock_token_alarm_threshold" {
  description = "Kill-switch trips if (input+output) Bedrock tokens in one period exceed this. Tuned 2026-07-02: 28-day peak 5-min bucket was 62,346 tokens (Sydney); 500k = ~8x headroom, ~$500+/day if sustained."
  type        = number
  default     = 500000
}

variable "bedrock_alarm_period_seconds" {
  description = "Metric window (seconds) the kill-switch sums Bedrock tokens over each poll."
  type        = number
  default     = 300
}

variable "bedrock_killswitch_poll_rate" {
  description = "EventBridge schedule for how often the kill-switch polls Bedrock token volume."
  type        = string
  default     = "rate(5 minutes)"
}
