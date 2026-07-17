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

## proof-acceptance / proof-rejection

From `store_proof` **this turn** only (not member wording).

- All proofs stored this turn `accepted` → `proof-acceptance`
- Any proof not accepted → `proof-rejection`
- Mutually exclusive for the turn; not preserved forever

## handoff-escalation

Only when `crwd_handoff` runs this turn (then preserved on later turns).

## gig-complete / risk-*

Owned by `crwd-proof-validator` / `crwd-risk-analyser`. Auto-labeler preserves them.
