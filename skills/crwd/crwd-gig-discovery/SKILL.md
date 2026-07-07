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
        description: Base URL of the CRWD member web app, used to build gig deep links (/explore/<gig_id>)
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
6. **Turn the gig's name into a hyperlink, every time you name a gig.** Every `crwd_db`
   action above returns the gig's `_id` — build `<crwd.app_base_url>/explore/<_id>`
   (default base `https://live-staging.joincrwd.com`; use the configured
   `skills.config.crwd.app_base_url` value if one is injected into context) and make the
   **gig name itself** the markdown link: `[Summer Skincare Bundle](https://live-staging.joincrwd.com/explore/6a3411008972fa2d14ce8fe0)`.
   This is proactive — do it the first time a gig is named, don't wait to be asked, and never
   post the raw URL as a separate "here's the link" line. The **only** reason to skip linking
   is that this exact gig was already hyperlinked earlier in the same conversation — after
   that, mention it as plain text, don't re-render the same link every message. Any other
   gig (or the same gig in a new conversation) still gets linked. Full detail:
   `skill_view("crwd-reference", "references/gig-lifecycle.md")`.
7. **Include the product name + buy link by default.** `list_active_gigs` and
   `get_gig_details` already return each store's `products[]` with `name` and `product_url`.
   When you describe a gig, surface those links alongside payout/deadline/store — don't wait
   for the member to ask "where do I buy it?" Links are helpful; give the real `product_url`,
   don't just name the product. (Only skip them if the gig genuinely has no product, or the
   member explicitly says they just want the list.)
8. **For a live (in-store / `irl`) gig, help them get to the store.** The gig data names the
   retailer (`stores[].store_name`) and, for `irl` gigs, an `address`/`city`/`state`/
   `postal_code`. Surface that store info by default when you describe a live gig.
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
   **get approved** → perform → submit proof → get paid. If a `[CRWD gig context]`
   block is present, quote each gig's `next_step` instead of generic lifecycle
   advice. If they're waiting on approval, say that; if approved, point them at
   what to do next (`crwd-gig-execution`).
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
- Product links are in the gig data already — quote the real `product_url`; never paraphrase
  a link or omit it just because the member didn't explicitly ask for it.
- **Store locating:** never invent a store, address, or phone number; if a bare ZIP matches
  several stores, give the top match and note there are others. Hours online can be stale
  (say "confirm by phone" for "open now?"), and you can't see live inventory (say "call to
  confirm," never "it's in stock"). Keep store replies to name + address + phone + hours.
- **Gig links:** never post a bare/separate URL and never fabricate a link from memory — the
  gig name in prose IS the link, built only from a real `_id` `crwd_db` just returned. Don't
  withhold a link for any reason other than "already linked this exact gig earlier in this
  conversation" — when in doubt, link it.

## Verification

- Details you gave (payout, deadline, store) came from `crwd_db`, not assumption.
- Product name + real buy link were included when the gig has a product — proactively, not
  only when asked.
- For a live gig, you gave the store info or asked for the member's location before searching,
  and the store reply had a real name, address, phone/store number, and hours.
- Available-gig answers excluded gigs the member is already in (`user_id` on
  `list_active_gigs`).
- "Show me more" used `next_offset` from the prior page when more gigs existed.
- You confirmed the specific gig `_id` when there was any ambiguity.
- The member knows their current step in the flow and what to do next.
- Every gig name mentioned is a working markdown link to `<app_base_url>/explore/<_id>`,
  unless that exact gig was already linked earlier in this conversation.
