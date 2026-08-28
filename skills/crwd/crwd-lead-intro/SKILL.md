---
name: crwd-lead-intro
description: "Coach a gig-interest lead using real CRWD data."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [crwd, leads, intro, coach, gig, interest]
    related_skills: [crwd-gig-discovery, crwd-application-expert]
    requires_toolsets: [crwd]
---

# CRWD Lead Intro

Proactive first Coach message after a trusted lead POST. The briefing in this
turn is ingest facts, not something the member typed. Chatwoot only sees your
outgoing reply. Do not use a frozen SMS template.

## When to Use

- This turn was started by lead ingest (facts block with `gig_id` / `user_created`).
- Not for ordinary inbound member messages (use `crwd-gig-discovery` then).

## Prerequisites

- `crwd_db` is available. Use `user_id` from `[CRWD member]` context or the facts
  block `crwd_user_id`.
- The Chatwoot thread is already the current session. Do not pick another inbox.

## How to Run

Call `crwd_db` **this turn** before you write. Then send **one** customer-visible
reply.

## Procedure

1. Load the gig: `get_gig_details` (or equivalent) with facts `gig_id`. Quote
   only fields the tool returned (name, payout, dates, next step). If the gig is
   unknown, say you cannot find that gig — do not invent it.
2. Load the member: `get_user_gig_status` with `user_id` — the brand-new interest
   row is included by default. Treat `user_created: true` as a new CRWD account
   just created for this lead; `user_created: false` as an **existing** member.
3. Write one Coach-voiced reply:
   - **Existing member** (`user_created` false): do not "welcome them to CRWD"
     as if they are new. Acknowledge this gig interest against their real
     status and the next real step.
   - **New member** (`user_created` true): short welcome plus this gig's real
     next step from tools.
4. Stop after one reply. Later inbound messages continue on the normal Coach
   path (`crwd-gig-discovery`, etc.).

## Pitfalls

- Do not quote the lead-ingest facts block or mention Lovable / webhooks / tokens.
- Do not invent enrollment, acceptance, or payout.
- Do not send a second message or a canned signup SMS — they already have a
  CRWD user from this POST.
- Do not call `crwd_handoff` unless tools show a real handoff trigger.
- One reply only.

## Verification

Done when this turn used `crwd_db` for the given `gig_id` and `user_id`, and
the member-facing text matches new vs existing (`user_created`) without a
frozen template.
