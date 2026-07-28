# Gig stages (maintainer reference)

Machine-readable `stage` values returned by `crwd_db` action `get_user_gig_status`
and injected as `[CRWD gig context]` on Chatwoot gig-related turns.

## Enrollment gate (`added_crwd_members`)

- **`isAccepted: false`** → Request Pending Approval (not in progress yet)
- **`isAccepted: true`** → IN PROGRESS (member may purchase, submit proof, etc.)
- **`isApproved` on membership is not used** for enrollment.

| Stage | Meaning |
|-------|---------|
| `request_pending_approval` | Applied; `isAccepted` is false — waiting for admin |
| `rejected` | Membership has `rejectionReason` — hand off |
| `need_purchase` | Accepted into gig, no `user_product_purchases` row |
| `need_receipt` | Product assigned; no accepted chat receipt proof yet |
| `receipt_review` | Chat receipt stored as `needs_human` |
| `receipt_rejected` | Chat receipt `rejected` (no later accept) — hand off |
| `need_review` | Receipt accepted; review/UGC still outstanding |
| `review_review` | Chat review/UGC stored as `needs_human` |
| `review_rejected` | Chat review/UGC `rejected` — hand off |
| `awaiting_payout` | All required chat proofs accepted, payout not yet marked paid |
| `paid` | `hasPaid` is true on membership |

Progress sources:

- Enrollment / payout: `added_crwd_members`, `user_product_purchases`, `crwds`
- Receipt / review / completion: Hermes **`proof_submissions` only** (not app
  `gig_store_orders` / `gig_product_reviews` / `order_receipt_reviews`)

App-only receipt uploads do not advance coach stage until a matching chat
`store_proof` exists.
