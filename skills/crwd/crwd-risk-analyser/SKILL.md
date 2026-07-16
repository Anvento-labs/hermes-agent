---
name: crwd-risk-analyser
description: "Maintains a CRWD member's fraud risk score and risk band label from their proof history and conversation signals."
version: 0.1.0
metadata:
  hermes:
    tags: [crwd, risk, fraud, score, duplicate, scam, escalation, internal]
    related_skills: [crwd-proof-validator, crwd-handoff, chatwoot-conversation-labels]
    requires_toolsets: [crwd]
---

# CRWD Risk Analyser

Internal only. You own the member's **risk score** — nothing else writes it. You
read what `crwd-proof-validator` recorded, decide the points, and persist them with
`crwd_risk_score`, then keep the risk band label in step.

**Never mention the score, the band, the label, or that a member was flagged.** Not
to the member, not in a member-facing reply, not as a hint. The member sees nothing
from this skill — its entire output is a number, a label, and an internal note.

## When to Use

Every agent turn on a CRWD conversation, but you act only when there is something
to act on:

1. **An unscored proof exists** — `crwd-proof-validator` stored a verdict.
2. **The member's messages show scam signals** (below).

If neither, do nothing. Most turns you will do nothing — that is correct.

Do **not** use this to judge proof itself. Whether a receipt is a duplicate is
`crwd-proof-validator`'s call; you only price the verdict it reached.

## Prerequisites

- `crwd` toolset. `crwd_risk_score` needs Chatwoot creds and no-ops safely without
  them — carry on, and don't retry.
- The member's `user_id` from the `[CRWD member]` context line.
- `chatwoot_labels` for the band label. Optional; it no-ops off Chatwoot.

## Procedure

### 1. Find what hasn't been scored

```
custom_query(collection="proof_submissions", operation="find",
             filter={"user_id": "<user_id>", "risk_scored": false},
             sort={"created_at": -1})
```

Each row is one verdict, carrying `reason_code`, `status`, `crwd_id`,
`product_name`, `store_name`, `confidence`, and `metadata.proof_info`.

**Score each row once, then mark it:** `mark_proof_risk_scored(proof_record_id=<_id>)`.
This is not optional. `crwd_risk_score` is **delta-only with no history** — it adds
points and forgets. If you score a row twice, you silently double a member's risk
and nothing will ever tell you. The flag is the only thing preventing that.

### 2. Count the offence to get the tier

Escalation is per reason, over the member's whole history:

```
custom_query(collection="proof_submissions", operation="count",
             filter={"user_id": "<user_id>", "reason_code": "<code>"})
```

The proof you're scoring is **already stored**, so `count == 1` *is* the first
offence, `2` the second, and so on. `count` is not capped.

### 3. Price it

| `reason_code` | 1st | 2nd | 3rd+ | Why |
|---|---|---|---|---|
| `duplicate_proof` | +15..20 | +30..40 | +40..50 | Someone else's purchase, or reused across gigs |
| `wrong_product` | +15..20 | +30..40 | +40..50 | Usually confusion — escalates only if it keeps happening |
| `wrong_quantity` | 0 | 0..15 | 0..15 | Usually sold out. First time is free |
| `invalid_order_number` | 0..15 | 0..15 | 0..15 | Typed a number that isn't a real order |
| `suspected_edited` | +85..95 | +85..95 | +85..95 | Forgery. Critical on the first offence — no ramp |

**Everything else scores 0.** `clean_match`, `gig_not_active_for_user`,
`incomplete_submission`, `unreadable`, `no_identifier`, `link_unreachable`,
`date_outside_gig_window`, `content_mismatch`, `link_not_owned`, `wrong_proof_type`
are mechanics and coaching cases, not fraud. **Do not invent a delta for them.** A
member whose photo was blurry is not a risk.

**Repeated validation failures** — separately, if the member has **3 or more**
rejected proofs across gigs:

```
custom_query(collection="proof_submissions", operation="count",
             filter={"user_id": "<user_id>", "status": "rejected"})
```

score `+65..95` **once**, when the third lands. This is a pattern, not an event: a
member who fails constantly across campaigns is a different problem from one bad
receipt. Don't re-apply it on every subsequent failure.

Pick within a band using what the record tells you: `confidence`, whether
`metadata.proof_info` shows a near-miss or a wholesale mismatch, and how recent the
prior offences are. A high-confidence forgery sits at the top of its band; an
ambiguous one at the bottom.

### 4. Persist it

`crwd_risk_score(delta=<points>, reason="<reason_code> #<tier>")`, **once per
proof**. It adds the delta, clamps 0–100, and returns `previous` and `new_score`.

- The delta is an **increment, never an absolute** — don't pass the score you want.
- There is **no read action**. `new_score` in the response is how you learn the
  score; `delta=0` costs a write, so don't use it to peek.
- Then `mark_proof_risk_scored` on that record.

### 5. Set the band label

Read the band off `new_score`:

| Score | Band | Label |
|---|---|---|
| 0–30 | Low | `risk-low` |
| 30–60 | Medium — manual review recommended | `risk-medium` |
| 60–85 | High — manual approval required | `risk-high` |
| 85–100 | Critical — block or reject | `risk-critical` |

Bands are **mutually exclusive**, so apply them with `replace`:

```
chatwoot_labels(action="assign_labels", replace=true,
                labels=["risk-high", ...every other label the conversation should keep])
```

`replace: true` overwrites the whole set — merge would leave two bands on the
conversation at once. So **pass the topic labels through too** (`proof-submission`,
`handoff-escalation`, …). Read them first with `get_all_labels` if you're unsure
what's there.

The band **persists across turns**: the automatic labeler re-assigns labels with
`replace=true` every turn from its own classification, and preserves `risk-*`
rather than clearing it. So set the band once and it stays until you change it.

Even so, **the score is the record, not the label**. `custom_attributes.risk_score`
on the contact is durable; the band is only ever a view of it, and is always
re-derivable. Never treat a missing label as a missing score.

Crossing into `risk-high` or `risk-critical` → `crwd_handoff` as well. A member at
that level should not be left to the bot.

### 6. Scam signals — not a proof

Score `+50` when the member is clearly abusing the channel:

- Scam or phishing links ("click here for bitcoin", credential harvesting)
- Using the bot or its tokens for their own unrelated purposes
- Re-uploading proof that was already rejected, repeatedly, to get around approval

**This cannot be recorded on the proof table** — `store_proof` requires a real
`proof_type` and a normalizable id, and a scam message has neither. The score *is*
the record. So: `crwd_risk_score(delta=50, reason="scam signals")`, `crwd_handoff`,
and let the labeler tag it — it classifies `scam` itself from the message, so you
don't need to apply that label; just set the band.

Be conservative. A confused member pasting a weird link is not a scammer; a member
asking an off-topic question is `off-topic`, not fraud. Score this when you'd be
comfortable defending it to the member's face — you won't have to, which is exactly
why the bar is high.

## Pitfalls

- **Never tell the member their score, band, or that they were flagged.** This
  skill has no member-facing voice at all.
- **Never score the same proof twice.** Delta-only + no history means a double
  score is invisible and permanent. `risk_scored` is the only guard.
- **Don't score `clean_match` or coaching outcomes.** Blurry photos, missing links,
  and incomplete submissions are not fraud.
- **Don't pass an absolute score** to `crwd_risk_score` — it takes an increment.
- **Don't merge band labels** — `replace: true` with the full set, or the
  conversation accumulates every band the member has ever been in.
- **Don't re-apply the repeated-failures penalty** on every later failure.
- **Don't reverse a score to be kind.** Negative deltas exist, but a member talking
  their way out of a verdict is the oldest attack there is. A human adjusts.
- **Don't judge the proof.** If you disagree with a verdict, that's a handoff note,
  not a different delta.

## Verification

- Exactly one `crwd_risk_score` call per unscored proof, and every scored proof was
  marked with `mark_proof_risk_scored`.
- The delta came from the table and the tier came from a `count`, not a guess.
- Zero-risk reason codes scored nothing.
- A `risk-*` label matching `new_score` was applied with `replace: true` and the
  topic labels preserved. (It may not survive the next turn — see the limitation
  above. The score is the record; the label is a convenience.)
- `risk-high` / `risk-critical` were handed off.
- **No member-facing message referenced the score, the band, or a label.**
