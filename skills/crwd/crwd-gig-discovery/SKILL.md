---
name: crwd-gig-discovery
description: "Coach a CRWD member on assigned gigs (status, next step) and find open gigs to join — details, apply, approval, and store location for live gigs. Use when they ask what to do, help with gigs, what's available, what they have, about a specific gig, or where the store is."
version: 2.1.1
metadata:
  hermes:
    tags: [crwd, gigs, campaigns, coach, browse, apply, approval, payout, deadline, store, walmart, target, location, nearest, hours, stock]
    related_skills: [crwd-gig-execution, crwd-application-expert, crwd-reminders-followups, crwd-reference]
    requires_toolsets: [crwd, web]
    config:
      - key: crwd.app_base_url
        description: Base URL of the CRWD member web app, used to build gig deep links (/explore/<gig_id>)
        default: "https://live-staging.joincrwd.com"
        prompt: "CRWD app base URL (e.g. https://app.joincrwd.com)"
---

# CRWD Gig Discovery

Coach the member against their **real** data: help with **assigned / in-progress**
gigs (status + next step; hand off how-to to `crwd-gig-execution`), and **find open
gigs** they can join. Members often still want new gigs while an online or long gig
is in progress — support both.

## When to Use

- "What should I do?" / "Help with my gigs" / "Anything for me?"
- "What gigs are available?" / "Any new gigs?" / "Any new gigs while I wait?"
- "Tell me about the [X] gig" — payout, deadline, store, what's involved
- "How do I apply?" / "Am I approved yet?"
- "What gigs do I have?" / "What are my active gigs?"
- "What are my waitlisted gigs?" / "What gigs are pending approval?"
- "What gigs have I done before?" / "My gig history"
- "Where do I go for this gig?" / "Where's the nearest Walmart/Target?"
- "What are the store hours?" / "Are they open now?"

## Procedure

Route by intent — keep Home vs Explore **labeled**, not forever exclusive:

- **Clear assigned / my gigs** ("what am I in?", "my active gigs", "pending approval",
  "my history") → assigned tools only (steps 3–5). Do not mix open Explore gigs into
  a pure Home answer.
- **Clear open / available** ("what's available?", "new gigs", "explore") →
  `list_active_gigs` only (step 1). Never treat enrolled gigs as "available."
- **Coach / vague help** ("help with gigs", "what should I do?", "anything for me?")
  → **one turn, two labeled sections** (same turn is OK):
  1. **Your gigs** — `get_user_gig_status` with `user_id` (use `include_waitlisted=true`
     when pending apps may matter). For each gig, paste the clickable title and quote
     that gig's `next_step`. For buy / UGC / proof how-to, hand off to
     `crwd-gig-execution` — this skill stays light on execution.
  2. **Open gigs you can join** — `list_active_gigs` with `user_id`; show the page
     (payout, deadline, products). Offer "show more" via `next_offset` when
     `has_more` is true.
- **`clarify` only for a pure list ask** that is still ambiguous between Home and
  Explore (e.g. bare "what gigs?" with no coach framing), with choices like
  `["Ones I'm already in", "Open gigs I can join"]`. Do **not** clarify away a
  coach turn — use the two-section reply instead.

After a clear answer, optionally offer one engaging follow-up (next step, or a
reminder via `crwd-reminders-followups`).

1. **Available gigs to apply for:** `crwd_db` action `list_active_gigs` **with `user_id`**
   from the `[CRWD member]` context line. Returns open gigs sorted by soonest end date,
   excluding any gig the member already has a membership for (pending, approved, or active).
   Includes payout, dates, stores, and proof type. Results are paginated (default 5 per
   page) — the response includes `has_more`, `total`, and `next_offset`.
   When the member asks to see more ("show me more", "any others?"), call again with
   `offset = next_offset` from the previous response (same `user_id`). Only say "that's
   the full list" when `has_more` is false. Do **not** ask preference / interest
   questions before listing — just show open gigs.
2. **A specific gig by name/text:** `get_gig_details` (fuzzy-matches, returns ranked
   candidates with an `_id`). **Confirm the right `_id`** before you quote details or use it
   elsewhere — if two candidates are close, ask which one they mean.
3. **Pending approval (not in progress yet):** Call `get_waitlisted_gigs` **before you
   reply** with `user_id` from the `[CRWD member]` context line — do not reuse an earlier
   message's tool result in this chat. Returns gigs they applied for but are not yet
   accepted (`isAccepted: false` — Request Pending Approval). Use this for "pending
   approval" or "still waiting to be accepted" — not `get_user_gigs` or `list_active_gigs`.
4. **Their in-progress / "what gigs am I part of" asks:** Call `get_user_gigs` or
   `get_user_gig_status` **before you reply** before naming any enrolled gigs — never
   answer from an earlier message's tool output even if gig names are still in chat
   history. When the ask covers both accepted and pending membership ("part of", "am I
   in", "my gigs"), use `get_user_gig_status` with `include_waitlisted=true`, or call
   `get_user_gigs` plus `get_waitlisted_gigs`. Pass `user_id` from the `[CRWD member]`
   line. `get_user_gigs` alone shows gigs they're **accepted into** (`isAccepted: true`,
   Home → Active / IN PROGRESS), not pending-approval applications.
5. **Past participation / history:** `get_user_gig_history` with `user_id`. Returns prior
   membership rows (including completed, rejected, or deleted gigs). Use for "what gigs have
   I done before?" — not `get_user_gigs` (in-progress only) or `list_active_gigs`.
6. **Every gig name must be a clickable markdown title — fail-closed.**
   Applies to **every** gig you name in the reply — not only single-gig answers.
   - Prefer the tool's already-linked `name` / `gig_name` (must look like
     `[Title](…/explore/<_id>)`). Paste that field **verbatim** into the reply.
   - Nested payloads (`get_user_gigs` / `get_waitlisted_gigs`): use `gig.name` (linked),
     not a freehand title.
   - If `name` / `gig_name` is plain text but `gig_url` is present, render
     `[plain](gig_url)` yourself — never send a bare title.
   - Never use `name_plain` / `gig_name_plain` alone in the member-facing reply.
   - Never rewrite a linked title to `**Title**` / bold-only text, a plain name,
     `Title — url`, a bare URL after the name, a paraphrase from chat memory, or
     any path other than the tool's `/explore/<id>` link. On pagination
     ("show more"), use fresh linked `name` from the new `list_active_gigs` page.
   - If the payload has no `gig_url` and the name stays plain, `CRWD_APP_BASE_URL` /
     `crwd.app_base_url` is likely unset — say you can't deep-link rather than
     inventing a URL. Still never ship a clickable-looking fake link.
   Full detail: `skill_view("crwd-reference", "references/gig-lifecycle.md")`.
7. **Include every product name + buy link.** `list_active_gigs` / `get_gig_details`
   return `stores[].products[]` with `name` + `product_url`. `get_user_gig_status`
   returns `products[]` (full list) plus legacy `buy_link` (first only). Whenever a
   gig has a product, list **every** `products[]` entry as
   `[Product Name](product_url)` — one per line, clickable product name. Never
   claim there's only one link when `products[]` has more, and never substitute
   `gig_url`. Prefer `get_user_products` with `crwd_id` / `get_gig_details` when
   answering a specific gig. Keep gig-title markdown and product markdown on
   separate lines.
   - **If you mention a link, show it.** Never describe the flow with a dangling
     reference like *"order it with the gig's link"*, *"use the buy link"*, or
     *"order it through the link"* unless the real `product_url` is rendered in
     that same message. A member cannot click a link you only talked about. When
     you describe buying/ordering a product, fetch and paste the actual
     `[Product Name](product_url)` right there — don't defer it to a later turn.
8. **Only a live (`gig_type: "irl"`) gig has a physical store — check `gig_type` first.**
   Store-locating (nearest Walmart/Target, address, hours, "open now?") applies **only** when
   the gig's `gig_type` is `irl`. `irl` gigs carry a physical `location`
   (`address`/`city`/`state`/`postal_code`); online gigs do not.
   - **Online gigs have NO store to visit — never offer to find one.** A `stores[].store_name`
     value (`Amazon`, `Target`, `Walmart`, …) is just the retailer the product is bought
     *through*; on an online gig the member orders it online, so there is no location to drive
     to. Do **not** offer "find your nearest <store>" for any gig that isn't `gig_type: "irl"`,
     even when the member says they prefer in-store and even when `store_name` names a big-box
     chain. If a member prefers in-store, point them at gigs whose `gig_type` is actually
     `irl` — don't reframe an online gig as an in-store trip.
   For a live (`irl`) gig, help them get to the store. Surface the store info by default when
   you describe it.
   - **Member location is CRWD-DB only — never Honcho / memory / guesswork.** Prefer the
     `[CRWD member]` context line's profile location when present. Otherwise call
     `crwd_db` `get_user` with `identifier` = the authenticated `user_id` and use
     `city` / `state` / `country` / `postal_code` from that payload. Do **not** use a
     city or ZIP from Honcho, session memory, another member, or a prior chat —
     those are often wrong (e.g. a test persona's Sacramento leaked across users).
   - If `city` and `postal_code` are both empty in DB/context, **ask once** —
     *"What city or ZIP are you in? I'll find the closest one."* Never invent a city.
   - Once you have their location, find the specific store with `web_search` (and
     `web_extract` on the store page if needed), e.g. *"Walmart near 90210 hours phone
     number"*. Give them, tightly: **store name + full address**, **phone / store number**,
     and **hours** (and whether it's open now, if you can tell).
   - Point them at the **retailer the gig actually uses**, not just any big-box store.
   - Suggest they **call ahead to confirm stock** — you cannot see live inventory, so never
     claim something is in stock.
9. Explain the flow against their **actual** state, not generically: browse → apply →
   **get approved** → perform → submit proof → get paid. Call `get_user_gig_status` when
   you need each gig's `next_step` — quote that instead of generic lifecycle advice. If
   they're waiting on approval, say that; if approved, point them at what to do next
   (`crwd-gig-execution` for buy / content / proof how-to).
10. Be precise on **payout, deadline, and estimated time** — quote the real numbers; never guess.
11. Offer a deadline reminder if the gig is time-sensitive (see `crwd-reminders-followups`).

For the deeper lifecycle detail, load
`skill_view("crwd-reference", "references/gig-lifecycle.md")`.

## Pitfalls

- Don't quote a gig's payout/deadline from memory — look it up.
- **Membership lists go stale** when the member joins or leaves between messages. Never
  reuse a previous message's `get_user_gigs` / `get_user_gig_status` /
  `get_waitlisted_gigs` output to answer "what gigs am I in / part of / enrolled in?" —
  always fetch again before listing.
- **Do not mix enrolled into a pure availability answer** — use step 1 alone for
  "what's available?" and steps 3–4 alone for "what active / pending gigs do I have?"
  Coach / vague asks **may** use both in one turn as two **labeled** sections
  (Your gigs → Open gigs); never dump enrolled rows into an unlabeled Explore list.
- **Pending approval** → step 3 (`get_waitlisted_gigs`) only — `isAccepted: false`. Do not use
  `get_user_gigs` or `list_active_gigs` for those questions.
- Always pass `user_id` to `list_active_gigs` when the member asks about available or new
  gigs — without it you may show gigs they've already joined.
- **"Show me more" means paginate** — pass `offset = next_offset` from the last
  `list_active_gigs` result; don't re-run offset 0 and conclude there are no more.
- Only tell the member they've seen all available gigs when `has_more` is false.
- `get_gig_details` returns *candidates*; picking the wrong `_id` sends the member to the
  wrong gig. Confirm first.
- Approval is gated by CRWD/the brand — you can report the state, but don't promise approval.
- Product links: quote every `products[]` / `product_url` as `[Product Name](url)`.
  Never paraphrase, never reuse `gig_url`, and never stop at the first `buy_link`
  when more products exist.
- **Never mention "the link" without providing it.** Saying *"order it with the
  gig's link"* / *"use the buy link"* while showing no link is the #1 complaint —
  the member is left with nothing to click. Any turn that references ordering
  through a link must render the real `product_url` in that same message.
- **Online gigs have no physical store** — store-finding is `gig_type: "irl"` only. A
  `stores[].store_name` of `Amazon`/`Target`/`Walmart` on a non-`irl` gig is just where the
  product is bought online, not a place to visit. Never offer "find your nearest <store>" for
  an online gig, even if the member prefers in-store. Only `irl` gigs carry a `location`.
- **Store locating:** never invent a store, address, or phone number; never invent the
  member's city/ZIP either — use `get_user` / the `[CRWD member]` profile location, not
  Honcho. If a bare ZIP matches several stores, give the top match and note there are
  others. Hours online can be stale (say "confirm by phone" for "open now?"), and you
  can't see live inventory (say "call to confirm," never "it's in stock"). Keep store
  replies to name + address + phone + hours.
- **Gig name href is mandatory.** Every gig named in the reply must be a clickable
  `[Title](…/explore/<id>)`. Prefer verbatim linked `name` / `gig_name` / `gig.name`;
  if plain + `gig_url`, build `[plain](gig_url)`. Never use `name_plain` alone, never
  `**Title**` / bold-only, never paraphrase from memory, never append a separate bare
  URL. If `gig_url` is missing and the name is plain,
  do not invent a link — `CRWD_APP_BASE_URL` / `crwd.app_base_url` may be unset.
- **No tables for gig catalogs.** Never format open or assigned gigs as a markdown
  table or Gig/Payout column grid.

## Verification

- Details you gave (payout, deadline, store) came from `crwd_db`, not assumption.
- Product name + real buy link were included when the gig has a product — proactively, not
  only when asked.
- For a live gig, store search used the member's CRWD profile city/ZIP (context or
  `get_user`), or you asked when those fields were empty — never a memory/Honcho city —
  and the store reply had a real name, address, phone/store number, and hours.
- You did **not** offer to find a physical store for a non-`irl` (online) gig, regardless of
  its `stores[].store_name` or the member's in-store preference.
- Available-gig answers excluded gigs the member is already in (`user_id` on
  `list_active_gigs`).
- Coach / vague asks used two labeled sections when both scopes were useful; pure
  availability answers did not mix in enrolled gigs.
- "Show me more" used `next_offset` from the prior page when more gigs existed.
- You confirmed the specific gig `_id` when there was any ambiguity.
- The member knows their current step in the flow and what to do next (quoted
  `next_step` where relevant; how-to pointed at `crwd-gig-execution`).
- **Every gig named in the reply has a clickable markdown title.** If any gig would
  have been plain text or `**bold-only**`, you fixed it (verbatim linked field or
  `[plain](gig_url)`) before sending — no trailing bare URL, no `name_plain`-only
  titles, no `**Title**` substitutes.
