# Validate: review screenshots

Loaded from `crwd-proof-validator`. The driver owns the flow, the verdict, and the
reply — this file only tells you what to look for in a **review screenshot**.

Proof type: `review_screenshot` — **the only way a review is proved.**

## What this proof is

A screenshot of a review the member posted, showing four things **in one frame**:
the **product**, their **username**, the **review date**, and their **rating and
words**. The product stops them showing a real review of something else, the handle
ties it to them, the date places it in the gig window.

All four come from the pixels via `vision_analyze`. `crwd_db` validates none of it —
every check here is yours. A missing one is usually a bad crop: **coach, don't
judge**.

### Links are not proof, at any store

`target.com/p/hj/-/A-95279869` is the *product page* every reviewer shares. Amazon
does issue permalinks, but they need a login — an unopened link is an unread proof,
and an unread proof is never accepted.

A link is **not** a rejection and not `wrong_proof_type`: it's a prompt to ask for
the screenshot, and nothing stores until one arrives. Members saying *"can't copy
link"* or *"I use the app"* are offering you the thing you actually want. Never send
anyone hunting for a permalink.

### Identifier

**`{crwd_id}:{handle}:{review_date}`**, e.g. `69deb0781ca6038a3a1f6f8a:sarah_k:July 15, 2026`.
`crwd_db` slugifies it and nothing more — same gig + handle + date text → same key.

All three parts are required; the tool returns no key without them, and that refusal
is the point. Missing handle or date → **coach for a wider crop**, `no_identifier`
only if asking fails. Never invent a date or substitute a URL.

Two honest members on one gig have different handles, so they don't collide — while
a member passing off someone else's review reproduces that handle and date, landing
on the key the real reviewer holds. **Product is not in the key**: phones truncate
titles, so it would hash two ways for one review. It's a check, not an identifier.

## Extract (from the image)

Use `vision_analyze` on the attachment. Pass these as `extracted` on
`store_proof`:

| Field | Notes |
|---|---|
| `platform` | Amazon, Target, Google, … — from UI chrome |
| `product` | Title **as shown**, truncation and all — don't tidy it up |
| `rating` | Number of stars |
| `review_text` | Verbatim |
| `handle` | Reviewer display name — required for `proof_id` and the identity check |
| `posted_at` | Review date **as shown** — required for `proof_id` |

Pass `proof_id` as `{crwd_id}:{handle}:{posted_at}` using the strings you read off
the image — don't reformat the date; matching what's visible beats a calendar parse.
Record `product` as the pixels show it: `"Shroom Vroom Mushroom Coffee Cr…"` is a
truthful extraction, a tidied-up guess is not.

Also call `crwd_verify_screenshot(image_url)` and judge the metadata fields
yourself (see below).

## Checks, in order (all visual)

Stop at the first that fires. Every check below is from what you **see** on the
screenshot (plus gig context you already looked up) — not from `crwd_db`.

### 1. Product → `wrong_product`

**Not visible at all** → almost always a tight crop, not a trick. Ask for a wider
capture; `no_identifier` only if that fails. Never `wrong_product` for a product you
couldn't see — that's a rejection on the record for a crop.

**Visible → match it partially, on purpose.** Phone screenshots truncate, and gig
titles are long and marketing-heavy
(`carpe-women-s-100hr-sweat-odor-control-antiperspirant-deodorant-mountain-breeze`).
Match on **distinctive tokens** — brand and product line — against the gig's
`stores[].products[].name`. Not the whole string, not a percentage.

- `"Shroom Vroom Mushroom Coff…"` vs *Shroom Vroom Mushroom Coffee Creamer, French
  Vanilla, 16oz* → **match**. The tail is packaging detail.
- **Different brand or product line → `wrong_product`.** That's the real failure.
- Too few tokens to tell brands apart → `needs_human`.

**Truncation is never evidence.** Never reject because the size, flavour or count
was cut off. A variant mismatch you *can* fully read (gig wants French Vanilla, the
review says Hazelnut) → `needs_human` unless the gig's terms name the variant;
members buy what's on the shelf.

### 2. Date window → `date_outside_gig_window`

Read `posted_at` from the image, compare to the gig's `start_date` / `end_date` from
`get_gig_details`. A `null` bound is **unbounded**.

- **Unreadable or out of frame** → coach for a crop that includes it, then
  `no_identifier`. **Never** treat an unseen date as out-of-window.
- **Relative dates** ("2 days ago") are what most apps show. Resolve against today;
  near a window boundary, `needs_human` beats a coin flip.
- **Clearly outside** → `date_outside_gig_window`. A review posted before the gig
  opened existed before the member was asked for it — the strongest signal here.

### 3. Rating or content rule → `content_mismatch`

Only when the gig sets one. Check the store's `requirements`: `requires_review_rating`
means a rating is mandatory. If the gig requires the review to cover specific points
and it doesn't, that's `content_mismatch`.

Never demand a *positive* rating on your own initiative — an honest low-star review
is a legitimate review unless the gig explicitly says otherwise.

### 4. Identity → `link_not_owned`

The visible handle should plausibly be the member's. Handles are frequently nothing
like a legal name, so this is a **weak** signal: treat a mismatch as `needs_human`
unless it is obviously another person. Never reject on a nickname.

### 5. Screenshot authenticity → `suspected_edited`

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

### 6. Scripted or duplicated text → `content_mismatch`

Review text identical to another member's submission suggests a copy-paste
template. Use `find_proof` if you have an id to check against history. One
suspicious phrasing is not proof — hand off rather than reject.

### 7. Clean → `clean_match`, accepted

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
| `high` | Product, rating, text, handle, and date all in frame and legible; product matches on distinctive tokens; UI consistent; metadata looks like a real screenshot |
| `medium` | Content reads fine but a signal is inconclusive — EXIF stripped, `error` set, an ambiguous handle, or a **truncated title that matches as far as it goes**. Never a rejection |
| `low` | Handle, date, or rating not legible, product not visible, or UI signals conflict. **Coach first**; `needs_human` only if asking didn't resolve it |

## Coach, don't accuse

Allowed:

- "Could you send a screenshot that includes your username at the top? I can't see
  who posted it in this crop."
- "Could you capture the review date as well?"
- "Which gig is this review for?"
- When they send a link, or when the crop is short of more than one thing: *"Could
  you send a screenshot of the review with the product, your username, and the date
  all in the shot?"* One question, not three.

**A truncated product title is not worth a question** — if the visible part matches,
accept it. Asking them to re-crop a title their phone will truncate again ends where
it started.

**Never** ask a question that reveals a finding. "Is this your account?", "Is this
the right product?", or "Did you write this yourself?" each disclose the check that
failed. Those are **verdicts** — record them, hand off, reply neutrally.
