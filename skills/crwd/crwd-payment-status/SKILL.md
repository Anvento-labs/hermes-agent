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
   straight through to `crwd_db`. Never ask the member for a "Dot user ID".
   Only use `crwd_db` `get_user` for a **different** person.
2. **Which gig?** If they're asking about a specific gig, resolve it first with
   `crwd_db` `get_gig_details` (confirm the `_id` when candidates are close) or
   `get_user_gigs`. For "all"/"history", skip this.
3. **Approval context (`crwd_db`)** — payment only flows **after** the work is
   approved. Prefer `get_user_gig_status` here: `get_user_gigs` returns only gigs
   CRWD has formally accepted them into, so it misses a member whose proof is all
   in but whose acceptance hasn't been stamped yet
   (`proof_complete_pending_acceptance`). Treat that stage exactly like
   `awaiting_payout` — approved, payout pending — and describe it in the same
   words; the outstanding acceptance is internal and never goes in the reply.
   Check membership `hasPaid` / `isCompleted` / `status` and, if useful,
   `get_user_receipts` (proof validation state). If a submission isn't approved
   yet, say that — there's nothing for Dot to send.
4. **Live payout (`dot`)** — once approved (or for a general history question):
   - you need a **Dot `user_id`**, which is not the CRWD `user_id`. If you don't
     have one, `dot` `create_user` returns one — pass the member's real name /
     email / phone from `crwd_db` `get_user`, never a guess.
   - list the member's transfers → `dot` `get_user_transfers` with that Dot
     `user_id`.
   - need one transfer in full → `dot` `get_transfer` with the `transfer_id` of
     the relevant transfer from that list.
   - **An empty list from a just-created user proves nothing.** When
     `create_user` returns `user_is_new`, the id is brand new, so Dot has no
     history under it — that is not evidence the member wasn't paid. Say you
     can't see their payout history and hand off (`crwd-handoff`). Telling
     someone they weren't paid on this basis is the worst error you can make
     here.
   - If `create_user` fails saying the user already exists, the member has a Dot
     account this tool can't look up. Hand off — don't guess.
5. **Work out what each transfer was for — product funds or the payout.** CRWD
   sends money for two reasons and Dot has no field saying which. Do **not**
   classify off a transfer's `type: "payout"` — that only means money going out,
   and both kinds look like that — and remember Dot's amounts are in **cents**
   against CRWD's dollars. Read the text CRWD attached (`metadata`, or the payout
   link's `memo`), then match the amount against that gig's `effective_payout` vs
   `effective_product_funds`. If neither settles it, give the amount, date and
   status **without naming which payment it was**, and hand off if they need to
   know. Full ladder: `skill_view("crwd-reference", "references/payments-dot.md")`.
6. **Answer plainly, in a line or two:** approved yet? → has Dot sent it? (method
   + date if shown). Quote the **real payout amount** from the gig data, not a
   guess. If the gig carries product funds, keep the two amounts distinct — never
   merge them into one figure, and never call product funds their earnings.
7. **Framing** (`skill_view("crwd-reference", "references/payments-dot.md")`):
   payout ≠ reimbursement (they keep the product); once approved, Dot typically
   lands in **1–2 business days** — say *typical, not guaranteed*, never promise a
   date.
8. **If timing is the actual question and you don't already know their payout
   method, ask.** "Once it's sent, how fast it lands depends on how you're set up
   to get paid — bank transfer, PayPal, Venmo, Cash App, or something else?" Each
   rail has a very different typical window (see `payments-dot.md`); naming the
   right one turns a vague "1–2 business days" into a concrete, reassuring answer.
9. **If the `dot` tool is unavailable or errors, don't hand off — fall back:** give
   the approval state from `crwd_db` plus the honest "1–2 business days after
   approval" framing. Only **escalate to `crwd-handoff`** for a genuine dispute you
   can't resolve from the data: Dot shows the payout **sent but the member never
   received it**, a wrong/missing amount, a refund request, or a **rejected**
   submission. Don't guess about money that's supposedly already gone out.

## Pitfalls

- Gig enrollment from this skill's `get_user_gigs` / status lookup is for **payment
  context in this reply** only — do not treat it as a durable membership roster for later
  "what gigs am I in?" questions (those must re-fetch via `crwd-gig-discovery`).
- **Don't claim the money landed** unless Dot actually reports it sent/paid.
  "Approved" and "paid" are different states — read them separately.
- **Never call a transfer "your payout" just because Dot labels it `type: "payout"`.**
  That word is Dot's mechanism, not the reason for the money — product funds arrive
  the same way. Classify from the attached text and the amount, or don't classify.
- **Product funds are not earnings.** Leave them out of "how much have I earned",
  and if some transfers couldn't be classified, say the total covers only what you
  could confirm rather than silently counting them.
- **Cents vs dollars.** Dot amounts are in cents; a `4000` is `$40`. Convert before
  you compare an amount to a gig's payout or product-funds figure, or quote it.
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

- Used the `[CRWD member]` `user_id` for `crwd_db`, and a Dot `user_id` for
  `dot` — never asked the member for a Dot user ID.
- Never told a member they weren't paid on the strength of an empty transfer
  list from a freshly created Dot user.
- Confirmed the right gig `_id` when the question was about a specific gig.
- Separated approval state (`crwd_db`) from Dot's payout state (`dot`) — didn't
  conflate "approved" with "paid".
- Every transfer you named as product funds or a payout was settled by its attached
  text or by an unambiguous amount match — not by Dot's `type` field, and not by a
  guess. Anything you couldn't settle was reported without a label.
- Any earnings total counted payouts only, and said so if some transfers were
  unclassified.
- Quoted the real payout amount and framed timing as *typical*, not guaranteed.
- Handed off on Dot errors, "sent but not received" disputes, or rejections.
- If the member was unsure how payment works at all, you explained the Dot flow
  (approval → Payout Link → claim → pick method → funds move), not just the raw status.
- If timing was the real question, you asked which payout method they use rather than
  giving one generic timeframe for every method.
