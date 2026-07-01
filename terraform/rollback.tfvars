# EMERGENCY ROLLBACK (Level 1 - fast, safe, fully reversible)
#
# Disables every guardrail added by the "implement all" change and puts the
# shutdown Lambda back into dry-run. Nothing gets stopped, denied, or torn down
# beyond detaching/removing the new controls.
#
#   cd terraform
#   terraform apply -var-file=terraform.tfvars -var-file=rollback.tfvars
#
# Effect:
#   - cost-cap + age-based shutdown -> DRY_RUN (no real stops)
#   - Bedrock model allow-list      -> detached from all principals
#   - Bedrock kill-switch           -> alarms + Lambda removed
#
# To RE-ARM afterwards: terraform apply -var-file=terraform.tfvars  (alone)
# For FULL removal of all new code/resources: see ROLLBACK.md (Level 2).

dry_run                   = "true"
cost_cap_enabled          = false
enable_bedrock_guardrail  = false
enable_bedrock_killswitch = false
