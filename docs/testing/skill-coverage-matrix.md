# CRWD Coach — Skill Coverage Matrix

Companion checklist to `docs/testing/test_personas.md`. Maps every `crwd-*`
skill to the persona(s) that exercise it and the concrete tool calls involved,
so whoever runs a client demo can confirm every capability actually got shown.

## crwd-application-expert

App navigation — Home vs. Explore, finding active/completed/waitlisted gigs.

- **Persona:** Maria (Curious Newcomer)
- **Tool calls:** none required for the onboarding explanation itself; would
  use `crwd_db` `get_user_gigs` / `get_waitlisted_gigs` if she later asks "what
  do I have?"
- **What it proves:** a brand-new member gets oriented to the app (Explore to
  browse, Home to track her own gigs) without a generic app-tour info-dump.

## crwd-gig-discovery

Finding/understanding gigs, applying, approval status, store lookup.

- **Personas:** Maria (browse + apply), Jasmine (store lookup), Destiny
  (available gigs + pagination)
- **Tool calls:**
  - `crwd_db` `list_active_gigs(user_id)` — Maria's first gig list, Destiny's
    "what's open that I haven't applied to"
  - `crwd_db` `list_active_gigs(user_id, offset=next_offset)` — Destiny's "show
    me more," confirming `has_more` is only declared false when true
  - `web_search` (+ `web_extract` if needed) — Jasmine's nearest Walmart:
    name, address, phone, hours
- **What it proves:** real payout/deadline/store data every time (never
  guessed), correct exclusion of gigs the member already joined, honest
  pagination, and location never assumed before asking.

## crwd-gig-execution

Buying the product, meeting special requirements, content, proof submission.

- **Persona:** Jasmine (First-Gig Doer)
- **Tool calls:**
  - `crwd_db` `get_gig_details` — confirms gig type (live vs. online)
  - `crwd_db` `get_user_products(user_id)` — real product name + buy link
  - `crwd_db` `get_user_receipts(user_id)` — proof validation state after
    submission
- **What it proves:** exact special requirements (two purchases, two payment
  methods) are stated precisely instead of paraphrased, the real buy link is
  given proactively, and submission status is confirmed from data, not assumed.

## crwd-payment-status

"Did I get paid," payment history, explaining the Dot flow.

- **Personas:** Maria (proactive Dot explainer for a newcomer), Priya (Anxious
  First-Time Earner — approved vs. paid, root-cause via payout method),
  Destiny (Power Earner — lifetime totals/history)
- **Tool calls:**
  - `crwd_db` `get_user_gigs` — approval/completion state before checking Dot
  - `dot` `get_user_transfers(user_id)` — payout history / totals (Destiny),
    sent status (Priya)
  - `dot` `get_transfer(transfer_id)` — a single payout in full, if needed
- **What it proves:** "approved" and "paid" are always treated as separate
  states, real dollar amounts are quoted (never estimated), and the coach asks
  for payout method to turn a vague timing answer into a specific one.

## crwd-reminders-followups

Deadline reminders and follow-up check-ins.

- **Personas:** Angela (Busy Multi-Gig Juggler — deadline reminders across 3
  gigs, plus a distinct follow-up check-in), Priya (follow-up on claiming her
  Dot payout link)
- **Tool calls:**
  - `crwd_db` `get_user_gigs` / `get_gig_details` — real `end_date` per gig,
    never a guessed time
  - `cronjob` — one job scheduled per reminder/follow-up
- **What it proves:** reminders are grounded in real deadline data, "remind me
  before it's due" and "check back with me tomorrow" are handled as two
  distinct offers (not one generic nudge), and each scheduled item gets a
  short, specific one-line confirmation.

## crwd-troubleshooting

Broken links, pages that won't load, dead buttons.

- **Persona:** Courtney (Frustrated, Stuck User) — first half of her scenario
- **Tool calls:** none (this skill is a guided fix sequence, not a data
  lookup)
- **What it proves:** standard fixes (refresh → incognito → clear cache/
  different browser) are offered one at a time with a check-in after each,
  not dumped all at once, and troubleshooting stops the moment it's resolved
  or repeats.

## crwd-handoff

Escalating to a human — frustration, repeated issues, rejections, disputes.

- **Persona:** Courtney (Frustrated, Stuck User) — second half, once her
  submission is rejected and she's visibly upset
- **Tool calls:** `crwd_handoff` (notifies the team with `reason` +
  `summary`)
- **What it proves:** rejection + frustration are treated as an immediate,
  confident handoff (no guessing at the rejection reason, no over-apologizing
  loop, no "might take a while" hedge) — the bot acts as the fast line, not
  the last line.

## Cross-cutting: crwd-reference

Not member-facing directly, but pulled by other skills for exact facts.

- **`references/payments-dot.md`** — used in Maria's and Priya's payment
  explanations (payout ≠ reimbursement, 1–2 business days typical, claim flow)
- **`references/gig-lifecycle.md`** — used in Maria's browse→paid explanation
- **`references/proof-requirements.md`** — used in Jasmine's exact proof
  format (receipt + store + content link)

## Demo run checklist

- [ ] Maria — identity, lifecycle explanation, Dot explainer, first apply
- [ ] Jasmine — store lookup, buy link, precise 2-purchase requirement, proof
      format, receipt status check
- [ ] Priya — approved-vs-paid distinction, payout method question, follow-up
      scheduled
- [ ] Destiny — real lifetime total, pagination with correct `has_more`
      handling
- [ ] Angela — real deadlines, 3 reminders scheduled, distinct follow-up
      check-in
- [ ] Courtney — troubleshooting steps in order, then a clean handoff on
      rejection + frustration

If every box above is checked, all 7 `crwd-*` skills and every core tool
(`crwd_db`, `dot`, `web_search`, `cronjob`, `crwd_handoff`) have been shown on
camera.
