# Proof requirements — look them up, per gig

Getting proof right is what keeps a submission from being rejected. **"Missing required
deliverables" is the single biggest proof rejection reason in the data** — members sending
less than the gig wanted. So name the **exact** proof this gig asks for, and never recite a
generic list.

## Look up the gig's requirements — don't guess from the gig type

**Proof requirements are per store, not per gig type**, and they vary widely *within* a
type. Some live gigs want only a receipt; others want a receipt plus a review. Some online
gigs want a review link; others don't. A gig-type rule cannot be right.

Call `crwd_db` `get_gig_details(query=<gig id or name>, full=true)`. Every store in the
result carries a **`requirements`** dict — that is the proof spec, and it is the same source
`crwd-proof-validator` checks the submission against:

| Flag | Ask the member for |
|---|---|
| `requires_receipt` | A clear receipt photo showing the product line item and store |
| `requires_order_id` | The order/transaction number (usually on the receipt or order page) |
| `requires_review_receipt` | A screenshot of the review they left |
| `requires_review_link` | **A screenshot of their review — not a link.** Legacy flag name; see below |
| `requires_review_rating` | A star rating with the review |
| `requires_store_address` | Which store location they bought at |
| `requires_ugc_post` | A link to the UGC video/photo they posted |
| `requires_tracking_id` | The shipment tracking number |

Name exactly the flags set to `true`. Nothing more, nothing less.

**`requires_review_link` does not mean a link.** Only a `review_screenshot`
satisfies it, at every store: Target's "review link" is the product page every
reviewer shares, and Amazon's permalink needs a login, so neither can be read. Ask
for a shot showing **the product, their username, the date, and the review** — never
send a member hunting for a permalink.

**Ignore `type_of_work_proof`** — it is unset on almost every gig.

## Why the lookup matters

Reciting a gig-type list actively harms members. In the real data:

- **Live gigs are advised to send a "UGC content link" — and `ugc_post_link` has never been
  submitted, on any gig.** Only a handful of stores set `requires_ugc_post`.
- **Two-thirds of live-gig submissions carry a review**, which the old gig-type list
  didn't mention at all.
- Most online gigs require a **review** and a **rating**, neither of which appeared in
  the old list.

A member who follows a wrong list sends the wrong thing, gets "missing required
deliverables", and needs a human. Reading the flags costs one tool call.

## Rough shape (illustration only — the flags decide)

- **Live (`gig_type: "irl"`)** — receipt is effectively always required; a review receipt is
  common; store address and UGC are occasional.
- **Online (`web_based`)** — receipt, order id, review receipt, review, and rating are
  all common.

Use this only to sanity-check what you read. **When this text and the `requirements` dict
disagree, the dict is right.**

## Special requirements

Some gigs need **two purchases with two different payment methods**. Both receipts are
required, and the payment methods must genuinely differ — call this out explicitly. Check the
gig terms; it is not expressed as a `requires_*` flag.

## Where proof goes

Proof is uploaded **directly in the coach chat (this conversation) as a message/attachment** —
receipts, review screenshots, and UGC links all go here, not into the CRWD app.
`crwd-proof-validator` picks up the submission and replies with the verdict.

## Rejections are a human's job

If a submission is **rejected**, do **not** guess the reason or coach the member through a
resubmission. Let them know kindly, then **hand off** so a person can explain and walk them
through the fix. See `crwd-handoff`.

Telling a member what a gig **requires** is not the same as telling them why something was
**rejected**. Requirements are public — they're on the gig page. Rejection reasons are not.
