---
name: chatwoot-conversation-labels
description: "Classify Chatwoot threads with support labels each turn."
version: 1.2.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [crwd, chatwoot, labels, classification, triage]
    related_skills: [crwd-handoff, crwd-payment-status, crwd-gig-execution, crwd-troubleshooting]
    requires_toolsets: [chatwoot]
---

# Chatwoot Conversation Labels

Internal triage only — classify each Chatwoot conversation with applied labels
so human agents can filter the inbox. **Never mention labels to the member.**

## When to Use

- **Every agent turn** on Chatwoot (after you understand the latest member
  message and thread context).
- Re-run classification when the topic shifts mid-conversation.

Don't use for: CLI, Telegram, or other non-Chatwoot platforms (`chatwoot_labels`
no-ops gracefully there).

## Quick Reference (applied)

| Signal | Label(s) |
|--------|----------|
| Any payment-related message | `payment-issue` |
| App navigation or broken UI | `app-help` |
| Member has not completed a gig yet (data-first) | `new-user` |
| All `store_proof` this turn accepted | `proof-acceptance` |
| Any `store_proof` this turn not accepted | `proof-rejection` |
| You called `crwd_handoff` this turn | `handoff-escalation` |
| This turn's `store_proof` set `is_gig_completed` | `gig-complete` |
| Fraud risk band | `risk-low` … `risk-critical` (skill) |

Unapplied titles (`mid-gig-support`, `proof-submission`, `gig-discovery`,
`general-inquiry`, `payment-payout`, `account-eligibility`, `account-info`,
`scam`, `off-topic`) stay in code for future reactivation but are **not**
assigned and are **not** created on Chatwoot by bootstrap.

More examples: `skill_view("chatwoot-conversation-labels", "references/label-taxonomy.md")`.

## How auto-labeling works

Labels are **applied automatically** after each turn via a Chatwoot plugin hook
(`post_tool_call` + `post_llm_call`). **Do not** call `chatwoot_labels`
`assign_labels` during normal turns — the end-of-turn hook replaces labels.

- **Intent (applied):** `payment-issue`, `app-help` from member text (LLM acts + heuristics).
- **Data-first:** `new-user` while the member has not completed ≥1 gig (required
  proofs accepted). Payment status does not matter. Unknown DB → skip (no guess).
- **Hard tools:** `crwd_handoff` → `handoff-escalation`; this-turn `store_proof`
  → `proof-acceptance` / `proof-rejection` / `gig-complete` (when
  `is_gig_completed`).
- **Preserved:** `risk-*` survive replace. `handoff-escalation` is kept only
  while conversation status is `open`; cleared when status is no longer `open`
  (bot owns again).

`create_labels_if_not_exists` bootstraps **applied** titles only.

## Procedure (every turn)

1. **Bootstrap** (optional): `chatwoot_labels` `action=create_labels_if_not_exists`.
2. **Hand off when needed** — `handoff-escalation` is added **only** when you
   call `crwd_handoff`.
3. **Do not mention labels to the member** — internal triage only.

## Multi-label examples

- Payout late + page won't load → `["payment-issue", "app-help"]` (+ `new-user` if applicable)
- Rejected proof this turn + handoff → `["proof-rejection", "handoff-escalation"]`
- All proofs accepted this turn → `["proof-acceptance"]` (+ `gig-complete` when
  `is_gig_completed` this turn)

## Common Pitfalls

1. **Expecting handoff label without calling `crwd_handoff`** — the tag follows
   the tool, not member frustration text alone.
2. **Calling `assign_labels` every turn** — the auto hook already replaces; manual
   assign races and can wipe preserved state labels if misused.
3. **Mentioning labels to the member** — internal only.
4. **Expecting unapplied titles** — they will not appear on conversations.
