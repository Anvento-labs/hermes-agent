# AI Progress Update

**A first look at CRWD's in-house AI system — three core agents, each with its own set of skills, that handle real member support and get smarter with every conversation.**

## How it works

We built an **agentic system**: a small set of core agents, each backed by a
growing set of specialized skills, working together inside a single
conversation. When a member messages us, the system figures out what they
actually need and routes to the right agent — grounded in CRWD's real rules
and real data, not generic guesses. It's built to know its limits, too:
frustration, disputes, or anything sensitive gets handed straight to a
human.

Before writing any agent behavior, we analyzed **over 400,000 real member
messages** to find what members actually ask, where they get stuck, and
what a good response looks like. Every agent and skill below was curated
from that real usage, not designed in a vacuum.

## The agents

### The "Auditor" Agent (Receipt Verification)

Reviews every piece of proof a member submits for a completed gig — the
receipt, the screenshot, the content — and decides whether it's valid. Its
underlying model is trained on **millions of receipts**, so it recognizes
real receipt formats and purchase patterns rather than checking surface-level
details — the goal is a foolproof, human-like audit on every submission.

- **Receipt Review** — checks submitted proof of purchase against what the
  gig actually requires (right product, right store, readable, complete).
- **Duplicate Detection** — flags receipts that have already been submitted
  once, before, so the same purchase can't be claimed twice.
- **Fact-Checking Against the Gig** — pulls the real details of the specific
  gig/campaign a receipt was submitted for and verifies the receipt actually
  matches it (product, store, purchase window) instead of approving on
  appearance alone.
- **Proof Completeness Check** — catches missing pieces before they become a
  rejection (e.g. a required second receipt, a missing content link).
- **Submission Status Lookup** — tells a member whether their submission
  passed, failed, or is still pending review.

### The "Sentinel" Agent (Fraud Scorer)

Runs underneath the Auditor to protect payouts — scoring submissions for
legitimacy so invalid or fraudulent claims get caught before money moves.

- **Legitimacy Scoring** — is being built out to score each submission for
  trust/fraud signals, flagging anything suspicious for review.
- **Pattern Detection** — watches for repeat or coordinated abuse across
  submissions rather than judging each one in isolation.
- **Escalation on Risk** — anything flagged goes to a human for a final call
  rather than being auto-approved or auto-rejected.

### The "Concierge" (Chatbot)

The member-facing front line — the agent members actually talk to for
everything else, start to finish.

- **Gig Discovery** — finds gigs for a member, explains payout/deadline/
  requirements, and locates the exact nearby store (address, phone, hours)
  via live web search.
- **Gig Execution** — walks a member through completing a gig correctly —
  what to buy, what content to create, what proof to submit.
- **Payment Status** — explains and tracks a member's real, live payout
  status end-to-end.
- **App Navigation** — guides members around the app — finding gigs,
  tracking progress, getting to the right screen.
- **Troubleshooting** — resolves common technical issues with quick, proven
  fixes.
- **Reminders & Follow-ups** — proactively schedules nudges so members don't
  miss a deadline or drop off, and checks back in to help them find their
  next gig.
- **Conversation Labeling & Reporting** — automatically categorizes every
  conversation, powering internal reporting on what members need.
- **Escalation / Handoff** — recognizes frustration or anything sensitive
  and hands off to a human cleanly.
- **Live Web Search** — answers real-world questions it can't know on its
  own — store hours, phone numbers, product details — instead of guessing.

## It also remembers

The system carries a running understanding of each member across every
conversation, not just within one chat. It builds a picture over time: gig
history, past questions, what they get stuck on, how they like to be
helped. In practice that means no repeated questions, faster and sharper
answers, personalized coaching, and an experience that keeps improving the
more a member uses it — this isn't a stored log of messages, it's a model
of the person that keeps learning.

## Why this matters

- **Scales instantly** with no ramp-up time, and stays consistent every time.
- **Compounding** — every agent, skill, and member profile gets sharper with
  more real usage.
- **Retention & efficiency** — members feel understood, and support cost per
  member drops as re-explaining goes away.
- **A durable data asset** — the longer a member is active, the more valuable
  their relationship history becomes.

This is phase one. The architecture is built to keep adding skills to each
agent — and new agents entirely — as we learn more about our members.
