# Conversation classification (Chatwoot / CRWD)

Source of truth in this repo:

- Predefined labels: `plugins/platforms/chatwoot/labels.py`
- Auto-classification: `plugins/platforms/chatwoot/labels_auto.py`
- Agent guidance / taxonomy examples: `skills/crwd/chatwoot-conversation-labels/`
- Hook wiring: `plugins/platforms/chatwoot/adapter.py` (`pre_llm_call`, `post_tool_call`, `post_llm_call`)

Labels apply on **Chatwoot** turns only. The skill states other platforms no-op.

---

## 1. Conversation labels currently used

Titles are lowercase. There is **no per-turn numeric label cap** — every matching
predefined label may apply. Auto-assign uses `replace=True` (full set replaced
each high-confidence turn).

| Label | When it is applied |
|-------|--------------------|
| `handoff-escalation` | **Only** when the agent calls `crwd_handoff` in the current turn (`post_tool_call` flag and/or scan of tool calls after the latest user message). Not from member frustration/opt-out text alone. Often paired with topic label(s). |
| `proof-submission` | Member text matches proof/receipt/submit-style patterns (`proof`, `receipt`, `submit`, `submission`, `upload`, `attachment`, `screenshot`, etc.). Always qualifies; enrollment not required. If enrolled, also pairs with `mid-gig-support`. |
| `mid-gig-support` | Mid-gig help language while the member has active enrollments (Mongo lookup via contact id). Named gig in the message must match an enrolled gig; unmatched named gig → `gig-discovery`. Proof + enrolled also adds this label. |
| `gig-discovery` | Browse/find/apply/“what is CRWD” patterns; mid-gig language when **not** enrolled; named gig that does not match enrollment; fallback when text has a CRWD anchor (`crwd`, `gig`, `payout`, `proof`, etc.) but no other topic matched; bare `\bgig` when nothing else matched. |
| `payment-payout` | Payment/payout keyword patterns (`paid`, `payment`, `payout`, Dot, chargeback, refund, “when will I be paid”, etc.). |
| `account-eligibility` | Eligibility-only patterns (`not eligible`, `ineligible`, `can't join`, `don't qualify`, `too young`, `wrong state`, `age requirement`). |
| `account-info` | Account status / membership patterns (`my account`, `membership`, `account status`, ban/suspension, deactivation). |
| `scam` | Fraud/phishing signals (`phishing`, `wire transfer`, `bitcoin`, `gift card`, `suspicious`, `send me your password`). |
| `app-help` | App navigation / broken UI patterns (`home tab`, `explore tab`, `won't load`, `crash`, `login`, etc.). |
| `off-topic` | Explicit off-topic patterns (joke, recipe, weather, homework, trivia, “write code”); bare greetings / identity questions; or fallback when there is no CRWD anchor and no other match; also used when classification text is empty (unless handoff alone). |

**Opt-out / stop-contact** (`stop texting`, `unsubscribe`, `opt out`, `remove me`, etc.) is **not** a topic label. Those messages fall through to `off-topic` / sticky / auxiliary LLM like other unmatched text. Hand off still requires `crwd_handoff`.

Topic rules (except handoff, proof, mid-gig) are multi-match in `_LABEL_RULES`:
payment → account-eligibility → account-info → scam → app-help → gig-discovery →
off-topic. Proof/mid-gig are applied afterward via `_apply_proof_and_mid_gig_labels`.

Optional override: agent may call `chatwoot_labels` `assign_labels` (skill); auto-hook covers normal triage.

---

## 2. How messages are classified

**Mechanism:** signal priority on each Chatwoot turn via `post_llm_call` →
`auto_label_hook`:

1. **Observed agent tool calls** this turn (`post_tool_call` bag) — deterministic
   map (e.g. `list_active_gigs` → `gig-discovery`, `dot` → `payment-payout`,
   `crwd_handoff` → `handoff-escalation`).
2. **Keyword / regex heuristics** on member text.
3. **Sticky previous topics** when confidence is still low (ambiguous follow-ups).
4. **Optional auxiliary LLM** (`call_llm(task="chatwoot_labels")`) when still
   unresolved — returns plain JSON text only (**no** tools / tool_choice), gated
   by `display.platforms.chatwoot.labels.llm_fallback` and configurable via
   `auxiliary.chatwoot_labels` for a cheap non-tool-calling model.

### Is classification based only on the user’s latest message?

**Mostly yes for regex.** `_build_turn_context()` builds text from:

1. The current `user_message`, and  
2. At most **one** prior distinct `role == "user"` message — **only** when the
   current message is ambiguous (short “ok” / “yes” / etc.).

Greetings and identity questions stand alone (no prior-user inheritance).
Gig-name extraction for mid-gig vs discovery uses the **current** `user_message`
only.

### Is it based on the user message plus chatbot response?

**No for topic keywords.** Assistant reply text is never included in regex or
auxiliary LLM context (coach copy often mentions “get paid” / “gigs”).

**Partially for handoff:** `handoff-escalation` depends on agent action this turn —
`crwd_handoff` via `post_tool_call`, or scanning assistant/tool messages **after**
the last user message in history for a `crwd_handoff` tool call.

### Is conversation history used?

**Yes, limited:**

| Use | What from history |
|-----|-------------------|
| Topic text | Latest user message; at most one prior **user** turn when ambiguous |
| Handoff detection | Messages after the last user turn (assistant tool_calls / tool results) |
| Enrollment context | Not chat history — Mongo membership lookup by Chatwoot `contact_id` when proof/mid-gig patterns fire |

Assistant/content history is **not** used for topic keyword classification.

---

## 3. Suggested improvements (tied to current implementation)

1. **Tighten `gift card` as scam** — may false-fire on legitimate payout language; consider requiring scam co-signals.  
2. **Multilingual / fuzzy matching beyond English regexes** — paraphrases and other languages fall through to `off-topic` or vague `gig-discovery`.  
3. **Surface confidence / reason in optional Chatwoot note** — today observability is process logs only.  
4. **Persist sticky across process restart** — in-memory sticky is lost on gateway restart.

---

## Repository coverage note

Conversation **categorization labels** in this tree are the Chatwoot/CRWD set above. Other “classification” modules (`agent/error_classifier.py`, `agent/tool_result_classification.py`, `agent/curator.py` skill classification, CI `classify_changes.py`) are **not** conversation-inbox labels and are out of scope for this document.
