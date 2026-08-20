# Gig stages (maintainer reference)

Machine-readable `stage` values returned by `crwd_db` action `get_user_gig_status`
and injected as `[CRWD gig context]` on Chatwoot gig-related turns.

## Terminology — three distinct concepts, don't cross them

- **Accepted / acceptance** — the member is let into the gig, pre-work. Tracked
  by `isAccepted` on `added_crwd_members`.
- **Approved / approval** — proof validated, cleared for payout. **Not a
  persisted field** — computed live each call from whether every required
  `proof_submissions` artifact is accepted, plus `hasPaid`. Only the
  `awaiting_payout` stage's `next_step` legitimately says "approved."
- **A single proof artifact's verdict** (`proof_submissions.status`) — one
  receipt/review/UGC submission's validation result. Always "accepted" /
  "rejected" / "needs_human" — never "approved," which is reserved for the
  payout concept above. The `progress` dict's `receipt_accepted` /
  `review_accepted` keys are this concept, not payout approval.

## Enrollment gate (`added_crwd_members`)

- **`isAccepted: false`** → Request Pending Acceptance (not in progress yet)
- **`isAccepted: true`** → IN PROGRESS (member may purchase, submit proof, etc.)
- **`isApproved` on membership is not used anywhere** — not for enrollment, not
  for payout; Hermes only ever echoes it back, never branches on it. (CRWD's
  own backend does appear to use "approved" for enrollment in some raw values —
  e.g. `user_product_purchases.source: "join_approved"`, and a legacy
  `added_crwd_members.status: "Approved"` value treated as an enrolled
  synonym — those are outside Hermes's control and are not renamed; only
  Hermes-authored prose/fields follow the contract above.)

| Stage | Meaning |
|-------|---------|
| `request_pending_acceptance` | Applied; `isAccepted` is false — waiting for admin |
| `rejected` | Membership has `rejectionReason` — hand off |
| `need_purchase` | Accepted into gig, no `user_product_purchases` row |
| `need_receipt` | Product assigned; no accepted chat receipt proof yet |
| `receipt_review` | Chat receipt stored as `needs_human` |
| `receipt_rejected` | Chat receipt `rejected` (no later accept) — hand off |
| `need_review` | Receipt accepted; review/UGC still outstanding |
| `review_review` | Chat review/UGC stored as `needs_human` |
| `review_rejected` | Chat review/UGC `rejected` — hand off |
| `awaiting_payout` | All required chat proofs accepted — **approved for payout**, not yet marked paid |
| `paid` | `hasPaid` is true on membership |

Progress sources:

- Enrollment / payout: `added_crwd_members`, `user_product_purchases`, `crwds`
- Receipt / review / completion: **`proof_submissions` only** (not app
  `gig_store_orders` / `gig_product_reviews` / `order_receipt_reviews`)

App-only receipt uploads do not advance coach stage until a matching chat
`store_proof` exists.
