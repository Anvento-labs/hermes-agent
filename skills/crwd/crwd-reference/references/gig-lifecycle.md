# Gig lifecycle — browse → paid

Every member goes through the same flow:

1. **Browse** — gigs are listed with a payout, a deadline ("Due in X days"), an estimated
   time ("Takes X hours"), and a source (Walmart, Amazon, JoinCRWD, etc.).
2. **Apply** — the member applies for a gig in the CRWD app/website.
3. **Get approved** — CRWD or the brand approves the application before the member can start.
4. **Perform the gig** — see gig types below.
5. **Submit proof** — receipts, review screenshots, UGC video/photo links, uploaded
   **into the coach chat (this conversation) as messages/attachments**, where the
   `crwd-proof-validator` skill reviews them. Proof is not uploaded in the CRWD app.
   Reviews are always proved by **screenshot**, never by link.
6. **Get paid** — payment is triggered once proof is reviewed and verified.

## Gig types

**Live gigs (in-store):**
- Member goes to a physical store (often Walmart or Target), finds the product, buys it —
  sometimes with a specific requirement like "two purchases with two different payment
  methods."
- What they do next, and what they submit, is whatever that gig's `requirements` say. Most
  live gigs want a receipt and a review; only a few want UGC content.

**Online gigs:**
- Member orders the product (commonly via Amazon) and leaves a review. Most online gigs want
  a receipt, the order id, a review screenshot, and a rating — but again, read the gig.
  `requires_review_link` is a legacy name and also takes a screenshot.
- Payment process starts once that proof is submitted and verified.

**Proof is per gig, never per gig type.** Requirements live on each store as `requires_*`
flags (`get_gig_details(..., full=true)`) and vary widely within a type. Reciting a gig-type
list is how members end up rejected for "missing required deliverables" — the biggest proof
rejection reason in the data. See
`skill_view("crwd-reference", "references/proof-requirements.md")`.

Every gig has a **payout**, a **deadline**, and an **estimated time to complete**. Be precise
about these when asked, and never guess if you don't have the real numbers in front of you —
look them up with `crwd_db`.

**Live (`gig_type: "irl"`) and online (`web_based`) are the two types in the data.** If a gig
doesn't fit either cleanly — a sampling run, a foot-traffic activation, or anything with
unusual requirements — don't force it into the wrong bucket. Describe it from its **actual**
data (payout, source, and the store `requirements` flags) and, if what's being asked for is
genuinely unclear, hand off rather than guess (`crwd-handoff`).

Don't reach for `type_of_work_proof` — it is unset on nearly every gig. The `requirements`
flags are the proof spec.

## Gig link

Every gig has a page in the CRWD app at:

```text
<crwd.app_base_url>/my-gigs/<gig_id>
```

e.g. `https://live-staging.joincrwd.com/my-gigs/6a3411008972fa2d14ce8fe0`. Runtime
`crwd_db` payloads set `name` / `gig_name` to markdown `[Title](that URL)` when
`CRWD_APP_BASE_URL` is set, keep `gig_url` as the bare URL, and keep the human
title in `name_plain` / `gig_name_plain`. The id comes straight from whichever
`crwd_db` action returned the gig — never fabricate or guess an id.

Gig-facing skills paste `name` / `gig_name` verbatim so the **title is the
clickable link**. Do not also append a bare URL after the name, and do not
rebuild `/explore/` URLs. (Gig-name markdown links are the allowed exception to
the coach "no markdown formatting" style.)
