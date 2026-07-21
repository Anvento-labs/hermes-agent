# Proof & Receipt Validation (CRWD Coach)

This document describes the **proof validation workflow that already ships** in
this repo. Receipt validation is **not** a missing feature waiting to be
added — it is implemented as an agent skill plus deterministic tools.

**What exists today**

| Layer | What it does |
| ----- | ------------ |
| Skill `crwd-proof-validator` | Owns reading, classifying, judging, recording, and **replying** to proof submissions (receipts, order screenshots, review screenshots, UGC links) |
| Tools (`crwd_db`, image meta, vision, web) | Persist verdicts, enforce dedup / order-number shape, compute gig completion, extract EXIF, read images / links |
| Downstream | Chatwoot auto-labels (`proof-acceptance` / `proof-rejection` / `gig-complete`); `crwd-risk-analyser` scores fraud from stored records |

There is **no** separate Python “receipt OCR engine” that returns accept/reject
by itself. Content is read with `vision_analyze` (and EXIF helpers); the coach
skill applies the playbooks; tools enforce hard rules on storage and duplicates.

For Chatwoot label behavior after a proof turn, see
`conversation_classification.md` and `conversation_labels_stakeholders.md`.

---

## Table of contents

1. [Purpose and boundaries](#1-purpose-and-boundaries)
2. [Source of truth](#2-source-of-truth)
3. [End-to-end workflow](#3-end-to-end-workflow)
4. [Proof types](#4-proof-types)
5. [Statuses, reason codes, confidence](#5-statuses-reason-codes-confidence)
6. [Tools and what each enforces](#6-tools-and-what-each-enforces)
7. [Receipt checks (playbook summary)](#7-receipt-checks-playbook-summary)
8. [Duplicates and gig completion](#8-duplicates-and-gig-completion)
9. [Downstream: labels and risk](#9-downstream-labels-and-risk)
10. [Member-facing reply rules](#10-member-facing-reply-rules)
11. [How to test](#11-how-to-test)
12. [Related docs](#12-related-docs)

---

## 1. Purpose and boundaries

When a member submits evidence for a gig (photo, PDF, or link), the coach must:

1. **Read** the artifact (never accept unread evidence).
2. **Validate** it against that gig’s store `requirements` and date window.
3. **Record** exactly one verdict per artifact via `store_proof`.
4. **Reply** in one of three registers (accepted / ask for clarification / neutral “received”).
5. On non-accept, **hand off** internally (`crwd_handoff`) without telling the
   member *why*.

**This skill does not**

- Move payout or gig state in CRWD’s product DB (only writes to agent-owned
  `proof_submissions`).
- Write the member’s fraud **risk score** (`crwd-risk-analyser` owns that).
- Assign Chatwoot proof/gig labels (the end-of-turn auto-labeler does).

**Standing rules (skill)**

- Questions *before* a verdict are fine (raise confidence).
- After a verdict, **never** explain the rejection reason to the member.

---

## 2. Source of truth

| Piece | Path |
| ----- | ---- |
| Agent procedure + reason codes | `skills/crwd/crwd-proof-validator/SKILL.md` |
| Receipt playbook | `skills/crwd/crwd-proof-validator/references/validate-receipts.md` |
| Review screenshot playbook | `skills/crwd/crwd-proof-validator/references/validate-reviews.md` |
| UGC link playbook | `skills/crwd/crwd-proof-validator/references/validate-ugc-links.md` |
| Per-gig requirement flags (coaching) | `skills/crwd/crwd-reference/references/proof-requirements.md` |
| Mongo proof storage / dedup / completion | `tools/crwd_db_tool.py` |
| Camera / screenshot EXIF extract | `tools/crwd_image_meta_tools.py` |
| Risk scoring (downstream) | `tools/crwd_risk_score_tool.py`, `skills/crwd/crwd-risk-analyser/` |
| Auto labels from this-turn `store_proof` | `plugins/platforms/chatwoot/labels_auto.py` |
| Unit tests (DB proof actions) | `tests/tools/test_crwd_db_tool.py` |
| Unit tests (image meta) | `tests/tools/test_crwd_image_meta_tools.py` |
| Unit tests (proof labels) | `tests/plugins/test_chatwoot_labels_auto.py` |

**Toolset:** `crwd` (includes `crwd_db`, `crwd_verify_camera_receipt`,
`crwd_verify_screenshot`, `crwd_handoff`, `crwd_risk_score`). Vision lives in
toolset `vision` (`vision_analyze`). Web / browser optional for UGC.

**Config:** `CRWD_MONGO_URI` (required for proof DB), `CRWD_MONGO_DB`
(default `crwd_staging`). Chatwoot creds for handoff / labels / risk attribute.

---

## 3. End-to-end workflow

```text
Member sends receipt / screenshot / UGC link
        │
        ▼
crwd-proof-validator (skill)
  1. vision_analyze / read_file / web_extract  — actually read the proof
  2. Classify proof_type + extract identifier
  3. get_user_gig_status + get_gig_details(full) + get_user_products
  4. Load playbook (receipts / reviews / ugc)
  5. crwd_verify_camera_receipt or crwd_verify_screenshot (images)
  6. check_gig_proof_completion — what is still outstanding?
  7. check_duplicate_proof(proof_id, proof_type, user_id, crwd_id)
  8. Coach if unclear; else decide status + reason_code + reason
  9. store_proof exactly once
 10. Non-accept → crwd_handoff (internal note)
 11. Reply (accepted / clarify / neutral)
        │
        ▼
post_tool_call / post_llm_call hooks
  • record store_proof evidence
  • assign proof-acceptance | proof-rejection | gig-complete (this turn)
        │
        ▼
crwd-risk-analyser (later / same session when unscored proofs exist)
  • delta via crwd_risk_score
  • mark_proof_risk_scored
  • risk-* band label (preserved by auto-labeler)
```

**Hard rule encoded in `store_proof`:** an `accepted` proof must include
`source_url` (attachment read) or `proof_link` (link opened). A typed order
number with no evidence cannot be accepted and cannot complete a gig.

---

## 4. Proof types

Closed set in `crwd_db_tool._PROOF_TYPES`:

| `proof_type` | Typical identifier |
| ------------ | ------------------ |
| `receipt_target` | Target `REC#` |
| `receipt_amazon` | Amazon Order # (tool expects **17** digits after normalize) |
| `receipt_other` | Merchant order / transaction number |
| `order_screenshot` | Order confirmation `#` (often same digits as the receipt for that purchase) |
| `review_screenshot` | Built key: `{crwd_id}:{handle}:{review_date}` |
| `ugc_link` | Platform post id (normalized as `platform:post_id`) |

**Reviews are proved by screenshot, never by link.** A review URL is coached into
a screenshot; nothing is stored until the image arrives.

**Order screenshot vs receipt:** same purchase may share one order number.
They are different `proof_type`s so both can be recorded. That is **not** a
duplicate for the same member on the same gig.

---

## 5. Statuses, reason codes, confidence

### Statuses (`_PROOF_STATUSES`)

| Status | Meaning |
| ------ | ------- |
| `accepted` | Matches gig; credits toward completion |
| `rejected` | Failed a check (or duplicate conflict on accept path) |
| `needs_human` | Ambiguous / unreadable / invalid key shape — human should review |

Every store requires `reason_code` + human-readable `reason`, **including accepts**
(`clean_match` on a clean accept).

### Reason codes (`_PROOF_REASON_CODES`)

| Code | Typical use |
| ---- | ----------- |
| `clean_match` | Accepted — all checks passed |
| `duplicate_proof` | Purchase claimed by another member, or same member on another gig |
| `gig_not_active_for_user` | Not enrolled / not at proof stage |
| `wrong_proof_type` | Wrong artifact vs store `requirements` |
| `incomplete_submission` | Gig needs more artifacts (e.g. second receipt) |
| `date_outside_gig_window` | Outside `start_date` / `end_date` (`null` = unbounded) |
| `no_identifier` | No defensible unique id |
| `invalid_order_number` | Number fails merchant shape rules (tool refuses to key it) |
| `wrong_product` | Product not a match to approved catalog |
| `wrong_quantity` | Fewer units than required |
| `unreadable` | Cannot extract needed fields |
| `suspected_edited` | Forgery / doctoring signals |
| `link_unreachable` | UGC dead / private / removed |
| `link_not_owned` | Handle doesn’t match member |
| `content_mismatch` | e.g. two-purchase rule: same payment method twice |

### Confidence (not a risk score)

`low` | `medium` | `high` — stored on the record for audit / risk context.
`low` means **ask first**, not auto-fail. Risk points are owned separately.

---

## 6. Tools and what each enforces

### `crwd_db` proof actions

| Action | Role |
| ------ | ---- |
| `store_proof` | **Only insert** path for proof verdicts → `proof_submissions`. Sets `is_gig_completed` from DB progress (caller cannot invent it). Blocks accept on cross-member / cross-gig conflict. Idempotent re-send → `already_recorded`. |
| `check_duplicate_proof` | Advisory conflict check. Pass **`user_id` + `crwd_id`** or same-gig second artifacts look like duplicates. |
| `check_gig_proof_completion` | Compares store `requirements` vs accepted proofs → `satisfied` / `outstanding` / `complete`. |
| `get_user_proofs` | “What has this member submitted?” |
| `find_proof` | History for a normalized proof id (who else touched this purchase). |
| `mark_proof_risk_scored` | Sets `risk_scored: true` so risk never double-counts a row. |
| `get_user_gig_status` / `get_gig_details` / `get_user_products` | Enrollment, windows, requirement flags, product catalog (read-only on product collections). |

Write scope is intentional: only `proof_submissions` is written by this tool
(`store_proof` insert, `mark_proof_risk_scored` boolean). Other CRWD collections
stay read-only.

### Image metadata

| Tool | Use |
| ---- | --- |
| `crwd_verify_camera_receipt` | EXIF/container fields for a **camera** photo of a physical receipt |
| `crwd_verify_screenshot` | Fields for order / review / app screenshots |

Both return **values only** (Make, Model, DateTimeOriginal, `has_gps`,
`has_makernote`, …). They do **not** compute authenticity scores — the skill
judges. GPS / MakerNote bytes are never returned (booleans only).

### Content reading

| Tool | Use |
| ---- | --- |
| `vision_analyze` | OCR / field extraction from images |
| `read_file` | PDFs |
| `web_extract` / `browser_*` | Open UGC links |

---

## 7. Receipt checks (playbook summary)

Full detail: `references/validate-receipts.md`. Order of checks (stop at first
hard fail):

1. **Store match** — merchant vs gig `stores[].store_name` (trim / case-fold).
2. **Product match** — fuzzy token match; near-matches are matches → else
   `wrong_product`.
3. **Quantity** — sum matching line items; short qty → ask about stock-outs
   before recording `wrong_quantity`.
4. **Date window** — vs gig `start_date` / `end_date`.
5. **Arithmetic** — line items + tax vs total → strong `suspected_edited` signal.
6. **Receipt logic / spelling / metadata** — multiple corroborating signals
   before `suspected_edited`.
7. **Clean** → `clean_match` + `accepted`.

**Two-purchase gigs:** two receipts, **different** payment methods (compare
tender / last four). One receipt alone → `incomplete_submission`, then coach for
the second.

Extracted fields commonly passed in `proof_info` / `extracted`:
`merchant_name`, `purchase_date`, `order_number`, `total_amount`, `tax_amount`,
`place`, `line_items[]`, `payment_method`.

---

## 8. Duplicates and gig completion

### Duplicate rules

A **proof id names a purchase**, not a single upload:

| Situation | Result |
| --------- | ------ |
| Another member already accepted that purchase | Conflict → do not accept; `duplicate_proof` |
| Same member, **different** gig | Conflict → `duplicate_proof` |
| Same member, **same** gig, second artifact (order + receipt) | **Allowed** |
| Identical artifact sent twice | `already_recorded` (not a penalty) |
| Prior reject / pending | Not a credit → not treated as “already claimed” for coaching |

Enforcement lives in `_proof_conflict` / accept path of `store_proof`, not only
in the skill.

### Gig completion

`check_gig_proof_completion` / `_gig_proof_completion` treat completion as:
every **artifact-level** requirement flag has at least one accepted proof of an
allowed type. Field-level flags (`requires_order_id`, `requires_review_rating`,
…) are checked *inside* another artifact and do not gate completion alone.

When an accept leaves nothing outstanding, `store_proof` sets
`is_gig_completed: true` on **that** row only.

---

## 9. Downstream: labels and risk

### Chatwoot labels (automatic)

From this turn’s `store_proof` tool evidence:

| Condition | Label |
| --------- | ----- |
| All statuses `accepted` | `proof-acceptance` |
| Any non-accepted | `proof-rejection` |
| `is_gig_completed: true` | `gig-complete` (this turn only; **not** preserved) |

Do **not** manually assign these with `chatwoot_labels` on normal turns — the
auto hook replaces labels end-of-turn.

**Proof turns vs payment topics.** When `store_proof` ran this turn, the
auto-labeler suppresses ungrounded `payment-issue` / `app-help` (including
sticky inheritance of those topics). A bare receipt upload should show
`proof-acceptance` or `proof-rejection` (± `new-user`), not `payment-issue`,
unless the member also asked about pay in the same message.

**Verbal approve without `store_proof`.** Saying “receipt approved” in the
reply does **not** create `proof-acceptance`. No `store_proof` this turn →
neither proof label. Always persist the verdict via the tool.

### Risk (separate skill)

`crwd-proof-validator` must **not** call `crwd_risk_score`. After a row is
stored with `risk_scored: false`, `crwd-risk-analyser` prices `reason_code`s,
writes a delta to the contact’s `risk_score`, marks the proof scored, and sets
`risk-low` … `risk-critical`. The auto-labeler **preserves** `risk-*` across
topic replaces.

---

## 10. Member-facing reply rules

Exactly three registers:

1. **Accepted** — warm, plain confirmation.
2. **Clarification** — one coaching question (missing / unreadable only).
3. **Anything else** — neutral “we’ve got it / someone will follow up” —
   **no reason, no reason code**.

Plain text for the chat widget. Internal detail belongs in the handoff note
(and Mongo), never in the member reply.

---

## 11. How to test

### Automated

```bash
scripts/run_tests.sh tests/tools/test_crwd_db_tool.py -q
scripts/run_tests.sh tests/tools/test_crwd_image_meta_tools.py -q
scripts/run_tests.sh tests/plugins/test_chatwoot_labels_auto.py -q
```

DB tests cover `store_proof`, duplicate checks, completion, and
`mark_proof_risk_scored` without live Chatwoot. Label tests cover
`proof-acceptance` / `proof-rejection` / `gig-complete` from synthetic
`store_proof` evidence.

### Manual / staging (Chatwoot + Mongo)

1. Confirm `CRWD_MONGO_URI` and Chatwoot env are set for the profile.
2. As a member on an active gig, send a clear receipt photo.
3. Expect: vision + (optional) `crwd_verify_camera_receipt` → `store_proof` →
   member reply → conversation labels `proof-acceptance` and/or `gig-complete`.
4. Reject path: wrong product / out-of-window / duplicate → `store_proof`
   non-accepted → `proof-rejection` + handoff note; member gets neutral ack.
5. Re-send same artifact → `already_recorded`, no second penalty.
6. After a scored rejection reason (e.g. `duplicate_proof`), confirm risk skill
   eventually updates contact `risk_score` and a `risk-*` label.

You do **not** need a separate “receipt validation service” to exercise this —
the workflow above *is* the validation system.

---

## 12. Related docs

| Doc | Topic |
| --- | ----- |
| `conversation_classification.md` | How proof/gig/risk labels are applied in Chatwoot |
| `conversation_labels_stakeholders.md` | Stakeholder-facing label meanings |
| `skills/crwd/crwd-proof-validator/` | Authoritative agent procedure |
| `skills/crwd/crwd-risk-analyser/` | Fraud score after proofs are stored |
| `skills/crwd/crwd-gig-execution/` | Coaching *before* submission (what to send) |
| `skills/crwd/crwd-payment-status/` | After proof is approved / payout questions |
