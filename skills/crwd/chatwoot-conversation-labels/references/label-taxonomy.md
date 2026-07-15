# Label taxonomy — examples per label

Titles are lowercase; pass them exactly to `chatwoot_labels` `assign_labels`.
Auto-labeling applies **every matching predefined label** (no per-turn numeric
cap). Prefer tool evidence when the agent called a mapped tool this turn.

## handoff-escalation

Applied **only when you call `crwd_handoff`** in that turn — not from member
text alone. Pair with topic label(s) when possible.

- You looped in a human after rejected proof → `["proof-submission", "handoff-escalation"]` (+ `mid-gig-support` if enrolled)
- Opt-out processed via handoff → `["off-topic", "handoff-escalation"]` (opt-out is not a topic label)

## proof-submission

Proof/receipt/submit questions **always** qualify (enrollment not required).
Tool signal: `crwd_db` `get_user_receipts`.

When the member is also enrolled, pair with `mid-gig-support`.

- "How do I submit proof?" (not enrolled) → `proof-submission`
- "How do I submit proof?" (enrolled) → `["proof-submission", "mid-gig-support"]`
- "Here's my receipt" / "my submission was rejected" → `proof-submission` (+ `mid-gig-support` if enrolled)

## mid-gig-support

Conversation about an **enrolled** gig: deadlines, requirements, next steps,
gig details for a named enrolled gig, or proof while enrolled.

Tool signals: `crwd_db` `get_user_gigs` / `get_user_gig_status` /
`get_user_gig_history` / `get_waitlisted_gigs`.

A named gig in the message must match an enrollment. Unenrolled or unmatched
named-gig help → `gig-discovery`.

- "What's my deadline?" (enrolled) → `mid-gig-support`
- "What's my deadline on the Amazon gig?" (enrolled in Amazon) → `mid-gig-support`
- "What's my deadline?" (not enrolled) → `gig-discovery`
- Unenrolled "tell me about the Amazon gig" → `gig-discovery`

## gig-discovery

Tool signal: `crwd_db` `list_active_gigs`.

- "What gigs are near me?" (even if enrolled)
- "Browse available gigs" / "what gigs can I apply to?"
- "What is CRWD?" / "How does CRWD work?"
- Unenrolled "tell me about the Amazon gig"
- Generic CRWD hello with no clearer bucket

## payment-payout

Tool signal: `dot` (`get_user_transfers` / `get_transfer`).

- "Did I get paid?"
- "When will I be paid?"
- "Show my payment history"
- Dot payout timing; refund/chargeback language

## account-eligibility

Eligibility to join or qualify — not account status, scam, or opt-out.

- "I'm not eligible"
- "Wrong state" / age requirement
- "Can't join" / "don't qualify" / "too young"

## account-info

Account status and membership — not eligibility or scam.

- "Why was I banned?" / suspended / deactivated
- "What's my account status?" / membership questions
- "My account" status inquiries

## scam

Scam / phishing / fraud signals. Hand off via `crwd_handoff` when appropriate.

- Phishing, suspicious links, "send me your password"
- Wire transfer / bitcoin / gift-card fraud language

## app-help

- "Where is Home vs Explore?"
- "Where can I find IRL gigs?" / "How do I find gigs in Explore?" (navigation to a tab/section)
- "How do I open a gig?"
- Link won't open, page won't load, login error, app crash
- Pair with `payment-payout` when a payout page is broken

## off-topic

- Jokes, recipes, weather, homework, trivia
- Identity / capability: "Who are you?", "What can you do?" (not gig-discovery —
  do not treat the coach intro as discovery or payment)
- Bare greetings ("hi", "hello")
- Fallback when the message has no CRWD anchor words

## Topic switches

When the member moves from one clear topic to another (especially when tools
map the new intent), auto-labeling **replaces** the previous set — e.g.
`app-help` is removed when the next turn is tool-backed `gig-discovery`.
