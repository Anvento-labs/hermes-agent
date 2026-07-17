# Validate: UGC content links

Loaded from `crwd-proof-validator`. The driver owns the flow, the verdict, and the
reply — this file only tells you what to look for in a **UGC post link**.

Proof type: `ugc_link`. TikTok, Instagram, YouTube.

> Low traffic today: only a handful of gig stores set `requires_ugc_post`, and no
> UGC post links have been submitted yet. This playbook is deliberately short.
> Deepen it when UGC gigs ramp up.

## What this proof is

A link to content the member posted publicly. Its identifier is the **post id**,
keyed as `platform:post_id` — `tiktok:7311123`, `instagram:C8xY_z1`,
`youtube:dQw4w9WgXcQ`. The normalizer extracts it from the URL, so tracking params,
`www.`, and a missing `@handle` segment don't matter.

**Short links carry no post id.** `tiktok.com/t/ZP8b9Kvxe/` is real in the data and
resolves to nothing keyable — the normalizer correctly returns empty for it. Open
the link and take the id from the **resolved** URL. Unresolvable → `no_identifier`
→ `needs_human`. Never guess.

Post ids are **case-sensitive** on Instagram and YouTube — pass the URL through
verbatim rather than lowercasing it yourself.

## Open it

`browser_navigate` if available, else `web_extract`. **Never approve a link you
could not open** — "trust me, it's there" is `needs_human`, not an accept.

## Extract

`platform`, `handle`, `posted_at`, plus a note of what the post shows.

## Checks, in order

### 1. Reachable and public → `link_unreachable`

Dead, deleted, or private. A login wall is `needs_human` **after one coaching ask** —
ask the member to confirm the post is public, then re-check once.

### 2. Ownership → `link_not_owned`

Posted by the member's own handle, not reshared from someone else. An obvious
repost of another creator's video is the real fraud case here. A handle that merely
doesn't look like the member's name is **weak** — `needs_human`, not a rejection.

### 3. Product shown → `wrong_product`

The gig's product is visible and identifiable in the content.

### 4. On-concept → `content_mismatch`

Only when the gig states a creative direction. Absent an explicit rule, don't
invent one — a member's natural post is the point of UGC.

### 5. Posted in window → `date_outside_gig_window`

Not an old post recycled for a new gig. A `null` gig bound is unbounded.

### 6. Clean → `clean_match`, accepted

## Confidence

| Band | When |
|---|---|
| `high` | Post opened, public, handle matches, product clearly shown, posted in window |
| `medium` | Opened and checks pass, but something is inconclusive — ambiguous handle, or the id came from a resolved short link |
| `low` | Couldn't open it, or the product isn't clearly visible. **Coach first**; `needs_human` if that fails |

## Coach, don't accuse

Allowed:

- "That link's asking me to log in — could you check the post is set to public?"
- "That short link isn't opening for me — could you send the full post link?"
- "Which gig is this post for?"

**Never** ask a question that reveals a finding. "Is this your account?" or "Did you
post this yourself?" disclose the check that failed. Those are **verdicts** —
record, hand off, reply neutrally.
