# Provider spend caps — checklist (non-AWS)

Companion to `AWS_COST_GUARDRAILS.md`. AWS is covered by the deployed guardrails
(cost-cap auto-shutdown + Bedrock kill-switch). Every other API provider the
org uses needs its cap set in the provider's own dashboard — none of them can
be capped from our side. Sorted by expected impact.

## OpenRouter (primary LLM provider — most projects)
- [ ] Switch to / stay on **prepaid credits**; keep the balance low (e.g. $50–100)
- [ ] **Turn OFF auto-top-up** (Settings → Credits). With auto-top-up on, a
      runaway drains the card, not just the balance — this is the single most
      important setting
- [ ] Set per-key spend limits on each API key (Keys → edit → credit limit),
      one key per project so a runaway is attributable
- Effect: hard stop when credits run out. Worst case = current balance.

## Google / Gemini (GCP)
- [ ] Confirm which GCP project bills Gemini usage; set a **Budget** with
      alerts at 50/90/100% (Billing → Budgets & alerts)
- [ ] Budgets only alert by default — for a hard stop, link the budget to a
      pub/sub + disable-billing function, or keep keys restricted instead
- [ ] **API key restrictions** on every key: restrict to the Generative
      Language API only + application restriction (IP/referrer)
- Note: the leaked website-chat key is confirmed **revoked** (API returns
  API_KEY_INVALID); traffic now goes through the Lambda proxy with rate limiting.

## OpenAI (if any workspace keys active)
- [ ] Set the **monthly budget limit** (platform.openai.com → Settings →
      Limits) — this is a true hard cap; requests fail once reached
- [ ] Set the notification threshold below it (e.g. 80%)

## Azure OpenAI (medical carve-out projects)
- [ ] No native hard cap — set an **Azure budget + action group** on the
      resource group, and keep TPM (tokens-per-minute) quota on each
      deployment at the minimum that serves the workload
- Effect: TPM quota is the real brake here; keep it low.

## ElevenLabs (TTS)
- [ ] Subscription tier itself is the cap — confirm **usage-based billing /
      overage is OFF** (Subscription → usage settings) so it hard-stops at the
      plan's character quota

## HeyGen (avatar video)
- [ ] Plan credits are the cap — confirm **no auto-purchase of extra credits**
- [ ] Remove unused API keys

## General (all providers)
- One key per project, named after the project — never share keys across apps
- Keys live server-side only (Amplify env vars / Lambda), never in client
  bundles (the website-chat lesson)
- Calendar reminder: review each dashboard's spend monthly until this is habit
