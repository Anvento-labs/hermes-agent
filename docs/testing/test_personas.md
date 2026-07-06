# CRWD Coach — Client Demo Personas

Purpose-built personas for demoing CRWD Coach to clients. Each one is a realistic
CRWD member (a real person trying to make real money on the platform) hitting a
situation the bot is actually built to handle. Conversations are written in CRWD
Coach's real voice — see `docs/plans/crwd-gig-coach/SOUL.md` — short texts, no
bullet dumps, no corporate filler, and grounded in the actual `crwd-*` skills in
`skills/crwd/`.

Each persona has an inline `[fires: skill/tool]` note after the coach's replies.
Those notes are annotations for whoever is running the demo — they are never
shown to the member.

Companion doc: `docs/testing/skill-coverage-matrix.md` maps every skill/tool back
to the persona(s) that exercise it, so a demo run can be checked off end to end.

---

## 1. Maria — The Curious Newcomer

**Who she is:** 34, downloaded the CRWD app after a friend posted a $40 Venmo
screenshot on Instagram. She's never done gig work like this before and isn't
sure if it's legit. She's cautious, a little skeptical, and wants the whole
picture before she commits any time.

**Situation:** First open of the app. She doesn't know what CRWD is, how gigs
work, or how/when she'd actually see money. She needs the concept sold to her in
plain language, then walked to her first gig.

**What she needs from the coach:** Clear identity ("who even is this"), an honest
explanation of the flow and payout timing before she invests effort, and help
finding something to apply for.

### Demo conversation

> **Maria:** hi who is this / what is this app
>
> **Coach:** I'm your CRWD Coach — CRWD pays everyday people to buy, try, and
> post about real products for brands. Ask me about a gig, a store, your
> payment, whatever you need.
> `[fires: SOUL.md identity framing]`
>
> **Maria:** ok but how do i actually make money from this. is it legit
>
> **Coach:** Totally — you browse gigs, apply, get approved, do the thing
> (buy/try/post), submit proof, then get paid through Dot, usually 1–2 business
> days after you're approved. You keep the product too, the payout's on top of
> that.
> `[fires: crwd-gig-discovery — lifecycle explanation, crwd-reference/payments-dot]`
>
> **Maria:** what's dot
>
> **Coach:** Dot's our payments partner — they're the ones who actually send
> your money once a gig's approved. You'll get a payout link by text or email,
> pick how you want paid (bank, PayPal, Venmo, stuff like that), and it lands
> on that rail. You can track any payout yourself at my.dots.dev too.
> `[fires: crwd-payment-status — proactive Dot flow explainer]`
>
> **Maria:** ok that helps. what can i actually do right now
>
> **Coach:** Let's get you your first one — one sec.
>
> **Coach:** Got a few open right now. There's a Tide Pods gig — $18, buy it at
> Walmart, post a quick video, due in 6 days. Want that one or should I show you
> what else is open?
> `[fires: crwd-gig-discovery — list_active_gigs(user_id), quoting real payout/deadline/store]`
>
> **Maria:** that one's fine, how do i apply
>
> **Coach:** Tap it in Explore, then hit Apply — you'll get a notification once
> you're approved, usually pretty quick. I'll let you know if you get stuck.
> `[fires: crwd-application-expert — Explore vs Home navigation]`

**What this demo proves:** The bot introduces itself and CRWD confidently (never
generic "AI assistant" filler), proactively demystifies payments *before* a
skeptical newcomer asks twice, and gets her to a concrete first action — not a
wall of text.

---

## 2. Jasmine — The First-Gig Doer

**Who she is:** 22, college student, approved for her very first gig last
night and is anxious about "doing it wrong" and losing the payout. She's
detail-oriented to a fault — she wants to know the *exact* requirement before
she spends a dollar.

**Situation:** Approved for a live (in-store) gig that requires two separate
purchases with two different payment methods — an easy thing to mess up if you
don't know it going in. She also doesn't know which Walmart to go to.

**What she needs from the coach:** The store location, the buy link, the exact
purchase requirement stated precisely, what content to film, and the exact
proof to submit.

### Demo conversation

> **Jasmine:** i got approved for the tide gig!! what do i do now
>
> **Coach:** Nice, congrats! It's a live gig — buy the product at Walmart, film
> a quick natural video with it, then submit your receipt + video. Want me to
> walk you through it step by step?
> `[fires: crwd-gig-execution — get_gig_details confirms gig type]`
>
> **Jasmine:** yes please. also where do i even buy it, is there a specific link
>
> **Coach:** Here's your product: Tide Pods Free & Gentle, 42ct —
> walmart.com/ip/tide-pods-free-gentle-42ct/123456. And this one needs **two
> separate purchases with two different payment methods** — like a debit card
> for one trip and cash or a different card for the other. Two receipts, not
> one.
> `[fires: crwd-gig-execution — get_user_products(user_id) for real buy link; precise requirement quoted]`
>
> **Jasmine:** oh good thing i asked lol. what walmart though, i don't wanna drive to the wrong one
>
> **Coach:** What city or ZIP are you in? I'll find the closest one.
> `[fires: crwd-gig-discovery — never assumes location]`
>
> **Jasmine:** 30310
>
> **Coach:** Closest one's Walmart Supercenter, 2050 Metropolitan Pkwy SW,
> Atlanta — (404) 752-8830, open 6am–11pm. Call ahead if you want to confirm
> stock, I can't see live inventory.
> `[fires: crwd-gig-discovery — web_search store lookup, name+address+phone+hours only]`
>
> **Jasmine:** ok bought both! what kind of video do i need
>
> **Coach:** Natural and un-scripted — just show the product clearly, like
> you're telling a friend about it. Not an ad-style thing. Then submit both
> receipts, the store, and a link to the video and you're done.
> `[fires: crwd-gig-execution — UGC/content requirement + exact proof format]`
>
> **Jasmine:** submitted! did it go through ok?
>
> **Coach:** Let me check — yep, both receipts came through and passed
> validation. You're all set, just waiting on the brand's final approval now.
> `[fires: crwd-gig-execution — get_user_receipts(user_id) proof validation state]`

**What this demo proves:** The coach never guesses a store, a requirement, or a
link — every concrete detail (address, phone, hours, buy link, purchase rule)
comes from a real lookup, and a nervous first-timer walks away confident instead
of confused.

---

## 3. Priya — The Anxious First-Time Earner

**Who she is:** 41, laid off recently, doing CRWD gigs to bridge the gap while
job-hunting. Money is tight and stressful for her right now, so a $22 payout
that doesn't land instantly feels like a red flag, not a normal wait.

**Situation:** She submitted proof on her first gig two days ago and hasn't
seen money hit her account. She's starting to think she got scammed.

**What she needs from the coach:** Calm, specific reassurance grounded in her
actual approval/payout state — not a vague "it's processing" — plus an
explanation of *why* timing varies so she stops worrying every time.

### Demo conversation

> **Priya:** did i get paid for the dawn dish soap gig. it's been 2 days and nothing
>
> **Coach:** Let me check.
>
> **Coach:** Your submission's approved, and Dot shows the payout as sent
> yesterday for $22. How are you set up to get paid — bank, PayPal, Venmo,
> something else? Timing depends a lot on that.
> `[fires: crwd-payment-status — get_user_gigs approval state + dot get_user_transfers, then asks payout method]`
>
> **Priya:** venmo i think? i never actually finished setting it up
>
> **Coach:** That's probably it — Dot sends a payout link by email or text
> first, you claim it and pick Venmo there before it can move. Check your email
>/texts for a link from Dot, claim it, and it should land fast once you do.
> `[fires: crwd-payment-status — Dot claim flow explainer, crwd-reference/payments-dot]`
>
> **Priya:** omg ok i think i see it now, i never opened that text i thought it was spam
>
> **Coach:** Yeah that one's real — go ahead and claim it. Want me to check
> back with you tomorrow to make sure it actually landed?
> `[fires: crwd-reminders-followups — proactive follow-up offer]`
>
> **Priya:** yes please that would help a lot
>
> **Coach:** Done — I'll check in with you tomorrow on the Venmo payout.
> `[fires: crwd-reminders-followups — cronjob scheduled, confirmed in one line]`

**What this demo proves:** The bot separates "approved" from "paid" instead of
giving a mushy non-answer, catches the *actual* root cause (unclaimed payout
link mistaken for spam) instead of repeating "1–2 business days," and closes
the loop with a real scheduled follow-up rather than just reassurance.

---

## 4. Destiny — The Power Earner

**Who she is:** 29, has been doing CRWD seriously for 8 months as a real income
stream — 25+ completed gigs. She thinks about this like a part-time job: she
wants numbers, efficiency, and the next best-paying thing to apply to, not
hand-holding.

**Situation:** Tax season is coming up and she wants to know what she's made
total. She also wants to keep her pipeline full — she's cleared out the easy
gigs she knew about and wants to see everything open right now.

**What she needs from the coach:** Accurate totals and history pulled from real
data (not an estimate), and an efficient way to page through every currently
open gig without re-seeing ones she's already in.

### Demo conversation

> **Destiny:** how much have i made on crwd total, need it for taxes
>
> **Coach:** Pulling your payout history now.
>
> **Coach:** Across your transfers on Dot, you're at $1,340 total this year —
> largest single payout was $85 for the Bissell gig in March. Want the
> individual amounts too?
> `[fires: crwd-payment-status — dot get_user_transfers(user_id), real totals not estimated]`
>
> **Destiny:** nah that's enough for now. what's open right now that i haven't already applied to
>
> **Coach:** Here's the first 5, sorted by soonest deadline: Olay Regenerist —
> $30, Amazon review, due in 4 days. Scrub Daddy — $15, Walmart, due in 9 days.
> Method All-Purpose — $20, Target, due in 5 days. La Croix 12-pack — $12,
> Amazon, due in 10 days. Persil ProClean — $25, Walmart, due in 3 days. Want
> more?
> `[fires: crwd-gig-discovery — list_active_gigs(user_id) excludes gigs she's already in]`
>
> **Destiny:** yeah keep going
>
> **Coach:** Next 5: Suave Professionals — $10, Amazon, due in 12 days.
> Tide PODS Ultra — $18, Walmart, due in 8 days. Dawn Powerwash — $16, Target,
> due in 6 days. Bounty Select-a-Size — $14, Amazon, due in 14 days. Swiffer
> WetJet — $22, Walmart, due in 7 days. That's everything open right now.
> `[fires: crwd-gig-discovery — pagination via next_offset, has_more=false stated only when true]`
>
> **Destiny:** perfect, applying to persil and the swiffer one, those pay best for the effort
>
> **Coach:** Smart picks — both solid payout-to-effort. Ping me if you need
> either store located or the buy links once you're approved.
> `[fires: crwd-gig-discovery + crwd-gig-execution hand-off point]`

**What this demo proves:** Real dollar totals from live payout data (critical
for a power user's trust), correct exclusion of gigs she's already enrolled in,
and honest pagination — the bot never claims "that's everything" until it
actually is.

---

## 5. Angela — The Busy Multi-Gig Juggler

**Who she is:** 37, full-time nurse, does CRWD gigs on the side between shifts.
She's disciplined about the work itself but her calendar is chaos — she's
lost payouts before to missed deadlines and is trying not to let that happen
again.

**Situation:** She's got three active gigs going with different deadlines and
knows she'll forget at least one if nobody reminds her.

**What she needs from the coach:** A real deadline pulled from her account (not
a guess), a reminder scheduled before the deadline hits, and a follow-up
check-in on something she said she'd finish tonight.

### Demo conversation

> **Angela:** i have like 3 gigs going and i know im gonna forget one, can u remind me before they're due
>
> **Coach:** Let me pull your actual deadlines first.
>
> **Coach:** You've got the CeraVe gig due in 2 days, the Method gig due in 5,
> and the Persil one due in 6. Want a reminder the day before each?
> `[fires: crwd-reminders-followups — get_user_gigs for real end_dates, not guessed]`
>
> **Angela:** yes exactly that
>
> **Coach:** Done — I'll ping you the day before each one hits.
> `[fires: crwd-reminders-followups — 3 cronjobs scheduled, confirmed in one line]`
>
> **Angela:** also i'm gonna submit the cerave proof tonight after my shift, can u check that it actually went through tmrw
>
> **Coach:** For sure — I'll follow up tomorrow to make sure your CeraVe
> submission went through.
> `[fires: crwd-reminders-followups — follow-up check-in, distinct from the deadline reminder]`

**What this demo proves:** Deadlines and reminders are grounded in the
member's real gig data, the bot proactively distinguishes "reminder before a
deadline" from "check-in on something left open" instead of treating them as
one generic nudge, and confirms each scheduled item in a single short line —
no over-promising, no nagging.

---

## 6. Courtney — The Frustrated, Stuck User

**Who she is:** 26, has done a handful of gigs successfully but is having a bad
day — technical friction plus a rejected submission puts her right at the edge
of giving up on the platform.

**Situation:** First, a gig link won't open. After that's resolved, she learns
her last submission was rejected and she doesn't understand why — and she's
getting heated.

**What she needs from the coach:** Fast, non-repetitive troubleshooting for the
technical issue, and — once real frustration and a rejection show up — a clean,
confident handoff to a human instead of the bot trying to talk her down itself.

### Demo conversation

> **Courtney:** the gig link isnt opening for me, ive tried like 3 times
>
> **Coach:** Annoying — try refreshing the page first?
> `[fires: crwd-troubleshooting — step 1, one fix at a time]`
>
> **Courtney:** still nothing
>
> **Coach:** Try an incognito/private window, or a different browser if that's
> easy — sometimes it's a cached login thing.
> `[fires: crwd-troubleshooting — step 2/3]`
>
> **Courtney:** ok that worked, i can see it now
>
> **Coach:** Good, glad that's sorted. Go ahead and finish it up — let me know
> if you need the deadline reminded.
> `[fires: crwd-troubleshooting — resolved, offers reminder tie-in]`
>
> **Courtney:** actually wait, i just saw my swiffer submission got REJECTED?? i did everything right this is so frustrating, i needed that money
>
> **Coach:** I hear you, that's a real gut-punch. I'm going to loop in someone
> from the team who can dig into exactly why and sort it out — they'll follow
> up right here.
> `[fires: crwd-handoff — rejection + frustration both trigger it; crwd_handoff tool notifies team, warm confident line, then stops]`
>
> **Courtney:** ok... thank you
>
> *(Coach does not continue troubleshooting or guessing at the rejection reason — the thread is now with a human.)*

**What this demo proves:** The bot tries real fixes one at a time instead of
immediately punting on a technical glitch, but the instant a rejection *and*
real frustration show up, it hands off cleanly, confidently, and without
guessing at a reason it isn't authorized to give — exactly the "fast line, not
last line" posture from the SOUL.md handoff policy.

---

## Using these in a client demo

Run personas roughly in the order above — they build from "what is this" to
"I'm about to churn" and naturally narrate the full lifecycle: onboarding →
first gig → first payment jitters → power usage → juggling deadlines →
something going wrong. Pause after each to point at the `[fires: ...]`
annotation and name the skill/tool that made the specific, non-generic answer
possible — that's the sellable difference versus a generic FAQ bot.
