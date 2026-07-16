---
name: crwd-proof-validator
description: "Reviews a gig proof, scores risk, and replies a verdict."
version: 0.2.0
metadata:
  hermes:
    tags: [crwd, proof, receipt, review, validation, approval, risk, gig, submission]
    related_skills: [crwd-gig-execution, crwd-payment-status, crwd-handoff, crwd-reference]
    requires_toolsets: [crwd]
---

# CRWD Proof Validator

Reacts whenever a member submits **any proof** for a gig — a receipt, an order
screenshot, a review screenshot, a review link, a UGC content link, or any other
file/attachment — **directly in this chat conversation as a message**, checks it
for the risk scenarios below, adjusts the member's risk score with
`crwd_risk_score`, and replies with the verdict. This skill owns **the reply** to
a proof submission — no other `crwd-*` skill should answer on its behalf. It does
**not** move payout/gig state in `crwd_db`/Dot.

## When to Use

- A member attaches or uploads any proof (image, screenshot, PDF, link, etc.) in
  the same message as, or immediately following, a gig submission — e.g. "here's my
  receipt", "uploading my proof", "here's my review", "does this work?", or a bare
  attachment/link with no comment while a gig is in progress.
- Any message where the member is clearly submitting proof for a gig (receipt,
  order, review, UGC content), regardless of format or quality.

Do **not** use this skill for:
- Payment/payout questions after a proof is already approved — that's
  `crwd-payment-status`.
- Walking a member through *how* to buy the product or *what* proof to submit
  before they've submitted anything — that's `crwd-gig-execution`.

## Prerequisites

- Chatwoot creds configured so `crwd_risk_score` and `crwd_handoff` can act on the
  current member. When Chatwoot is not configured both tools no-op safely — still
  reply to the member.
- The `crwd` toolset (already required via `requires_toolsets`).

## How to Run

**Hard rule: never approve a receipt you have not read.** Before any verdict you
MUST call `vision_analyze` on the attachment (image/screenshot) — or `read_file`
for a PDF — and extract the order/transaction number. Do not reply "approved" (or
acknowledge the proof as accepted) until you have the order number in hand and
have checked it against the **Known duplicate order IDs** list. A blurry or
unreadable receipt is NOT an approval — flag it (`crwd_handoff`).

1. Call `vision_analyze` on each attachment URL (from the message's media) with a
   prompt asking for: order/transaction number (Target `REC#`, Amazon `Order #`),
   merchant, SKUs / product names, quantities, totals, and timestamp. Use
   `read_file` for PDFs/text.
2. **Check the order number against the Known duplicate order IDs list first.** If
   it matches (compare digits only, ignoring the `REC#`/`Order #` prefix and
   spacing) → it is a duplicate → score the duplicate delta, auto-reject, and
   `crwd_handoff`. Do not approve.
3. Otherwise match the extracted fields against the remaining **Risk Scenarios**
   and pick the single most severe matching scenario.
4. Call `crwd_risk_score` **once** with the scenario's `delta` and a short `reason`.
   The tool reads the current score, adds `delta`, and clamps to 0-100 — you only
   pass the increment, never an absolute value.
5. Reply with the verdict (approved / flagged / rejected). On rejection or a
   high-risk score, also call `crwd_handoff` — a human owns rejections.

## Quick Reference

| Scenario | delta | Verdict |
|---|---|---|
| Duplicate receipt (order id in hardcoded list) | +15..20 (1st), +30..40 (2nd), +40..50 (3rd+) | Auto-reject + handoff |
| Fake / edited receipt | +85..95 | Reject + handoff |
| Repeated validation failures | +65..95 | Reject + handoff |
| Wrong product (SKU / fuzzy-name mismatch) | +15..20 (1st), +30..40 (2nd), +40..50 (3rd+) | Validation failure, not fraud |
| Wrong quantity (fewer items than required) | 0..15 | Flag, allow manual override |
| Clean, matching proof | 0 | Approved |

## Known duplicate order IDs

If the submitted receipt's order/transaction number matches any value below, treat
it as a **duplicate** — score with the duplicate delta and auto-reject. This list
is maintained inline; add order ids as they are confirmed handled.

Order numbers appear as the Target `REC#` line or the Amazon `Order #`. Match on
the digits, ignoring the `REC#`/`Order #` prefix and any spacing.

```
# order ids already handled elsewhere (duplicate = auto-reject)
# Target receipts (REC#)
2-6177-0190-0173-4723-7
2-6177-0190-0173-4722-9
2-6177-0190-0173-4724-5
2-6172-2275-0172-6193-1
# Amazon order
112-2229469-0480212
```

## Procedure

1. **Extract signals from the proof.** Use `vision` / `read_file` to pull the
   order number, merchant, SKUs/product names, quantities, totals, and timestamp.
   If nothing is legible, do not guess — flag for manual review (`crwd_handoff`).

2. **Duplicate receipt.** Signals: the order number matches the hardcoded list
   above; identical image; same transaction timestamp + amount; same SKU
   combination. This is escalation-based on how many times this member has
   duplicated: `+15..20` first time, `+30..40` second, `+40..50` third+. Always
   **auto-reject** and `crwd_handoff`.

3. **Fake / edited receipt.** Signals: altered totals, a fabricated order page, a
   Canva-style "receipt", mismatched fonts/spacing, inconsistent math. Score
   `+85..95`, reject, and `crwd_handoff`.

4. **Repeated validation failures.** Signals: approval rate below threshold, a
   history of rejected submissions, repeated failures across campaigns (check
   `crwd_db` `get_user_receipts` for prior pass/fail state). Score `+65..95`,
   reject, and `crwd_handoff`.

5. **Wrong product.** Signals: SKU mismatch, fuzzy product-name mismatch vs the
   campaign requirement, quantity mismatch. This is usually confusion, not fraud:
   route to a **validation failure**, not an immediate fraud rejection. Score
   `+15..20` first time, `+30..40` second, `+40..50` third+.

6. **Wrong quantity.** Signals: quantity mismatch, incomplete bundle (e.g. bought
   1 of a required 3). Often the item was sold out or unavailable. Score `0..15`,
   **flag but allow a manual override / pre-approval** — do not reject outright.

7. **Clean proof.** If the receipt matches the campaign (right merchant, right
   SKUs, right quantity) and trips no signal, score `0` and reply approved.

8. **Persist the score, then reply.** Call `crwd_risk_score(delta=<points>,
   reason="<scenario>")` once, then send the member the verdict. Keep the reply
   short (this is a chat widget) and never mention the risk score.

## Pitfalls

- **Never approve without reading.** Don't say "approved" or acknowledge a receipt
  as accepted before you've run `vision_analyze` and checked the order number
  against the duplicate list. Acknowledging receipt ≠ approving it.
- Don't pass an absolute score to `crwd_risk_score` — it only takes a `delta` and
  clamps to 0-100 itself.
- Don't score the same submission twice — one `crwd_risk_score` call per proof.
- Don't tell the member their risk score, that they were "flagged", or that a
  human is reviewing fraud — reply with a plain verdict and let `crwd_handoff` do
  the internal notification.
- Don't reject on a wrong-quantity or wrong-product case as if it were fraud —
  those route to validation failure / manual override, not immediate rejection.
- Don't claim the payout has been triggered or that money is on the way — this
  skill only scores and acknowledges the proof; it doesn't touch payment state.
- Don't self-diagnose a rejection reason to the member — rejections go to a human
  via `crwd_handoff`. Payment-status questions go to `crwd-payment-status`.

## Verification

- Every proof submission got exactly one `crwd_risk_score` call with a `delta`
  matching the most severe scenario that applied (or `0` for clean proof).
- Duplicate / fake / repeated-failure submissions were rejected and handed off;
  wrong-quantity and wrong-product cases were flagged/validation-failed, not
  fraud-rejected.
- No message revealed the risk score or claimed payout was triggered.
- Follow-up payment questions were routed to `crwd-payment-status`.
