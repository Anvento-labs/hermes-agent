---
name: crwd-receipt-validator
description: "Reviews a submitted gig receipt and replies with a verdict."
version: 0.1.0
metadata:
  hermes:
    tags: [crwd, receipt, proof, validation, approval, beta, gig, submission]
    related_skills: [crwd-gig-execution, crwd-payment-status, crwd-handoff, crwd-reference]
    requires_toolsets: [crwd]
---

# CRWD Receipt Validator

Reacts whenever a member submits a receipt/proof file (photo, screenshot, or other
attachment) for a gig, and replies with the validation verdict. This skill owns
**the reply** to a receipt submission — no other `crwd-*` skill should answer on
its behalf.

## ⚠️ Beta status

This skill is in **beta**. There is no real validation logic yet: whatever file is
submitted, of whatever type or quality, is treated as accepted for testing
purposes. Do **not** describe this to members as "real" review — see wording rules
below.

## When to Use

- A member attaches or uploads a file (image, screenshot, PDF, etc.) in the same
  message as, or immediately following, a gig submission — e.g. "here's my
  receipt", "uploading my proof", "does this receipt work?", or a bare attachment
  with no comment while a gig is in progress.
- Any message where the member is clearly submitting proof of purchase for a gig,
  regardless of format or quality.

Do **not** use this skill for:
- Payment/payout questions after a receipt is already approved — that's
  `crwd-payment-status`.
- Walking a member through *how* to buy the product or *what* proof format is
  needed before they've submitted anything — that's `crwd-gig-execution`.

## Procedure (beta behavior)

1. **Any file counts.** In this beta version, accept whatever is submitted — do
   not inspect the file's content, quality, or format. There is nothing to reject
   yet.
2. **Reply "approved."** Confirm the receipt was received and mark it approved,
   e.g.: *"Got your receipt — approved! ✅"* Keep it short, this is a chat widget.
3. **Don't fabricate downstream state.** Approving the receipt here is a reply to
   the member only — it does not change payout or gig status in `crwd_db`/Dot.
   If the member then asks about payment status, hand off to
   `crwd-payment-status` for the real approval/payout state rather than assuming
   your beta "approved" reply already moved money.
4. **If asked whether this is a real review**, be honest: this is an automated
   placeholder response while receipt validation is being built — a human or the
   full validation pipeline still does the real check.

## Pitfalls

- Don't claim the payout has been triggered or that money is on the way — this
  skill only acknowledges the file, it doesn't touch payment state.
- Don't pretend to inspect the receipt ("I can see the total is $12.99...") —
  no real validation is happening yet in beta.
- Don't let this skill answer payment-status questions — hand those to
  `crwd-payment-status`.
- If the member seems confused or upset about a *previous* rejection, don't
  reflexively re-approve here — that's `crwd-handoff` territory.

## Verification

- Every message containing a submitted receipt/proof file got a reply from this
  skill, and every reply said approved.
- No claims were made about real content validation or payout being triggered.
- Follow-up payment questions were routed to `crwd-payment-status`, not answered
  here.
