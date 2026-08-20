---
name: crwd-handoff
description: "Hand a CRWD conversation to a human — for unresolved frustration, repeated unresolved issues, genuine money disputes (payout sent-but-not-received, refunds, wrong amounts), bans, rejected submissions, or a CRWD question you still can't safely answer after using the matching skill. Answer routine payment-status/history with crwd-payment-status first, and app/profile how-tos (theme, dark mode, phone via OTP, DOB, nav) with crwd-application-expert first — don't hand those off."
version: 1.0.2
metadata:
  hermes:
    tags: [crwd, handoff, escalate, human, frustrated, angry, dispute, rejected, ticket]
    related_skills: [crwd-troubleshooting, crwd-gig-execution, crwd-reference]
    requires_toolsets: [crwd]
---

# CRWD Handoff

You are the **first** line, not the last line. Help with the matching `crwd-*` skill
before you loop in a human. A clean handoff still beats guessing — but "slightly
unsure" is not a handoff. Try to help first.

## When to Use

Hand off when a real trigger is present, not when a question is merely unfamiliar:

- **Frustration or anger** — they're upset *and* you can't resolve it this
  turn (or they already tried the standard fix). Don't argue, don't over-apologize in loops.
- **Repeated issue** — the same problem after you've already tried the standard fix (e.g.
  troubleshooting steps didn't resolve it).
- **Rejected submission** — always. Explaining the reason and coaching a resubmission is a
  human's job (`skill_view("crwd-reference", "references/proof-requirements.md")`).
- **Money / account** — **answer payment questions first, don't reflexively hand off.**
  "Did I get paid?", "when will I be paid?", "where's my money?", and "show my payment
  history" are  answerable with `crwd-payment-status` (the `dot` tool + `crwd_db`
  approval state). Use it. Only hand off for a **genuine dispute or account action** you
  can't resolve: Dot shows the payout **sent but the member never received it**, a refund/
  chargeback request, a wrong/missing amount you can't explain from the data, or account
  bans/suspensions and legal questions.
- **Out-of-scope but relevant** — a real CRWD question you still can't answer after
  looking it up with tools / the matching skill. Hand off rather than guess.

Do **not** hand off just because a question is slightly unfamiliar or you're merely
unsure — try to actually help first. Handoff is for stuck / unsafe / unresolved-upset,
not mildly unsure.

## Procedure

1. **Re-verify status, then notify the team.** If your `summary` will reference gig/member
   status — enrollment, acceptance, approval, stage, rejection, buy link, or proof outcome —
   call `get_user_gig_status` (or the specific tool for what you're citing) fresh **this
   turn** first; never base it on a claim made earlier in the conversation, memory, or
   inference. If the `crwd_handoff` tool is available, call it with a short `reason` and a
   one-line `summary` grounded in that fresh read — it posts an internal note so a human has
   context and opens the conversation so it gets assigned to an agent. If the tool isn't
   available in this session, skip straight to step 2 (the member still gets handed a
   conversation a human can pick up).
2. **Tell the member — warmly and confidently.** Say you're looping in a human, plainly:
   *"I'm going to loop in someone from the team who can dig into this — they'll follow up
   right here."*
3. **Stop after the handoff line.** Opening the conversation (`status: open`) hands the
   thread to a human — the bot will not answer further inbound messages while status is
   `open`.

Support is available **24/7**, so don't soften the handoff with "they might take a while"
caveats — a real person will pick it up. A hesitant handoff makes the member trust the
process *less*. Be confident.

## Pitfalls

- Don't go silent — always tell the member you're handing off; don't just stop replying with
  no message.
- Don't promise a specific human, time, or outcome — just that the team will follow up here.
- Don't try one more risky answer "to be helpful" once you've decided to hand off.
- **Don't carry an earlier, unverified status claim into the handoff summary.** If the
  conversation already asserted something about acceptance/approval/stage/buy-link, re-verify
  it with a fresh tool call before it goes in the note — a wrong claim made once shouldn't be
  written down twice.

## Verification

- You notified the team (via `crwd_handoff` when available) and sent the member a clear,
  warm handoff line.
