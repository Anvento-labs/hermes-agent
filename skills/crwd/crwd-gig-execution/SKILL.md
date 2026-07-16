---
name: crwd-gig-execution
description: "Walk a CRWD member through doing a gig end to end — buying the product (with buy links), meeting special requirements, creating the UGC content, and submitting the right proof so it isn't rejected. Use when a member asks how to do a gig, where to buy, what content to make, or what proof to submit."
version: 1.0.0
metadata:
  hermes:
    tags: [crwd, gig, execution, buy, product, ugc, content, proof, receipt, submission]
    related_skills: [crwd-gig-discovery, crwd-payment-status, crwd-reference, crwd-handoff, crwd-proof-validator]
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
   The type tells you where they shop, not what to submit — the store `requirements` flags do
   that (step 6). Don't use `type_of_work_proof`; it is unset on nearly every gig. Hand off if
   what's required is unclear.
2. **Paste linked `name` / `gig_name` verbatim.** `get_gig_details` and `get_user_gig_status`
   return those as `[Title](…/my-gigs/<_id>)`. Copy the field as-is so the title is clickable — do **not** also append a bare URL. Full
   detail: `skill_view("crwd-reference", "references/gig-lifecycle.md")`.
3. **Surface every product + buy link.** Prefer `get_user_products` with `crwd_id`
   (or `get_gig_details` / status `products[]`) so multi-SKU gigs list all items —
   not only legacy `buy_link`. Render each as `[Product Name](product_url)` on its
   own line (clickable product name). Never substitute `gig_url`. Pass the member
   `user_id` from `[CRWD member]` context. Include buy links by default whenever
   a product is involved.
   - **If you mention a link, show it.** Never say *"order it with the gig's
     link"*, *"use the buy link"*, or *"order it through the link"* without the
     real `product_url` rendered in that same message. A member can't click a link
     you only described. Any turn that walks through buying/ordering a product must
     paste the actual `[Product Name](product_url)` right there.
4. **Live gig steps:** go to the store (see `crwd-gig-discovery` if they need to find it),
   buy the product, and **call out any special requirement precisely** — e.g. *two purchases
   with two different payment methods* means two separate transactions and two receipts.
   Then do whatever **this gig's `requirements`** actually ask for (step 6): most live gigs
   want a review; only a few want UGC. Only send them off to film a natural, non-scripted
   video/photo showing the product when `requires_ugc_post` is true — don't ask for content
   the gig never wanted.
5. **Online gig steps:** order the product (commonly Amazon) via the buy link, then leave a
   review per the gig's instructions — including a star rating when `requires_review_rating`
   is set. When `requires_review_link` is set, ask them for a **screenshot of the review**,
   never a link: no store gives a review a usable URL (Target's is the product page,
   Amazon's needs a login), so a link cannot be accepted and asking for one strands them.
   Tell them what the shot needs: **the product, their username, the date, and the review**.
6. **Proof — look up what THIS gig requires, then tell them exactly that.**
   Proof is submitted by uploading it **right here in this chat as a message/attachment** —
   not in the CRWD app. Send them here to `crwd-proof-validator`, which owns the reply to a
   submission.
   - **Read the gig's `requirements`** from `get_gig_details(query=<gig>, full=true)` — each
     store carries `requires_receipt`, `requires_order_id`, `requires_review_receipt`,
     `requires_review_link`, `requires_review_rating`, `requires_store_address`,
     `requires_ugc_post`. **Name exactly the ones set to `true`** — nothing more, nothing less.
   - **Never recite a proof list from the gig type.** Requirements are per store and vary
     within a type. `crwd-proof-validator` checks the submission against these same flags, so
     a generic list gets the member rejected for "missing required deliverables" — the biggest
     proof rejection reason there is. In particular, do **not** ask for a UGC link unless
     `requires_ugc_post` is true, and do **not** forget the review when
     `requires_review_link` is true — asking for it as a **screenshot**, since the flag's
     name is legacy and a link is never accepted.
   - Two-purchase gigs (two different payment methods) need **both** receipts, on genuinely
     different payment methods.
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
- **Never mention "the link" without providing it.** Telling the member to *"order
  it with the gig's link"* while showing no link leaves them with nothing to click.
  Any turn that references ordering through a link must render the real
  `product_url` in that same message.
- Rejected submissions always go to a human. Never coach a resubmission.
- Never replace linked `name` / `gig_name` with a plain title or `Title — url` —
  paste the markdown field verbatim and do not append a bare URL.

## Verification

- Product name + real buy link came from `get_user_products`.
- Any special requirement (e.g. two payment methods) was stated precisely.
- The member knows the exact proof to submit for their gig type.
- Rejections were handed off, not self-diagnosed.
- Gig titles were paste-ready markdown links from `crwd_db`, with no trailing bare URL.
