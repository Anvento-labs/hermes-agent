# CRWD Coach — Live Browser Test Results

Ran all 6 personas from `docs/testing/test_personas.md` against the real "Ask
CRWD" chat widget on the local CRWD Portal (`http://localhost:5173/my-gigs/...`)
in a single continuous session on a real logged-in test account (Mahender).
This documents what the bot *actually* said, compared against the scripted
demo conversations, plus concrete bugs/gaps worth fixing before a client demo.

Note: since this was one continuous chat session on one real account (not 6
fresh sessions), the bot correctly used the account's real state throughout
(real gig: "Pul Tool," a $40 Walmart live gig; real earnings: $1,302.16
lifetime) rather than the fictional details in the written scripts. That's
expected and actually a good sign — see "Real-data grounding" below.

## What worked well (matches the skills as designed)

- **Identity/onboarding (Maria).** Opened with "Hey Mahender! I'm your CRWD
  Coach..." — named CRWD immediately, no generic "AI assistant" framing.
- **Real-data grounding, not scripted answers.** Every persona's answers used
  live account data: the real $1,302.16 lifetime total, the real "Pul Tool"
  gig and its real $40 payout, the real 54 open gigs, the real Venmo transfer
  dates/amounts. Nothing was guessed or hallucinated.
- **Caught a false premise (Jasmine).** When told "I got approved for the Pul
  Tool gig," the bot checked `crwd_db` and correctly pushed back — the
  membership was actually still pending, not approved. It didn't just accept
  the member's claim. This is exactly the "never fabricate approval status"
  pitfall from `crwd-gig-discovery`/`crwd-gig-execution` working as intended.
- **Precise, quoted requirements (Jasmine).** The two-purchase rule ("2
  different payment methods... 2 separate transactions = 2 receipts"), the
  two UGC concept options, and the exact proof list were all quoted precisely
  from gig data, not paraphrased.
- **Honest tool-unavailability fallback (Jasmine).** When asked for the
  nearest Walmart to a ZIP, the bot said plainly "I don't have a live web
  search tool active right now" instead of inventing an address — matches the
  "never invent a store" pitfall exactly.
- **Approved vs. paid separation + root-causing (Priya).** Rather than a
  generic "1-2 business days," the bot asked which gig and which payout
  method, then reasoned about *why* nothing had landed (unclaimed Dot payout
  link) instead of repeating the generic timeline.
- **Tax-season value-add (Destiny).** Gave the real lifetime total ($1,302.16)
  and proactively mentioned the Dot 1099 threshold and `my.dots.dev` as the
  export source for an accountant — beyond what the skill's procedure
  strictly requires, but on-brand and useful.
- **Correct gig-list exclusion (Destiny).** "Show me everything I haven't
  applied to" correctly excluded gigs already joined and returned an accurate
  count (54 open gigs).
- **No duplicate reminders (Angela).** When asked to remind before the
  deadline a second time, the bot recognized the Aug 15 reminder already
  existed from earlier in the conversation and only added the *new*,
  distinct follow-up (tomorrow-morning check-in) instead of creating a
  redundant duplicate job.
- **Adaptive troubleshooting (Courtney).** Since the member said she'd
  "already tried 3 times," the bot skipped straight to incognito/different
  browser instead of blindly starting at "try refreshing" — a nice
  adaptation, not a robotic fixed script.
- **Clean handoff, twice (Courtney).** Both triggers worked correctly:
  unresolved troubleshooting after real attempts, and a rejected submission +
  visible frustration. Both handoffs were warm, confident, didn't guess at
  the rejection reason, and didn't loop retrying.

## Bugs / gaps worth fixing before a client demo

1. **Markdown tables and numbered/bulleted lists appear in the chat widget,**
   violating the SOUL.md style rule ("No markdown lists, headers, tables, or
   bold blocks — they look terrible in this widget"). Seen in:
   - Priya's and Destiny's payment-history answers (`| Date | Amount |`
     tables).
   - Destiny's gig list (`$100`, `$50`... headers with `-` bullets under
     each).
   - Courtney's troubleshooting steps (`1. ... 2. ...` numbered list).
   These render as raw pipe/hyphen text in a chat widget that isn't a
   markdown-aware surface — worth confirming what the actual widget renders
   (if it strips markdown to plain text, these become unreadable lines of
   `|` characters). **Recommend:** check whether the CRWD Coach system
   prompt/response post-processing enforces the "no markdown" style rule at
   the model or gateway level; right now it isn't holding under real load
   for money/list-formatted answers.
2. **Duplicate assistant replies.** Several answers were sent twice back to
   back with near-identical (sometimes slightly reformatted) content — e.g.
   the "still pending approval" message and both Venmo-history messages. Only
   the first appeared to fire tool calls; the second looked like a
   regenerated/re-sent variant. Worth checking the gateway's message-send
   path for a double-dispatch on multi-part responses.
3. **Background curator noise leaking into the member-facing chat.** Messages
   like "💾 Self-improvement review: Patched SKILL.md in skill
   'crwd-db-query-patterns'..." appeared directly in the support widget
   transcript, visible to the member. Per `AGENTS.md`, curator activity
   should be an internal/background concern — it should not post into a
   live member support conversation.
4. **"Just hang tight!" on the troubleshooting handoff (Courtney)** brushes
   close to the "don't soften with 'might take a while' caveats" pitfall in
   `crwd-handoff`. It's not a hard violation (no explicit "could take a
   while"), but it's a step away from the "confident, no hedging" bar the
   skill sets — the rejection-handoff message right after it ("someone's on
   it") was a better example of the intended tone.
5. **Chatwoot home-channel system message on first contact** ("📬 No home
   channel is set for Chatwoot... Type /sethome...") appeared as the very
   first thing a brand-new member (Maria persona) would see, before the
   coach's own greeting. This is an internal Hermes/gateway setup message
   that a real CRWD member has no context for and shouldn't need to see or
   act on in a support widget.

## Persona-by-persona summary

- **Maria (Curious Newcomer):** Identity, legitimacy, and Dot flow explained
  clearly and personalized with real account data; smoothly moved to a
  concrete gig list. Matches the script's intent.
- **Jasmine (First-Gig Doer):** Caught the false "approved" claim, gave exact
  buy link/requirement/proof, correctly gated buying on approval, flagged the
  TX/FL geo-restriction (a good catch the written script didn't even
  anticipate), and honestly punted on live store lookup when web search
  wasn't available.
- **Priya (Anxious First-Time Earner):** Asked the right disambiguating
  questions (which gig, which payout method), reassured with real payment
  history, and scheduled a follow-up — but the answer used a markdown table
  and was sent twice (see bugs above).
- **Destiny (Power Earner):** Accurate lifetime total with tax-filing framing,
  accurate open-gig list excluding her own — but again via markdown
  table/list formatting that likely doesn't render well in the widget.
- **Angela (Busy Multi-Gig Juggler):** Correctly avoided a duplicate
  reminder and added a distinct follow-up, confirmed both in one message.
  Clean pass.
- **Courtney (Frustrated, Stuck User):** Adaptive troubleshooting, then a
  clean handoff on repeated failure, then a second clean handoff on
  rejection + frustration. Clean pass aside from the minor "hang tight"
  phrasing note above.

## Suggested next step

Fix items 1–3 above (markdown formatting, duplicate sends, curator message
leakage) before running this in front of a client — they're the difference
between "looks like a polished product demo" and "looks like a bot with rough
edges," even though the underlying skill logic and data-grounding are already
working correctly.
