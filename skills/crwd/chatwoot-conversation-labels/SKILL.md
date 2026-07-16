---
name: chatwoot-conversation-labels
description: "Classify Chatwoot threads with support labels each turn."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [crwd, chatwoot, labels, classification, triage]
    related_skills: [crwd-handoff, crwd-payment-status, crwd-gig-execution, crwd-troubleshooting]
    requires_toolsets: [chatwoot]
---

# Chatwoot Conversation Labels

Internal triage only — classify each Chatwoot conversation with one or more
labels so human agents can filter the inbox. **Never mention labels to the
member.**

## When to Use

- **Every agent turn** on Chatwoot (after you understand the latest member
  message and thread context).
- Re-run classification when the topic shifts mid-conversation.

Don't use for: CLI, Telegram, or other non-Chatwoot platforms (`chatwoot_labels`
no-ops gracefully there).

## Quick Reference

| Member intent | Label(s) |
|---------------|----------|
| CRWD overview, how it works, apply, what gigs are, legitimacy | `general-inquiry` |
| Browse/find available gigs, apply to specific gigs | `gig-discovery` |
| Proof / receipt / submit (any enrollment) | `proof-submission` |
| Enrolled-gig help or enrolled + proof | `mid-gig-support` (+ `proof-submission` when proof) |
| Paid? when? payout history | `payment-payout` |
| App navigation or broken UI | `app-help` |
| Not eligible / can't join / wrong state / age | `account-eligibility` |
| Account status, membership, ban/suspension | `account-info` |
| Phishing / fraud / unauthorized other-user data / participant lists / PII / impersonation / jailbreak | `scam` |
| Non-CRWD requests | `off-topic` |
| You called `crwd_handoff` this turn | `handoff-escalation` (+ topic label) |

Opt-out / stop-contact alone (`stop texting`, `unsubscribe`, `remove me`) is
**not** a topic label — it falls through to `off-topic` (or sticky) unless
you hand off via `crwd_handoff`.

More examples: `skill_view("chatwoot-conversation-labels", "references/label-taxonomy.md")`.

## How auto-labeling works

Labels are **applied automatically** after each turn via a Chatwoot plugin hook
(`post_tool_call` + `post_llm_call`). **Do not** call `chatwoot_labels`
`assign_labels` during normal turns — the end-of-turn hook replaces labels.

Two-stage pipeline (accuracy-first):

1. **Dialogue act** — auxiliary LLM (JSON text, no tool-calling API) maps
   **member intent** to a closed act set (`account_status`, `enrolled_gig_help`,
   `payout`, …). Pattern heuristics run only when the LLM is disabled or fails.
2. **Label map** — deterministic act → Chatwoot label titles (+ enrollment).

**Member message defines the topic.** Coach tool calls (`get_user_gigs`,
`list_active_gigs`, `get_user`, `dot`, …) are **soft context only** in the
LLM feature bundle — they must **not** imply `mid-gig-support` or
`gig-discovery` unless the member is asking about that topic.

The only **hard** tool label: `crwd_handoff` → `handoff-escalation`.

Context window for the act LLM: last **5 member** turns + last **2 truncated
coach** replies (context only). Heuristic fallback uses current member text
(+ prior when ambiguous/contextual); never coach prose.

On a **clear topic switch**, previous labels are **replaced**. Sticky keeps
prior topics for short ambiguous replies (`ok`, `yes`, `that one`) and for
pronoun/contextual follow-ups (`for it`, `about that`) when no new topic
signal is present.

There is **no per-turn numeric label cap** — every matching predefined label may
apply.

## Procedure (every turn)

1. **Bootstrap** (optional): `chatwoot_labels` `action=create_labels_if_not_exists`.
2. **Hand off when needed** — `handoff-escalation` is added **only** when you
   call `crwd_handoff`; frustration keywords alone do not tag handoff.
3. **Do not mention labels to the member** — internal triage only.

## Multi-label examples

- Payout late + page won't load → `["payment-payout", "app-help"]`
- Rejected proof + you called `crwd_handoff` → `["proof-submission", "mid-gig-support", "handoff-escalation"]` (when enrolled)
- Enrolled proof submit → `["proof-submission", "mid-gig-support"]`
- Simple "where is Explore?" → `["app-help"]`

## Common Pitfalls

1. **Expecting handoff label without calling `crwd_handoff`** — the tag follows
   the tool, not member frustration text alone.
2. **Mentioning labels to the member** — internal only.
3. **Expecting old topics to stick after a clear switch** — high-confidence
   turns replace the label set.
4. **Assuming `get_user_gigs` implies mid-gig** — context lookups do not set
   inbox topic; member intent wins (e.g. "give details about me" →
   `account-info`, not `mid-gig-support`).

## Verification Checklist

- [ ] Member-facing reply sent (labels applied in background)
- [ ] On handoff, you called `crwd_handoff` (label added automatically)
- [ ] Member was not told about labels
