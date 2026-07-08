# Label taxonomy — examples per label

Use these to pick up to 2 labels each turn. Titles are lowercase; pass them
exactly to `chatwoot_labels` `assign_labels`.

## handoff-escalation

Applied **only when you call `crwd_handoff`** in that turn — not from member
text alone. Pair with the topic label when possible.

- You looped in a human after rejected proof → `["proof-submission", "handoff-escalation"]`
- Opt-out processed via handoff → `["account-eligibility", "handoff-escalation"]`

## proof-submission

Proof/receipt/submit questions **always** qualify (enrollment not required).

When the member is also enrolled, pair with `mid-gig-support`.

- "How do I submit proof?" (not enrolled) → `proof-submission`
- "How do I submit proof?" (enrolled) → `["proof-submission", "mid-gig-support"]`
- "Here's my receipt" / "my submission was rejected" → `proof-submission` (+ `mid-gig-support` if enrolled)

## mid-gig-support

Conversation about an **enrolled** gig: deadlines, requirements, next steps,
gig details for a named enrolled gig, or proof while enrolled.

A named gig in the message must match an enrollment. Unenrolled or unmatched
named-gig help → `gig-discovery`.

- "What's my deadline?" (enrolled) → `mid-gig-support`
- "What's my deadline on the Amazon gig?" (enrolled in Amazon) → `mid-gig-support`
- "What's my deadline?" (not enrolled) → `gig-discovery`
- Unenrolled "tell me about the Amazon gig" → `gig-discovery`

## gig-discovery

- "What gigs are near me?" (even if enrolled)
- "How do I find gigs in Explore?"
- "What is CRWD?" / "How does CRWD work?"
- Unenrolled "tell me about the Amazon gig"
- Generic CRWD hello with no clearer bucket

## payment-payout

- "Did I get paid?"
- "When will I be paid?"
- "Show my payment history"
- Dot payout timing; refund/chargeback language

## account-eligibility

- Not eligible, wrong state, age requirement
- Account banned or suspended, membership status
- Opt-out / stop messaging (until you hand off)
- Scam/phishing signals (hand off via `crwd_handoff` when appropriate)

## app-help

- "Where is Home vs Explore?"
- "How do I open a gig?"
- Link won't open, page won't load, login error, app crash
- Pair with `payment-payout` when a payout page is broken

## off-topic

- Jokes, recipes, weather, homework, trivia
- Fallback when the message has no CRWD anchor words
