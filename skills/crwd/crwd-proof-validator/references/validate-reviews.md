# Validate: review screenshots

Loaded from `crwd-proof-validator`. The driver owns the flow, the verdict, and the
reply — this file only tells you what to look for in a **review screenshot**.

Proof type: `review_screenshot`. For a review submitted as an Amazon **link**, use
`validate-amazon-review-links.md`.

## What this proof is

A screenshot of a review the member actually posted: the product, their star
rating, their words, and — critically — **their username**.

Its identifier is weak by nature. Use the review/order id if one is visible;
otherwise form a **composite key**: `platform + product + reviewer handle`. That is
why the handle must be in frame. Without it there is no defensible identifier →
`no_identifier` → `needs_human`.

### Target reviews land here

Target has no per-review URL. A link like `target.com/p/hj/-/A-95279869` is the
**product page** — every member reviewing that product submits the identical link,
and that exact URL already appears more than once in real submissions. Keying on it
would auto-reject the second honest reviewer.

So a bare Target link is **not proof**. Coach the member for a screenshot showing
their review, the rating, and their username, then validate it here. The product id
in the URL (`A-95279869`) is still useful — it confirms *which* product — it just
cannot identify *whose* review it is. The same reasoning applies to any store whose
"review link" resolves to a product page rather than a review.

A Target screenshot therefore also satisfies that gig's `requires_review_link` —
it's the only proof of the review a member *can* produce. **This does not carry to
Amazon**, which does give reviews their own URL: there a screenshot proves the
review exists but never stands in for the link, and the link is still owed.

### Any other store: check, then default to a screenshot

Only two stores are settled: **Amazon has** per-review permalinks, **Target has
not**. Walmart, Whole Foods, Sprouts, Raley's, Apple and the rest are **unknown** —
no member has ever submitted a review link from one, so we have no evidence either
way.

When a gig at an unfamiliar store sets `requires_review_link`:

1. **If in doubt, go and look.** Open the store's review section for that product
   with `web_extract` / `browser_navigate` and see whether an individual review has
   its own URL, or whether every review lives on the product page.
2. **If you cannot establish that it does — ask for a screenshot instead.** Coach
   for a capture showing the review, the rating, and the username, and validate it
   as `review_screenshot`.

**Default to the screenshot whenever it's unclear.** Demanding a permalink from a
store that may not issue one strands an honest member on a proof that does not
exist — the same trap as the Target product page. `check_gig_proof_completion`
already resolves unknown stores this way: its `accepts` list includes
`review_screenshot` for every store except Amazon, so a screenshot completes the gig.

If you do establish that a store issues real per-review URLs, say so in the
`crwd_handoff` note so the list can be tightened — don't just move on.

### The member can't find the link — ask for a screenshot, always

This is the single most common real case. Members say *"I'm unsure how to copy the
link"*, *"Can't copy link. I have a photo"*, *"There's no link"*, *"I use the target
app, idk how to find a link"*. They are stuck on mechanics, not cheating.

**Never leave them at a dead end.** Coach for the link once or twice; if they can't
produce it, take the screenshot. Where that lands depends on the store:

- **Target / unknown store** → the screenshot **satisfies** `requires_review_link`.
  The gig completes. Nothing more is owed.
- **Amazon** → store the screenshot as `review_screenshot` (it does prove the review
  exists), but `requires_review_link` **stays outstanding** → the proof is
  `needs_human`, and a person decides whether to take it instead of the link.

Why Amazon is different: it *does* give every review its own URL, and that URL is
what proves the review is **theirs** — a screenshot could be of anyone's review.
*"I can't find the link"* is also precisely the sentence a fraudster learns to say
once it starts working. So a human clears it, not the bot. The member still gets a
route either way; they are never told why, and never left stuck.

## Extract

Pass these as `extracted` on `store_proof`:

| Field | Notes |
|---|---|
| `platform` | Amazon, Target, Google, … |
| `product` | Title as shown |
| `rating` | Number of stars |
| `review_text` | Verbatim |
| `handle` | Reviewer display name — required for the composite key |
| `posted_at` | Review date as shown |

## Checks, in order

Stop at the first that fires.

### 1. Wrong product → `wrong_product`

The review is attached to a different product than the gig's. Match fuzzily against
the gig's `stores[].products[].name` — display titles are long and marketing-heavy
(`carpe-women-s-100hr-sweat-odor-control-antiperspirant-deodorant-mountain-breeze`),
so match on distinctive tokens, not the whole string. Unsure → `needs_human`.

### 2. Rating or content rule → `content_mismatch`

Only when the gig sets one. Check the store's `requirements`: `requires_review_rating`
means a rating is mandatory. If the gig requires the review to cover specific points
and it doesn't, that's `content_mismatch`.

Never demand a *positive* rating on your own initiative — an honest low-star review
is a legitimate review unless the gig explicitly says otherwise.

### 3. Identity → `link_not_owned`

The visible handle should plausibly be the member's. Handles are frequently nothing
like a legal name, so this is a **weak** signal: treat a mismatch as `needs_human`
unless it is obviously another person. Never reject on a nickname.

### 4. Screenshot authenticity → `suspected_edited`

The chrome must be the platform's real UI:

- Fonts and weights match the platform — Amazon and Target each have a house font
- Star glyphs are the platform's own shape, not a generic Unicode `★`
- Layout, badge placement, and spacing are plausible for that page
- Text baseline and anti-aliasing are **consistent across the image**

Edited text usually betrays itself **within a single line**: one word rendered in a
different weight, or crisply anti-aliased text sitting in an otherwise compressed
screenshot. That local inconsistency is the tell, not overall quality.

Also: a phone screenshot normally has a status bar with a plausible time and battery.
Its absence is common (cropping) and proves nothing on its own.

Require **several** signals before `suspected_edited`. One oddity → `needs_human`.

### 5. Scripted or duplicated text → `content_mismatch`

Review text identical to another member's submission suggests a copy-paste
template. Use `find_proof` if you have an id to check against history. One
suspicious phrasing is not proof — hand off rather than reject.

### 6. Clean → `clean_match`, accepted

## Metadata signals

Call `crwd_verify_screenshot(image_url)`. Returns field values only — no score:

`format`, `width`, `height`, `Make`, `Model`, `Software`, `DateTimeOriginal`,
`ISO`, `FocalLength`, `ExposureTime`, `FNumber`, `has_gps`, `has_makernote`,
`png_text`, `error`.

| Pattern | Means |
|---|---|
| Camera tags null, `has_gps` false, `has_makernote` false, PNG/WEBP, `width`/`height` look like a device resolution | Consistent with a genuine screenshot → supports `high` |
| **Full camera EXIF + GPS + MakerNote on a claimed screenshot** | **The inversion signal.** A screenshot has no camera. This is a photo *of a screen*, or something re-encoded — worth a real look |
| `Software` names an image editor | Notable; see the caveat in `validate-receipts.md` — the editor list is our heuristic, not a tool output |
| `png_text` mentions Screenshot | Supporting, never required. **PNG-only** — `None` on a JPEG means nothing |
| `error` set, all null | Extraction failed. **No evidence** — judge on the pixels |

Empty metadata after a chat re-encode is **inconclusive, not fraud**. It lowers
confidence; it never rejects alone.

Note the asymmetry with receipts: for a camera receipt, *rich* camera metadata is
reassuring. For a screenshot, rich camera metadata is the opposite. Don't carry the
receipt heuristics over.

## Confidence

| Band | When |
|---|---|
| `high` | Product, rating, text, handle, and date all read clearly; UI consistent; metadata looks like a real screenshot |
| `medium` | Content reads fine but a signal is inconclusive — EXIF stripped, `error` set, or the handle is ambiguous |
| `low` | Handle or rating not legible, or UI signals conflict. **Coach first**; `needs_human` only if asking didn't resolve it |

## Coach, don't accuse

The single most common real case is a member who cannot produce a link:
*"I have a screenshot"*, *"Can't copy link"*, *"I don't have a link for the review"*,
*"I'm waiting for my review to publish"*. These are honest members stuck on
mechanics — coach them.

Allowed:

- "Could you send a screenshot that includes your username at the top? I can't see
  who posted it in this crop."
- "Mind capturing a bit more of the screen — I want to make sure I've got the whole
  review."
- "Which gig is this review for?"
- For a Target link: "Target doesn't give reviews their own link — could you send a
  screenshot of your review instead, with your username showing?"

**Never** ask a question that reveals a finding. "Is this your account?", "Is this
the right product?", or "Did you write this yourself?" each disclose the check that
failed. Those are **verdicts** — record them, hand off, reply neutrally.
