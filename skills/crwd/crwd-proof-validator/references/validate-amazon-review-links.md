# Validate: Amazon review links

Loaded from `crwd-proof-validator`. The driver owns the flow, the verdict, and the
reply — this file only tells you what to look for in an **Amazon review link**.

Proof type: `amazon_review_link`. This is the single most common link members
submit. For a review *screenshot*, use `validate-reviews.md`.

## What this proof is

A link to the review the member posted on an Amazon listing. Its identifier is the
**review id**, not the URL:

```
https://amazon.com/gp/customer-reviews/RC890PZQT2RLK?ref=pf_ov_at_pdctrvw_srp
                                       ^^^^^^^^^^^^^ the key
```

### Get to a real link first

Two things stand between you and that id, both routine in real submissions:

**Links arrive inside prose.** Members paste sentences, not URLs:

```
Review on Amazon: Worth it https://a.co/d/0i8Mmj2q
Check out this page from amzn1.account.AGM3DFQ… https://a.co/d/07PHVHtk
```

Pull the URL out of the text before doing anything else.

**Short links carry no id.** `a.co/d/0i8Mmj2q` is a redirect — there is no review id
in it, and the normalizer correctly refuses to key on it. **Open it and use the
resolved URL.** Same for any `amzn.to` form.

A link you cannot resolve is `no_identifier` → `needs_human`. Never guess a key from
a short link, and never key on the `a.co` URL itself — two different reviews can
share neither, but a member re-sharing gets a different short link each time.

Watch for links that aren't reviews at all: `a.co/d/…` often resolves to the
**product page**, not the member's review. A product page is not proof of a review —
coach for the review permalink (or a screenshot), exactly as with Target.

## Open it

`browser_navigate` if available, otherwise `web_extract`. **Never approve a link you
could not open.** Amazon may show a login wall or a bot check — that is
`needs_human` after one coaching ask, not a rejection and not something to work
around.

## Extract

| Field | Notes |
|---|---|
| `platform` | `amazon` |
| `product` | Title / ASIN on the review |
| `rating` | Stars |
| `review_text` | Verbatim |
| `handle` | Reviewer display name |
| `posted_at` | Review date |

## Checks, in order

### 1. Reachable → `link_unreachable`

404, removed, or private. **Amazon aggressively removes incentivized reviews** — a
disappeared review most often means it tripped Amazon's own filters, which is a
platform outcome, not proof the member cheated. `needs_human`, never a fraud
rejection.

### 2. Right product → `wrong_product`

The ASIN/product on the review matches the gig's product. Fuzzy-match the title;
prefer the ASIN when visible.

### 3. Identity → `link_not_owned`

The reviewer display name should plausibly be the member. Amazon exposes little, and
display names are often nicknames — **weak signal**. A mismatch is `needs_human`
unless it's obviously someone else.

### 4. Verified Purchase

When the gig requires a real purchase. Missing badge → `needs_human`, especially if
a separate receipt already covers the purchase; the badge can lag or be absent for
legitimate reasons.

### 5. Rating / content rule → `content_mismatch`

Only when the gig sets one — check the store's `requires_review_rating` and the gig
terms.

### 6. Date window → `date_outside_gig_window`

`posted_at` inside the gig's window. A `null` bound is unbounded.

### 7. Clean → `clean_match`, accepted

## Anomalies worth noticing

These don't decide a verdict alone, but they're what a link proof can reveal that a
screenshot cannot:

- **Review posted before the purchase date** on the member's receipt. A review of a
  product you hadn't bought yet is the clearest anomaly available here.
- **Review posted minutes after the order** — possible, but odd for a product that
  ships.
- **Handle unrelated to the member** and unrelated to any name on the receipt.
- **Boilerplate text** identical or near-identical to another submission.
- A reviewer profile with a burst of reviews all posted the same day.

Any of these → `needs_human` with the detail in the note. Several together, plus a
content or identity failure, may justify `suspected_edited`.

## Confidence

| Band | When |
|---|---|
| `high` | Page opened, review id read, ASIN matches, Verified Purchase present, handle plausible, in window |
| `medium` | Opened and checks pass, but something is inconclusive — no badge, ambiguous handle, or the id came from a resolved short link |
| `low` | Page wouldn't load, or you're working from prose you couldn't resolve. **Coach first**; `needs_human` if that fails |

## Coach, don't accuse

Real members routinely say *"I'm unsure how to copy the link"*, *"How do I get the
link?"*, *"I'm waiting for Amazon to post my review"*. Those are mechanics problems,
not fraud.

Allowed:

- "That link takes me to the product page rather than your review — on the review
  itself there's usually a permalink. Could you grab that one?"
- "I couldn't open that link on my end — could you paste it again?"
- "No rush if Amazon hasn't published it yet — send the link once it's live."
- "Which gig is this review for?"

**If they genuinely can't get the link, ask for a screenshot** — never leave them
stuck. After a round or two: *"No problem — send me a screenshot of your review
instead, with your username showing."* Store it as `review_screenshot`. On Amazon it
does **not** satisfy `requires_review_link` (the permalink is what proves the review
is theirs), so the proof lands `needs_human` and a person decides. See
`validate-reviews.md`.

**Never** ask a question that reveals a finding. "Is this your review?", "Did you
actually buy this?", or "Why is the date before your receipt?" each disclose the
check that failed. Those are **verdicts** — record them, hand off, reply neutrally.
