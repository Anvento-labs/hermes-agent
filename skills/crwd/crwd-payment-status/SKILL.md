---
name: crwd-payment-status
description: "Tell a CRWD member their payment status and history — whether a gig's payout has been sent via Dot, when to expect it, and what they've earned. Figures out which gig they mean, checks approval state, and reads live Dot payout status. Use when a member asks 'did I get paid?', 'where's my money?', 'when will I be paid?', or 'show my payment history'."
version: 1.1.0
metadata:
  hermes:
    tags: [crwd, payment, payout, dot, paid, money, history, earnings, status, platform, method]
    related_skills: [crwd-gig-discovery, crwd-gig-execution, crwd-reminders-followups, crwd-handoff, crwd-reference]
    requires_toolsets: [crwd, web]
---

# CRWD Payment Status

Answer "did I get paid?" against the member's **real** data: which gig, is it
approved yet, and what Dot says about the payout. Payments go out through **Dot**
(CRWD's payments partner) — the `dot` tool reads live payout status/history; the
`crwd_db` tool supplies the gig and approval context. Combine them.

## When to Use

- "Did I get paid for [gig]?" / "Where's my money?"
- "When will I be paid?" / "How long does payment take?"
- "Show my payment history." / "How much have I earned?"
- "How do I even get paid?" / "How does CRWD pay me?" / general uncertainty about
  whether/how the money will actually reach them.

## Lead with payment clarity

A member who doesn't understand *how* Dot pays them will hesitate on gigs or assume
they got scammed when a payout doesn't land instantly — this is the most important
thing this skill does. Before or alongside answering the specific status question:

- If the member seems new, unsure, or is asking "how does payment even work" rather than
  "where's my specific payout," **explain the flow proactively**: once approved, CRWD tells
  Dot to pay → Dot sends a Payout Link by email/SMS → the member claims it (verify phone,
  tax form if applicable) → picks a payout method → funds move on that method's rail.
  Full detail: `skill_view("crwd-reference", "references/payments-dot.md")`.
- **Ask which payout platform/method they use or plan to use** — bank transfer/debit card,
  PayPal, Venmo, Cash App, Payoneer, AirTM, etc. — whenever timing is the question. Timing
  varies a lot by method (minutes for PayPal/Venmo vs. several business days for
  international bank transfers), so this one follow-up question resolves most "when will I
  get paid" uncertainty instead of you giving a vague answer.
- Mention they can self-track any payout at **https://my.dots.dev** — Dot's own payee
  dashboard, independent of CRWD.
- If they want specifics on their method (e.g. "why is Venmo still pending?"), use
  `web_search`/`web_extract` to pull the relevant Dot Help Center article rather than
  guessing — see the reference links in `payments-dot.md`.

## Procedure

1. **Member `user_id`** comes from the `[CRWD member]` context line — pass it
   straight through to both `crwd_db` and `dot`. **Beta assumption:** CRWD and
   Dot ids are treated as the same value for now, so the same `user_id` goes
   to `dot` `get_user_transfers` too — never ask the member for a separate
   "Dot user ID" or try to look one up. Only use `crwd_db` `get_user` for a
   **different** person.
2. **Which gig?** If they're asking about a specific gig, resolve it first with
   `crwd_db` `get_gig_details` (confirm the `_id` when candidates are close) or
   `get_user_gigs`. For "all"/"history", skip this.
3. **Approval context (`crwd_db`)** — payment only flows **after** the work is
   approved. Check `get_user_gigs` (membership `hasPaid` / `isCompleted` /
   `status`) and, if useful, `get_user_receipts` (proof validation state). If a
   submission isn't approved yet, say that — there's nothing for Dot to send.
4. **Live payout (`dot`)** — once approved (or for a general history question):
   - list the member's transfers → `dot` `get_user_transfers` with `user_id`
     (the member's CRWD `user_id` — beta assumption is it's the same id Dot
     uses, so don't ask them for a separate one).
   - need one transfer in full → `dot` `get_transfer` with the `transfer_id` of
     the relevant transfer from that list.
5. **Answer plainly, in a line or two:** approved yet? → has Dot sent it? (method
   + date if shown). Quote the **real payout amount** from the gig data, not a
   guess.
6. **Framing** (`skill_view("crwd-reference", "references/payments-dot.md")`):
   payout ≠ reimbursement (they keep the product); once approved, Dot typically
   lands in **1–2 business days** — say *typical, not guaranteed*, never promise a
   date.
7. **If timing is the actual question and you don't already know their payout
   method, ask.** "Once it's sent, how fast it lands depends on how you're set up
   to get paid — bank transfer, PayPal, Venmo, Cash App, or something else?" Each
   rail has a very different typical window (see `payments-dot.md`); naming the
   right one turns a vague "1–2 business days" into a concrete, reassuring answer.
8. **If the `dot` tool is unavailable or errors, don't hand off — fall back:** give
   the approval state from `crwd_db` plus the honest "1–2 business days after
   approval" framing. Only **escalate to `crwd-handoff`** for a genuine dispute you
   can't resolve from the data: Dot shows the payout **sent but the member never
   received it**, a wrong/missing amount, a refund request, or a **rejected**
   submission. Don't guess about money that's supposedly already gone out.

## Pitfalls

- **Don't claim the money landed** unless Dot actually reports it sent/paid.
  "Approved" and "paid" are different states — read them separately.
- Approval gates payment. If it's not approved/completed, there's no payout yet —
  don't send them to check their bank.
- Don't invent timing. "Typically 1–2 business days after approval" is the only
  promise, and even that is *typical*.
- `get_gig_details` returns *candidates* — confirm the right `_id` before quoting
  a gig's payout.
- Money disputes and rejections are a human's job — hand off, don't improvise.
- Keep it short: this is a phone chat widget.
- Don't answer "how do I even get paid?" with just a transfer-status lookup — explain
  the Dot flow itself (see "Lead with payment clarity" above).
- Don't guess at Dot's per-method timing beyond what's in `payments-dot.md` — pull the
  specific Help Center article with `web_search`/`web_extract` if a member pushes on a
  method-specific delay.

## Verification

- Used the `[CRWD member]` `user_id` for both `crwd_db` and `dot` (same id,
  beta assumption — never asked the member for a separate Dot user ID).
- Confirmed the right gig `_id` when the question was about a specific gig.
- Separated approval state (`crwd_db`) from Dot's payout state (`dot`) — didn't
  conflate "approved" with "paid".
- Quoted the real payout amount and framed timing as *typical*, not guaranteed.
- Handed off on Dot errors, "sent but not received" disputes, or rejections.
- If the member was unsure how payment works at all, you explained the Dot flow
  (approval → Payout Link → claim → pick method → funds move), not just the raw status.
- If timing was the real question, you asked which payout method they use rather than
  giving one generic timeframe for every method.
