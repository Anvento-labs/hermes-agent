---
name: crwd-gig-discovery
description: "Help a CRWD member find and understand gigs — what's available now, gig details (payout, deadline, product links, store), applying, approval status, and where to go for a live gig (nearest Walmart/Target, address, hours). Use when a member asks what gigs are open, about a specific gig, how to apply, whether they're approved, or where the store is."
version: 2.0.0
metadata:
  hermes:
    tags: [crwd, gigs, campaigns, browse, apply, approval, payout, deadline, store, walmart, target, location, nearest, hours, stock]
    related_skills: [crwd-gig-execution, crwd-application-expert, crwd-reminders-followups, crwd-reference]
    requires_toolsets: [crwd, web]
    config:
      - key: crwd.app_base_url
        description: Base URL of the CRWD member web app, used to build gig deep links (/my-gigs/<gig_id>)
        default: "https://live-staging.joincrwd.com"
        prompt: "CRWD app base URL (e.g. https://app.joincrwd.com)"
---

# CRWD Gig Discovery

Find gigs and explain them against the member's **real** data — not in the abstract.

## When to Use

- "What gigs are available?" / "Any new gigs?"
- "Tell me about the [X] gig" — payout, deadline, store, what's involved
- "How do I apply?" / "Am I approved yet?"
- "What gigs do I have?"
- "What are my waitlisted gigs?" / "What gigs are pending approval?"
- "What gigs have I done before?" / "My gig history"
- "Where do I go for this gig?" / "Where's the nearest Walmart/Target?"
- "What are the store hours?" / "Are they open now?"

## Procedure

Clear asks already map via the steps below (available → `list_active_gigs`, in-progress →
`get_user_gigs` / `get_user_gig_status`, pending → `get_waitlisted_gigs`, etc.). **Only when**
the message could mean either **enrolled/applied** or **open/unenrolled** gigs, call
`clarify` first with choices like `["Ones I'm already in", "Open gigs I can join"]`, then
use the matching `crwd_db` action. Do not list both scopes in one turn. After a clear answer,
optionally offer one engaging follow-up (next step, or a reminder via `crwd-reminders-followups`).

1. **Available gigs to apply for:** `crwd_db` action `list_active_gigs` **with `user_id`**
   from the `[CRWD member]` context line. Returns open gigs sorted by soonest end date,
   excluding any gig the member already has a membership for (pending, approved, or active).
   Includes payout, dates, stores, and proof type. Results are paginated (default 5 per
   page) — the response includes `has_more`, `total`, and `next_offset`.
   When the member asks to see more ("show me more", "any others?"), call again with
   `offset = next_offset` from the previous response (same `user_id`). Only say "that's
   the full list" when `has_more` is false.
2. **A specific gig by name/text:** `get_gig_details` (fuzzy-matches, returns ranked
   candidates with an `_id`). **Confirm the right `_id`** before you quote details or use it
   elsewhere — if two candidates are close, ask which one they mean.
3. **Pending approval (not in progress yet):** `get_waitlisted_gigs` with `user_id` from the
   `[CRWD member]` context line. Returns gigs they applied for but are not yet accepted
   (`isAccepted: false` — Request Pending Approval). Use this for "pending approval" or
   "still waiting to be accepted" — not `get_user_gigs` or `list_active_gigs`.
4. **Their in-progress gigs:** `get_user_gigs`. The current member's CRWD `user_id` is
   provided to you in context (a `[CRWD member]` line) — pass it straight through as `user_id`.
   This shows gigs they're **accepted into** (`isAccepted: true`, Home → Active / IN PROGRESS),
   not pending-approval applications.
5. **Past participation / history:** `get_user_gig_history` with `user_id`. Returns prior
   membership rows (including completed, rejected, or deleted gigs). Use for "what gigs have
   I done before?" — not `get_user_gigs` (in-progress only) or `list_active_gigs`.
6. **Paste linked `name` / `gig_name` verbatim — the title IS the clickable link.**
   Every `crwd_db` action returns `name` / `gig_name` already as markdown
   `[Title](…/my-gigs/<_id>)`. Copy that field as-is into the reply. Do **not**
   write `Title — url` or append a bare URL after the name. Do not invent
   `/explore/` links. On "show more", call `list_active_gigs` with `next_offset`
   and use the fresh linked `name` — don't paraphrase titles from memory.
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
   - **Never assume the member's location.** If you don't already know their city/ZIP (from
     the conversation or profile), **ask first** — one short question:
     *"What city or ZIP are you in? I'll find the closest one."* Don't guess or pick a random
     store.
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
   (`crwd-gig-execution`).
10. Be precise on **payout, deadline, and estimated time** — quote the real numbers; never guess.
11. Offer a deadline reminder if the gig is time-sensitive (see `crwd-reminders-followups`).

For the deeper lifecycle detail, load
`skill_view("crwd-reference", "references/gig-lifecycle.md")`.

## Pitfalls

- Don't quote a gig's payout/deadline from memory — look it up.
- **Do not combine `list_active_gigs` and `get_user_gigs` when answering availability**
  questions — enrolled gigs belong on Home, not Explore. Use step 1 alone for "what's
  available?" and step 4 alone for "what active gigs do I have?"
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
- **Store locating:** never invent a store, address, or phone number; if a bare ZIP matches
  several stores, give the top match and note there are others. Hours online can be stale
  (say "confirm by phone" for "open now?"), and you can't see live inventory (say "call to
  confirm," never "it's in stock"). Keep store replies to name + address + phone + hours.
- **Gig links:** paste linked `name` / `gig_name` from `crwd_db` verbatim
  (`[Title](…/my-gigs/<id>)`). Never append a separate bare URL, never rebuild
  `/explore/` links, and never replace the markdown with only `name_plain`.

## Verification

- Details you gave (payout, deadline, store) came from `crwd_db`, not assumption.
- Product name + real buy link were included when the gig has a product — proactively, not
  only when asked.
- For a live gig, you gave the store info or asked for the member's location before searching,
  and the store reply had a real name, address, phone/store number, and hours.
- You did **not** offer to find a physical store for a non-`irl` (online) gig, regardless of
  its `stores[].store_name` or the member's in-store preference.
- Available-gig answers excluded gigs the member is already in (`user_id` on
  `list_active_gigs`).
- "Show me more" used `next_offset` from the prior page when more gigs existed.
- You confirmed the specific gig `_id` when there was any ambiguity.
- The member knows their current step in the flow and what to do next.
- Every gig named used the verbatim linked `name` / `gig_name` from `crwd_db`
  (clickable title), with no trailing bare URL.
