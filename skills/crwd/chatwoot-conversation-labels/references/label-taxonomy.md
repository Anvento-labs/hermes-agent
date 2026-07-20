# Applied label taxonomy (examples)

Unapplied titles are omitted here on purpose — they are not assigned.

## payment-issue

Any payment-related member message.

- "When will I get paid?" → `payment-issue`
- "Where's my Dot / refund / chargeback?" → `payment-issue`
- Payout page broken → `payment-issue` + `app-help`

## app-help

App navigation and broken UI.

- "Where is the Explore tab?" → `app-help`
- "The page won't load" → `app-help`

## new-user

Data-first — not intent. Applied while the member has **not** completed at least
one gig (all required proofs accepted). Payment may or may not have been received.

- First-time member asking anything → includes `new-user` when DB confirms no completed gig
- After a gig completes (`is_gig_completed` / completed-gig lookup) → `new-user` drops

## proof-acceptance / proof-rejection / gig-complete

From `store_proof` **this turn** only (not member wording).

- All proofs stored this turn `accepted` → `proof-acceptance`
- Any proof not accepted → `proof-rejection`
- `is_gig_completed: true` on this turn → `gig-complete`
- Mutually exclusive proof verdicts for the turn; none of these are preserved forever

## handoff-escalation

When `crwd_handoff` runs this turn. Kept while conversation status is `open`;
cleared when status is no longer `open` (bot owns again, typically `pending`).

## risk-*

Owned by `crwd-risk-analyser`. Auto-labeler preserves the current band across
replace turns.
