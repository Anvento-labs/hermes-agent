# Gig stages (maintainer reference)

Machine-readable `stage` values returned by `crwd_db` action `get_user_gig_status`
and injected as `[CRWD gig context]` on Chatwoot gig-related turns.

## Enrollment gate (`added_crwd_members`)

- **`isAccepted: false`** → Request Pending Approval (not in progress yet)
- **`isAccepted: true`** → IN PROGRESS (member may purchase, submit proof, etc.)
- **`isApproved` on membership is not used** for enrollment — only proof-level
  `isApproved` on `gig_store_orders` / `gig_product_reviews` matters for submissions.

| Stage | Meaning |
|-------|---------|
| `request_pending_approval` | Applied; `isAccepted` is false — waiting for admin |
| `rejected` | Membership has `rejectionReason` — hand off |
| `need_purchase` | Accepted into gig, no `user_product_purchases` row |
| `need_receipt` | Purchased, no receipt uploaded |
| `receipt_review` | Receipt submitted, awaiting approval |
| `receipt_rejected` | Receipt rejected — hand off |
| `need_review` | Receipt approved, review/UGC not submitted |
| `review_review` | Review submitted, awaiting approval |
| `review_rejected` | Review rejected — hand off |
| `awaiting_payout` | All proof approved, payout not yet marked paid |
| `paid` | `hasPaid` is true on membership |

Progress sources: `added_crwd_members`, `user_product_purchases`,
`gig_store_orders`, `gig_product_reviews`, `order_receipt_reviews`.
