# Validate: receipts

Loaded from `crwd-proof-validator`. The driver owns the flow, the verdict, and the
reply — this file only tells you what to look for in a **receipt**.

Covers `receipt_target`, `receipt_amazon`, `receipt_other`, and `order_screenshot`.
`receipt_other` is not an edge case: real gigs run at Walmart, Whole Foods, Sprouts,
Raley's, Apple, and smaller independents. Treat them all with the same rigour.

**`order_screenshot` vs a receipt.** An online gig commonly asks for the order
confirmation screenshot; the gig may also want the receipt. These are two artifacts
of **one purchase** and carry the **same order number** — that is expected, not a
duplicate. Type them apart (`order_screenshot` vs `receipt_amazon`) so both can be
recorded. An order screenshot shows the order page (order number, items, total, order
date); it has no tender line and no store address, so don't fault it for lacking them.

## What this proof is

A receipt is evidence the member **bought the gig's product, at the gig's store,
inside the gig's date window**. Its identifier is the order/transaction number —
Target's `REC#`, Amazon's `Order #`, or whatever the merchant prints
(`Trans 004512`). Pass it to `crwd_db` raw; the tool normalizes it to digits.

No readable order number → `no_identifier` → `needs_human`. Never invent one.

## Extract

Read these with `vision_analyze` (or `read_file` for a PDF) and pass them as
`extracted` on `store_proof`:

| Field | Notes |
|---|---|
| `merchant_name` | As printed. `SPROUTS FARMERS MARKET`, `TARGET` |
| `purchase_date` | Normalize to `YYYY-MM-DD` |
| `order_number` | The identifier |
| `total_amount` | Number |
| `tax_amount` | Number |
| `place` | Store city/state as printed, e.g. `Dallas, TX 75231` |
| `line_items[]` | `{product_name, quantity, price, amount}` per line |
| `payment_method` | The tender line — `VISA ••1234`, `CASH`, `MASTERCARD`, `EBT`. Needed only on two-purchase gigs (below); read it when it's there |

If a required field is unreadable, that's a **coaching** moment (ask for a clearer
shot of that region), not a rejection.

## Checks, in order

Stop at the first that fires. Each maps to exactly one `reason_code`.

### 1. Store match

Compare `merchant_name` to the gig's `stores[].store_name`. **Trim and case-fold
both sides first** — the real data contains both `'Target'` and `'Target '` (with a
trailing space), and `Apple` alongside `Apple Store`.

A receipt from a store not on the gig is usually the member shopping at the wrong
place, not fraud. Prefer `needs_human` unless the gig clearly restricts the store,
in which case `wrong_proof_type`.

### 2. Product match → `wrong_product`

**Match fuzzily. This is the check most likely to hurt an honest member.**

Receipts abbreviate aggressively and drop vowels. All of these are the *same
product*:

```
SMOOTH LGND DEODORNT   =  Smooth Legend Deodorant
UNICORN FRT DEODORNT   =  Unicorn Fruit Deodorant
VANILLA BABY SHAVE     =  Vanilla Baby Shave Cream
```

The rule: match on **token prefixes**, and tolerate dropped vowels, truncation, and
OCR confusions (`0`/`O`, `1`/`I`/`l`, `5`/`S`, `8`/`B`, `rn`/`m`). **A near-match is
a match.** Only a clearly different product is `wrong_product`.

Campaign product names themselves contain typos — a live gig config has
`SMOOTH LGND DEOORNT` (missing a `D`) for a product that prints as
`SMOOTH LGND DEODORNT`. An exact-match rule rejects that valid receipt. Do not
reproduce that bug: when the receipt line and the required product differ by a
character or two, they match.

Genuinely unsure → `needs_human`. Never reject on a name you merely failed to parse.

### 3. Quantity → `wrong_quantity`

Fewer units than the gig requires. Sum `quantity` across matching line items — a
member may buy two units on two lines.

**Ask before you record this one.** Out-of-stock is the usual cause, and the
required quantity is **public on the gig page** — so asking "I only see one of the
three, were the others out of stock?" discloses nothing the member doesn't already
know. It is a fact, not a finding, which is why it's the rare exception to
"a verdict is never a question".

Put their answer in `reason` — it's what the human needs to approve the override.
Score stays 0; this is confusion, not fraud.

### 4. Date window → `date_outside_gig_window`

`purchase_date` against the gig's `start_date` / `end_date`. **A `null` bound is
unbounded, not a failure** — real gigs have `start_date: null`. If a date could be
read either US or international (`01/15/2020` vs `15/01/2020`), ask rather than
assume.

### 5. Arithmetic → `suspected_edited`

Sum the `line_items[].amount`, add `tax_amount`, compare to `total_amount`. A forger
edits a total and rarely re-does the maths, so this is the strongest single tell of
a doctored receipt.

Allow a small tolerance (a cent or two of rounding). **Legitimate causes of a
mismatch — do not fire on these:**

- Coupons, loyalty, and manager discounts applied below the subtotal
- Bottle/bag deposits and fees (real data has a `BAG PROMPT CHARGE` line at `$0.10`)
- Tax-exempt lines mixed with taxable ones
- Line items cropped out of frame — that's `unreadable`, ask for the full receipt

Only call `suspected_edited` when the gap is material and unexplained.

### 6. Receipt logic and spelling → `suspected_edited`

A real Target receipt does not misspell `TARGET`. Signals:

- Merchant name misspelled, or branding that doesn't match the chain's real receipt
- Tax rate implausible for the `place` state (0% or 30% on a US grocery receipt)
- `purchase_date` in the future, or before the product existed
- Fonts, weights, or character spacing that change **within a single line**
- The same line item repeated to pad a total
- A timestamp outside any plausible store hours
- Prices with impossible precision (`$15.9999`) or inconsistent currency formatting

**Any one of these alone is weak.** A phone camera at an angle produces odd kerning;
a small merchant may genuinely have a strange receipt. Require **several
corroborating signals** before `suspected_edited`; a single odd signal is
`needs_human`.

### 7. Clean → `clean_match`, accepted

Right store, product matched, quantity met, in window, maths checks out, nothing
suspicious. Record *what matched* in `reason`.

**On a two-purchase gig, one clean receipt is not a finished submission** — see below
before you accept.

## Two-purchase gigs (two different payment methods)

Some gigs require **two purchases paid with two different payment methods**. This is
a real, documented CRWD requirement — check the gig terms and the store's
`requirements` before deciding any receipt is the whole story.

On these gigs:

- **Each receipt is its own record.** Two purchases mean two order numbers, so they
  key separately and neither is a duplicate of the other.
- **The payment methods must actually differ.** Read the tender line on each. Two
  receipts both paid `VISA ••1234` do not satisfy a two-method requirement — that's
  `content_mismatch`, and it is the whole point of the rule.
- **Compare the card, not just the type.** Two different Visa cards are two methods;
  the same Visa twice is one. If the last four are unreadable on either receipt, you
  cannot judge it — coach for a clearer shot, or `needs_human`.
- **One receipt so far is `incomplete_submission`, not `clean_match`.** Record the
  first receipt on its own merits, then coach for the second. Never let a gig's proof
  look complete when half of it is missing.

The same "don't accept a partial as complete" logic applies wherever a gig needs more
than one artifact — a live gig wanting a receipt **and** a UGC link, or an online gig
wanting an order screenshot **and** a review screenshot.

Coaching the second receipt is allowed and expected — it asks for something *missing*,
not something *wrong*: *"Thanks — that's the first one. This gig needs a second
purchase on a different payment method, so send that receipt across when you have
it."* Note this states the **gig's requirement**, which the member already knows from
the gig page. It is not a rejection reason.

## Metadata signals

Call `crwd_verify_camera_receipt(image_url)` on a photographed receipt. It returns
**field values only and computes no score — you judge them**:

`format`, `width`, `height`, `Make`, `Model`, `Software`, `DateTimeOriginal`,
`Orientation`, `ISO`, `FocalLength`, `ExposureTime`, `FNumber`, `Flash`,
`SensingMethod`, `SubSecTimeOriginal`, `OffsetTimeOriginal`, `has_gps`,
`has_makernote`, `error`.

**Reading them:**

| Pattern | Means |
|---|---|
| `Make`/`Model` set, `DateTimeOriginal` present, `ISO`/`FocalLength`/`ExposureTime`/`FNumber` present, `has_makernote` true, JPEG/HEIC | Consistent with a real camera capture → supports `high` |
| All camera fields null, no `Software` | **Inconclusive.** Almost certainly a chat re-encode → `medium`, never a rejection |
| `Software` names an image editor | Worth a look — but see the caveat below |
| `error` set, everything null | **Extraction failed. This is no evidence at all** — not a metadata verdict. Judge on the pixels |

Three traps:

- **`ExposureTime` and `FNumber` come back as strings** like `"1/60"` and `"11/5"`,
  not floats. Don't compare them numerically without parsing.
- **Stripped EXIF is inconclusive, not fraud.** WhatsApp, Telegram, and iOS sharing
  strip metadata routinely. It lowers confidence; it never rejects on its own.
- **The editor list below is our heuristic, not a tool output.** The tool reports
  whatever string sits in `Software`; nothing in the codebase classifies it.

Editor/generator names worth noticing in `Software`: Photoshop, Lightroom, GIMP,
Canva, Snapseed, Pixlr, Midjourney, Stable Diffusion, DALL·E. Note that a benign
`Software` value is common — phones write their own OS build there, and lawful apps
re-encode. `Software: Photoshop` on a receipt is a real signal; `Software: iOS 17.2`
is not.

## AI-generated receipts

Signals, in combination:

- **No EXIF whatsoever, format PNG**, and `png_text` carrying generator markers
  (`parameters`, `prompt`, `Software: Stable Diffusion`, a C2PA key).
  **`png_text` is PNG-only** — it is `None` on every JPEG, and that absence is not
  evidence of anything.
- Invented or subtly wrong store branding
- Product lines that read as plausible English but aren't real SKUs
- Glyphs that aren't a receipt font — real receipts use thermal-printer monospace
- Totals that correspond to no combination of the lines
- Impossibly clean paper: no fold, no curl, no thermal fade, perfect lighting

A generated receipt usually fails the **arithmetic** check too — cross-check before
calling it. Several signals → `suspected_edited`, rejected. One signal →
`needs_human`.

## Confidence

| Band | When |
|---|---|
| `high` | Every field read cleanly, product and store matched, maths checks out, metadata consistent with a camera capture |
| `medium` | Fields read and checks pass, but a signal is inconclusive — EXIF stripped, `error` set, or a fuzzy product match you're confident but not certain about |
| `low` | Fields partly illegible, or signals conflict. **Coach first** — ask for a better photo. `needs_human` only if asking didn't resolve it |

## Coach, don't accuse

Allowed, because they seek what you couldn't read:

- "Could you send that again with the top of the receipt in frame? I can't quite
  make out the order number."
- "The photo's a little blurry near the total — mind sending a sharper one?"
- "Which gig is this receipt for?"

**Never** ask a question that reveals a finding. "Is this the right product?",
"Are you sure about this total?", and "Did you buy this during the campaign?" each
hand the member the check that failed. A wrong product, a bad date, a failed
arithmetic check, and a duplicate are **verdicts** — record them, hand off, and
reply with the driver's neutral acknowledgement.
