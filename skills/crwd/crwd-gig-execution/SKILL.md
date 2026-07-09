---
name: crwd-gig-execution
description: "Walk a CRWD member through doing a gig end to end — buying the product (with buy links), meeting special requirements, creating the UGC content, and submitting the right proof so it isn't rejected. Use when a member asks how to do a gig, where to buy, what content to make, or what proof to submit."
version: 1.0.0
metadata:
  hermes:
    tags: [crwd, gig, execution, buy, product, ugc, content, proof, receipt, submission]
    related_skills: [crwd-gig-discovery, crwd-payment-status, crwd-reference, crwd-handoff]
    requires_toolsets: [crwd]
    config:
      - key: crwd.app_base_url
        description: Base URL of the CRWD member web app, used to build gig deep links (/my-gigs/<gig_id>)
        default: "https://live-staging.joincrwd.com"
        prompt: "CRWD app base URL (e.g. https://app.joincrwd.com)"
---

# CRWD Gig Execution

Get the member from "approved" to "proof submitted" — buying, content, and proof, in one
skill (proof is just the tail of doing the gig).

## When to Use

- "How do I do this gig?" / "What are the steps?"
- "Where do I buy the product?" / "What's the buy link?"
- "What kind of video/photo do I need to make?"
- "What proof do I submit?" / "What do I upload?"

## Procedure

0. **Load enrolled gig status.** Call `get_user_gig_status` with `user_id` from the
   `[CRWD member]` context line. Use each gig's `next_step` as the primary answer when
   the member asks about gigs they are in. Re-call with `gig_name` or `crwd_id` when they
   name a specific gig.
1. **Confirm the gig and its type** (live `irl` vs online) with `crwd_db` `get_gig_details`.
   If it's cleanly neither, go by the gig's real `type_of_work_proof`/requirements rather than
   forcing it into one bucket — and hand off if what's required is unclear.
2. **Paste linked `name` / `gig_name` verbatim.** `get_gig_details` and `get_user_gig_status`
   return those as `[Title](…/my-gigs/<_id>)`. Copy the field as-is so the title is clickable — do **not** also append a bare URL. Full
   detail: `skill_view("crwd-reference", "references/gig-lifecycle.md")`.
3. **Surface every product + buy link.** Prefer `get_user_products` with `crwd_id`
   (or `get_gig_details` / status `products[]`) so multi-SKU gigs list all items —
   not only legacy `buy_link`. Render each as `[Product Name](product_url)` on its
   own line (clickable product name). Never substitute `gig_url`. Pass the member
   `user_id` from `[CRWD member]` context. Include buy links by default whenever
   a product is involved.
4. **Live gig steps:** go to the store (see `crwd-gig-discovery` if they need to find it),
   buy the product, and **call out any special requirement precisely** — e.g. *two purchases
   with two different payment methods* means two separate transactions and two receipts.
   Then create the content: a natural, non-scripted UGC video/photo showing the product
   clearly, matching the gig's "approved concepts."
5. **Online gig steps:** order the product (commonly Amazon) via the buy link, then leave a
   review per the gig's instructions.
6. **Proof — tell them the exact format** so it isn't rejected:
   - Live: receipt photo (readable, showing the product), store location, and the UGC content
     link. Both receipts if there's a two-purchase requirement.
   - Online: order screenshot + review screenshot.
   - Full detail: `skill_view("crwd-reference", "references/proof-requirements.md")`.
7. **Check submission status** if they ask "did it go through?" — `get_user_receipts` shows
   receipt/proof validation state (pass/fail + reason).
8. **If a submission is rejected → hand off** (`crwd-handoff`). Do not guess the rejection
   reason or coach a resubmission yourself — that's a human's job.

## Pitfalls

- Payout is **not** reimbursement — the member keeps what they bought (see
  `references/payments-dot.md`). Don't imply they'll be refunded for the purchase.
- Don't paraphrase a buy link or requirement — quote the real product URL and the exact
  requirement from the gig data.
- Rejected submissions always go to a human. Never coach a resubmission.
- Never replace linked `name` / `gig_name` with a plain title or `Title — url` —
  paste the markdown field verbatim and do not append a bare URL.

## Verification

- Product name + real buy link came from `get_user_products`.
- Any special requirement (e.g. two payment methods) was stated precisely.
- The member knows the exact proof to submit for their gig type.
- Rejections were handed off, not self-diagnosed.
- Gig titles were paste-ready markdown links from `crwd_db`, with no trailing bare URL.
