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
| Browse/apply gigs, CRWD overview | `gig-discovery` |
| Proof / receipt / submit (any enrollment) | `proof-submission` |
| Enrolled-gig help or enrolled + proof | `mid-gig-support` (+ `proof-submission` when proof) |
| Paid? when? payout history | `payment-payout` |
| App navigation or broken UI | `app-help` |
| Not eligible / can't join / wrong state / age | `account-eligibility` |
| Account status, membership, ban/suspension | `account-info` |
| Phishing / wire / bitcoin / gift-card scam signals | `scam` |
| Non-CRWD requests | `off-topic` |
| You called `crwd_handoff` this turn | `handoff-escalation` (+ topic label) |

Opt-out / stop-contact alone (`stop texting`, `unsubscribe`, `remove me`) is
**not** a topic label — it falls through to `off-topic` (or sticky) unless
you hand off via `crwd_handoff`.

More examples: `skill_view("chatwoot-conversation-labels", "references/label-taxonomy.md")`.

## How auto-labeling works

Labels are **applied automatically** after each turn via a Chatwoot plugin hook
(`post_tool_call` + `post_llm_call`). You do not need to call `chatwoot_labels`
for normal triage. Optionally call the tool to **override** auto-classification.

Priority:

1. **Tools you called this turn** (highest confidence) — e.g. `crwd_db`
   `list_active_gigs` → `gig-discovery`; `get_waitlisted_gigs` /
   `get_user_gigs` → `mid-gig-support`; `get_user_receipts` → `proof-submission`;
   `dot` → `payment-payout`; `crwd_handoff` → `handoff-escalation`.
2. **Member-message heuristics** when tools don't map clearly.
3. On a **clear topic switch** (high confidence), previous labels are
   **replaced** — e.g. `app-help` drops when the next turn is `gig-discovery`.
4. Ambiguous short replies may keep the previous topic until the next clear turn.

There is **no per-turn numeric label cap** — every matching predefined label may
apply (taxonomy size is the soft bound).

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
   turns replace the label set (tool-backed topic changes drop stale tags).

## Verification Checklist

- [ ] Member-facing reply sent (labels applied in background)
- [ ] On handoff, you called `crwd_handoff` (label added automatically)
- [ ] Member was not told about labels
