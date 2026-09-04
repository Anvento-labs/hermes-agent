# Gig stages (maintainer reference)

Machine-readable `stage` values returned by `crwd_db` action `get_user_gig_status`
and injected as `[CRWD gig context]` on Chatwoot gig-related turns.

## Terminology — three distinct concepts, don't cross them

- **Accepted / acceptance** — the member is let into the gig by CRWD. Tracked by
  `isAccepted` on `added_crwd_members`. **No longer gates the work** — see below.
- **Approved / approval** — proof validated, cleared for payout. Persisted on
  `added_crwd_members.isApproved`, which **Hermes itself writes**: `store_proof`
  flips it true the moment a member's last required artifact is accepted,
  whatever `isAccepted` says. Only the `awaiting_payout` and
  `proof_complete_pending_acceptance` stages' `next_step` say "approved."
- **A single proof artifact's verdict** (`proof_submissions.status`) — one
  receipt/review/UGC submission's validation result. Always "accepted" /
  "rejected" / "needs_human" — never "approved," which is reserved for the
  payout concept above. The `progress` dict's `receipt_accepted` /
  `review_accepted` keys are this concept, not payout approval.

## Acceptance does not gate progression (`added_crwd_members`)

A member marked Interested — by lead ingest, Coach `add_user_gig_interest`
(including a matching campaign code), or the same action mid-chat — buys the
product and submits proof on exactly the same path as an accepted member. There is
no "wait to be accepted first" step, and no stage that parks them.

`isAccepted` still decides three *other* things, all unchanged:

- **Capacity** — only `isAccepted: true` rows count against a gig's `number_of_people`.
- **App-tab bucketing** — `get_user_gigs` (Active) vs `get_waitlisted_gigs`
  (Pending Approval), mirroring what the member's own CRWD app screen shows.
- **CRWD's own logical status** — their backend reads `accepted` when `isAccepted`,
  else `approved` when `isApproved`, else `pending`.

**`isAccepted: false` + `isApproved: true` is a real, expected row**: the member did
all the work and CRWD hasn't stamped acceptance yet. It is **not** acceptance —
answer "am I accepted?" from `isAccepted` alone, never narrate `isApproved` to a
member, and never tell anyone their membership was approved.

(CRWD's backend also uses "approved" for enrollment in some raw values —
`user_product_purchases.source: "join_approved"`, a legacy
`added_crwd_members.status: "Approved"` treated as an enrolled synonym. Those are
outside Hermes's control and are not renamed; only Hermes-authored prose follows
the contract above.)

| Stage | Meaning |
|-------|---------|
| `rejected` | Membership has `rejectionReason` — hand off |
| `need_purchase` | No `user_product_purchases` row yet (acceptance irrelevant) |
| `need_receipt` | Product assigned; no accepted chat receipt proof yet |
| `receipt_review` | Chat receipt stored as `needs_human` |
| `receipt_rejected` | Chat receipt `rejected` (no later accept) — hand off |
| `need_review` | Receipt accepted; review/UGC still outstanding |
| `review_review` | Chat review/UGC stored as `needs_human` |
| `review_rejected` | Chat review/UGC `rejected` — hand off |
| `proof_complete_pending_acceptance` | All required chat proofs accepted, `isAccepted` still false — approved, awaiting CRWD's acceptance + payout |
| `awaiting_payout` | All required chat proofs accepted — **approved for payout**, not yet marked paid |
| `paid` | `hasPaid` is true on membership (regardless of `isAccepted`) |

`proof_complete_pending_acceptance` and `awaiting_payout` carry the **same
`next_step` wording on purpose**. The member's situation is identical — proof in,
approved, money pending — and the outstanding acceptance is internal. Never add
that detail back in: telling someone who already bought the product and sent proof
that they were never accepted reads as a problem, and it contradicts what
`crwd-lead-intro` already told them.

## Membership writes

`added_crwd_members` has exactly two Hermes writers:

- `_add_user_gig_interest` — inserts the initial Interested row (lead ingest,
  Coach `add_user_gig_interest` after `lookup_campaign_code`, or the same action
  mid-chat).
- `_mark_membership_approved` — called only from `store_proof` on gig-proof
  completion. Sets `isApproved` (and `updatedAt`) and nothing else; skips rows that
  already carry a `rejectionReason`, since CRWD reads rejected as *rejectionReason
  set AND not isApproved*. `isAccepted`, `isCompleted`, `hasPaid` and `status` stay
  CRWD's to set.

Progress sources:

- Enrollment / payout: `added_crwd_members`, `user_product_purchases`, `crwds`
- Receipt / review / completion: Hermes **`proof_submissions` only** (not app
  `gig_store_orders` / `gig_product_reviews` / `order_receipt_reviews`)

App-only receipt uploads do not advance coach stage until a matching chat
`store_proof` exists.
