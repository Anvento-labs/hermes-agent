# Payments — Dot, timing, reimbursement

## How members get paid

- Payments are processed through **Dot** (CRWD's payments partner — the company is
  "Dots," product at usedots.com).
- Once a submission is **approved**, payout is sent via Dot. Typical turnaround from
  approval to payment landing is **1–2 business days**.
- Frame this as *typical, not guaranteed* — you can't promise timing on CRWD's behalf.

## How Dot actually pays them out (explain this proactively — don't wait to be asked)

Payment clarity is the single biggest thing that keeps a member from hesitating or
churning. A member who doesn't understand *how* the money reaches them will assume the
worst. If a member seems unsure about getting paid at all — first gig, "how does this even
work?", or general anxiety about money — walk them through this flow instead of only
answering the narrow question they asked:

1. **CRWD tells Dot to pay once your work is approved.** You don't have to do anything to
   trigger it — approval is the trigger.
2. **Dot sends you a Payout Link** by email or SMS, to whichever contact info CRWD has on
   file for you. This is the claim link — nothing happens until you open and complete it.
3. **You claim the link:** enter/verify your phone number (a code is texted to you), fill in
   basic personal info the first time you use Dot, and — depending on your location and how
   much you've been paid this year — complete a short tax form (W-9 for US members, W-8BEN
   for international members). This is standard for any payments platform, not a CRWD-specific
   hurdle.
4. **You pick your payout method** from whatever's available in your country, and enter that
   method's details (e.g. debit card/bank info for a bank transfer, your PayPal email, your
   Venmo/Cash App tag). **Ask the member which platform/method they use or plan to use** if
   they're asking "how will I get paid" or the timing question hinges on it — the method they
   pick is what determines how fast it lands, so this single follow-up question answers most
   of their uncertainty.
5. **Funds move on the rail tied to that method.** See timing per method below.

### Payout methods and typical timing (once Dot initiates the transfer)

Availability depends on the member's country; ask which one applies to them rather than
assuming:

- **PayPal** — usually 5–10 minutes, occasionally delayed up to 2 business days.
- **Venmo** (US) — usually 5–10 minutes, occasionally delayed up to 2 business days.
- **US bank transfer (ACH)** — 1–3 business days; if the member's bank supports RTP
  (Real-Time Payment), it can land within minutes to 24 hours instead.
- **Cash App** — 1–2 business days, plus up to 24 hours for Cash App itself to show it.
- **International bank transfer** — usually 3–5 business days.
- **Payoneer** — 1–3 business days on Dot's end, then another 1–3 business days for
  Payoneer to show the funds.
- **AirTM** — usually 5–10 minutes.
- **Amazon gift card / virtual Visa prepaid card** (where offered) — instant.

Always frame these as *typical, not guaranteed* — same rule as the overall 1–2 business
day estimate. Payouts made on a Friday or over the weekend often don't complete until the
next business day because most rails only move on banking business days (no weekends,
no bank holidays).

### Members can self-track at my.dots.dev

Dot gives every payee their own dashboard at **https://my.dots.dev** to check payout
status and update payout details directly — independent of CRWD. Mentioning this gives
an anxious member something concrete to check on their own.

### Official Dot references (share when useful, don't just recite from memory)

- Payee dashboard: https://my.dots.dev
- How to claim a Payout Link: https://support.usedots.com/en/articles/8327083-how-to-claim-a-payout-link
- "Where is my money?" (per-method timing/troubleshooting): https://support.usedots.com/en/articles/8338681-where-is-my-money
- Receiving a Payout (help center collection — bank, PayPal, Venmo, Cash App, AirTM, Payoneer): https://support.usedots.com/en/collections/5968479-receiving-a-payout
- What Dot/Dots is: https://usedots.com/

If a member wants more detail than the summary above (e.g. "why is my Venmo payout still
pending?"), use `web_search`/`web_extract` to pull the specific Dot Help Center article
for their method rather than guessing — the answers above are accurate as of when this
skill was written, but Dot's own docs are the source of truth for edge cases.

## Live payment status — via the `dot` tool

- Live Dot payout status **is** available through the `dot` tool (used by the
  `crwd-payment-status` skill): `get_user_transfers` lists a member's transfers by
  `user_id`, and `get_transfer` fetches one transfer in full by its
  `transfer_id` (taken from that list).
- **Beta assumption:** CRWD and Dot don't yet expose a real id-mapping lookup,
  so the member's CRWD `user_id` is passed straight through as the Dot
  `user_id`. Don't ask the member for a separate "Dot user ID" and don't try
  to look one up — revisit once CRWD/Dot ship a real cross-reference.
- Still frame timing honestly: "once your proof is approved, Dot sends payment, usually
  within 1–2 business days" — *typical, not guaranteed*. Don't promise a date.
- Read approval and payment as **separate** states: `crwd_db` shows whether the work is
  approved (`hasPaid` / `isCompleted` / receipt status); `dot` shows whether Dot has
  actually sent the payout. "Approved" ≠ "paid".
- If the `dot` tool errors or isn't configured, fall back to the `crwd_db` approval state
  plus the honest 1–2 business-day framing.
- For a genuine money dispute — Dot reports **sent but the member never received it** — or
  anything you can't answer confidently, **hand off** (`crwd-handoff`). Don't guess about
  money already gone out.

## The two payment types

CRWD sends members money for two different reasons, and both arrive through Dot:

| | **Product funds** | **Payout** |
|---|---|---|
| What it is | Money to **buy the product** with | The **fee for completing the gig** |
| When | Before they shop | After their proof is approved |
| Is it earnings? | **No** — it's spending money | **Yes** |
| Gig field | `effective_product_funds` | `effective_payout` |

Only some gigs have product funds. Check the gig's **`effective_product_funds`**
(`get_gig_details` / `list_active_gigs`) before framing money at all:

- **It has a value** → two separate amounts, described separately. Never add them together
  or quote one as if it were the other.
- **It is null** → the default, and true of every gig today. Use the reimbursement framing
  below, and **say nothing about product funds at all** — not even to rule them out. "This
  gig doesn't include product funds" is a claim about machinery the member can't see, and
  it goes stale the moment CRWD ships product funds on some gigs.

## Telling the two apart in Dot's transfer history

Dot does **not** have a field for which of the two a transfer is. You work it out. Two
traps first, both of which produce a confident wrong answer:

- **`type: "payout"` on a transfer does not mean "the gig payout."** It is Dot's own
  mechanism word — money leaving to the member — and **both** payment types arrive as
  `type: "payout"`. Never classify on this field.
- **Dot amounts are in cents; CRWD's are in dollars.** A `$40` payout is `4000` in Dot.
  Convert before comparing or every match fails.

Work down this ladder and stop at the first rung that actually settles it:

1. **The transfer's own text.** Read the free text CRWD attached — `metadata` on the
   transfer, and the `memo` when the transfer came from a payout link (`payout_link_id`
   is set; `get_transfer` for the detail). If it names which payment this is, that is
   **authoritative** — believe it over any amount arithmetic.
2. **Match the amount against that gig's two known numbers.** Pull `effective_payout` and
   `effective_product_funds` for the gig from `crwd_db`, convert Dot's cents to dollars,
   and see which one the transfer equals. Conclusive only when **exactly one** matches.
3. **Sequence, as corroboration only.** Product funds necessarily arrive *before* the
   purchase; the payout lands *after* proof is approved. Use this to sanity-check a match
   from rung 1 or 2 — **never** as the basis for a label on its own.
4. **Still ambiguous → don't label it.** Both amounts equal, no text, several gigs with
   the same figure, or no gig context at all. Say what you can actually see — the amount,
   the date, and whether Dot sent it — and leave the reason unnamed. If the member needs
   to know which it was, **hand off** (`crwd-handoff`). Guessing here tells someone their
   grocery money was their wages.

**"How much have I earned?" counts payouts only.** Product funds are not earnings — they
are money to spend on the product. Summing every transfer overstates what the member made,
so exclude anything you have identified as product funds, and if you could not classify
some transfers, say your total covers only what you could confirm rather than quietly
including them.

On a gig whose `effective_product_funds` is null, all of this collapses: every transfer for
it is a payout, and there is nothing to disambiguate.

## Payout ≠ reimbursement (gigs with no product funds — the default)

- For **live gigs**, the member **keeps the product** they bought. The payout is the **fee
  for completing the gig**, not a refund of the purchase.
- If a member asks "do I get my money back for what I bought?" → the answer is **no**: they
  keep the item, and the payout is separate. This is a fixed fact — say it plainly, don't hedge.
- This holds whenever `effective_product_funds` is null. On a gig that *does* carry
  product funds, those funds are what covers the purchase — check the field before
  reciting this.
