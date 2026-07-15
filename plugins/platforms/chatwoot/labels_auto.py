"""Automatic Chatwoot conversation labeling on every turn.

Applies labels via ``post_llm_call`` so inbox tags appear even when the agent
never invokes ``chatwoot_labels``.

Signal priority:
1. Tool evidence this turn (``post_tool_call`` bag) — highest confidence
2. Scored keyword heuristics on the current member message
3. Optional auxiliary LLM when confidence is still low
4. Sticky previous topics when the turn is ambiguous (no Chatwoot notes)

``handoff-escalation`` is applied only when the agent calls ``crwd_handoff``.
Classification observability is process logs only — never Chatwoot private notes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from plugins.platforms.chatwoot.labels import PREDEFINED_LABEL_TITLES
from plugins.platforms.chatwoot.labels_tool import (
    _assign_labels,
    _create_labels_if_not_exists,
    _resolve_conversation,
    check_chatwoot_labels_requirements,
)

logger = logging.getLogger(__name__)

_AMBIGUOUS_MAX_LEN = 24
_ENROLLMENT_CACHE_TTL_S = 60.0

_handoff_this_turn: ContextVar[bool] = ContextVar("chatwoot_handoff_this_turn", default=False)
_contact_id_this_turn: ContextVar[str] = ContextVar("chatwoot_contact_id_this_turn", default="")
_tool_evidence_this_turn: ContextVar[Tuple[Dict[str, str], ...]] = ContextVar(
    "chatwoot_tool_evidence_this_turn", default=()
)

_last_labels_lock = threading.Lock()
# conversation key -> last applied topic labels (handoff excluded from sticky store)
_last_topic_labels: Dict[str, List[str]] = {}

_enrollment_cache_lock = threading.Lock()
# contact_id -> (monotonic_ts, payload) where payload is None=unknown or (enrolled, names)
_enrollment_cache: Dict[str, Tuple[float, Optional[Tuple[bool, Set[str]]]]] = {}

_CRWD_ANCHOR_RE = re.compile(
    r"\b(crwd|gig|gigs|payout|proof|campaign|dot)\b|"
    r"\bin the app\b|\bpayment\b",
    re.IGNORECASE,
)

_AMBIGUOUS_RE = re.compile(
    r"^(?:y(?:es|eah|ep)?|no|nope|ok(?:ay)?|sure|thanks|thank you|thx|"
    r"that one|this one|the first|the second|got it|k|嗯|好)[\s.!]*$",
    re.IGNORECASE,
)

# Bare greetings — no topic. Must not inherit labels from the coach welcome
# (which often says "get paid" / "gigs" and would false-fire payment/gig rules).
_GREETING_RE = re.compile(
    r"^(?:hi|hii+|hello|hey|heya|hiya|howdy|yo|sup|greetings|"
    r"good\s+(?:morning|afternoon|evening|day))"
    r"(?:\s+there)?[\s!.?]*$",
    re.IGNORECASE,
)

# Coach identity / capability — off-topic, never invent gig-discovery from bio.
_META_IDENTITY_RE = re.compile(
    r"^(?:who\s+are\s+(?:you|u)|who\s+(?:r|is)\s+(?:you|u|this)|"
    r"what\s+are\s+you|what(?:'s|\s+is)\s+your\s+name|"
    r"what\s+can\s+you\s+do|what\s+do\s+you\s+do|"
    r"are\s+you\s+(?:a\s+)?(?:bot|human|ai)|"
    r"tell\s+me\s+about\s+yourself)[\s!.?]*$",
    re.IGNORECASE,
)

# LLM may not invent these unless member text or tools support them.
_LLM_MUST_GROUND = frozenset({
    "gig-discovery",
    "payment-payout",
    "account-eligibility",
    "account-info",
    "scam",
})

# Scored topic rules (score is for confidence/logging; no numeric label cap).
# Opt-out / stop-contact is intentionally not a topic — falls through to
# off-topic / sticky / auxiliary LLM like other unmatched text.
_LABEL_RULES: Tuple[Tuple[str, float, Tuple[str, ...]], ...] = (
    (
        "payment-payout",
        1.0,
        (
            r"\bpaid\b",
            r"\bpayment\b",
            r"\bpayout\b",
            r"\bwhere(?:'s| is) my money\b",
            r"\bwhen will i (?:get|be) paid\b",
            r"\bpayment history\b",
            r"\bdot\b",
            r"\bchargeback\b",
            r"\brefund\b",
        ),
    ),
    (
        "account-eligibility",
        1.0,
        (
            r"\bnot eligible\b",
            r"\bineligible\b",
            r"\bcan'?t join\b",
            r"\bdon'?t qualify\b",
            r"\btoo young\b",
            r"\bwrong state\b",
            r"\bage requirement\b",
        ),
    ),
    (
        "account-info",
        1.0,
        (
            r"\bmy account\b",
            r"\bmembership\b",
            r"\baccount status\b",
            r"\bdeactivat",
            r"\bban(?:ned|s)?\b",
            r"\bsuspend",
        ),
    ),
    (
        "scam",
        1.0,
        (
            r"\bwire transfer\b",
            r"\bgift card\b",
            r"\bbitcoin\b",
            r"\bphishing\b",
            r"\bsuspicious\b",
            r"\bsend me your password\b",
        ),
    ),
    (
        "app-help",
        1.0,
        (
            r"\bwhere (?:is|do i find)\b",
            r"\bwhere (?:can|do|to|should) i (?:find|see|go|look|open)\b",
            r"\bwhich (?:tab|section)\b",
            r"\bin (?:which|what) (?:tab|section)\b",
            r"\bhow do i (?:find|open|get to)\b",
            r"\bhome tab\b",
            r"\bexplore tab\b",
            r"\bin the app\b",
            r"\bnavigate\b",
            r"\bwon'?t load\b",
            r"\bbroken\b",
            r"\bnot working\b",
            r"\bdoesn'?t work\b",
            r"\bcan'?t (?:open|load|click)\b",
            r"\berror\b",
            r"\bbug\b",
            r"\bcrash\b",
            r"\blogin\b",
            r"\blink won'?t\b",
            r"\bpage won'?t load\b",
            r"\berror code\b",
        ),
    ),
    (
        "gig-discovery",
        1.0,
        (
            r"\bfind (?:a )?gig",
            r"\bbrowse\b",
            r"\bavailable gig",
            r"\bnear me\b",
            r"\bnew gig",
            r"\bdiscover\b",
            r"\bwhat gig",
            r"\bany gig",
            r"\bwhat is crwd\b",
            r"\bhow does crwd work\b",
            r"\bhow do i apply\b",
        ),
    ),
    (
        "off-topic",
        0.9,
        (
            r"\btell me a joke\b",
            r"\brecipe\b",
            r"\bweather\b",
            r"\bhomework\b",
            r"\btrivia\b",
            r"\bwrite code\b",
        ),
    ),
)

_PROOF_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bproof\b",
        r"\breceipt\b",
        r"\bsubmit\b",
        r"\bsubmission\b",
        r"\bupload\b",
        r"\battachment\b",
        r"\bscreenshot\b",
        r"\bpaperclip\b",
        r"\bresubmit\b",
    )
)

_MID_GIG_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\brequirements?\b",
        r"\bdeadline\b",
        r"\bcomplete (?:the )?gig\b",
        r"\bhow do i do\b",
        r"\bgig steps?\b",
        r"\bnext steps?\b",
        r"\bstuck on\b",
        r"\bactive gig\b",
        r"\bmy gig\b",
        r"\bnext step for\b",
        r"\bdetails? about\b",
        r"\bgig details?\b",
        r"\btell me about (?:the )?\w+ gig\b",
        r"\bgive me details\b",
        r"\bhow (?:do|to) (?:i )?(?:complete|do)\b",
        r"\bamazon gig\b",
        r"\brejected\b",
    )
)

_COMPILED_RULES: Tuple[Tuple[str, float, Tuple[re.Pattern[str], ...]], ...] = tuple(
    (label, score, tuple(re.compile(p, re.IGNORECASE) for p in patterns))
    for label, score, patterns in _LABEL_RULES
)

# Direct crwd_db action → label (get_gig_details is conditional — see labels_from_tools).
_CRWD_DB_ACTION_LABELS: Dict[str, str] = {
    "list_active_gigs": "gig-discovery",
    "get_waitlisted_gigs": "mid-gig-support",
    "get_user_gigs": "mid-gig-support",
    "get_user_gig_status": "mid-gig-support",
    "get_user_gig_history": "mid-gig-support",
    "get_user_receipts": "proof-submission",
}

_ENROLLED_CRWD_ACTIONS = frozenset(
    {
        "get_user_gigs",
        "get_user_gig_status",
        "get_user_gig_history",
        "get_waitlisted_gigs",
    }
)

_DOT_ACTIONS = frozenset({"get_user_transfers", "get_transfer"})


@dataclass
class ClassificationResult:
    labels: List[str] = field(default_factory=list)
    confidence: str = "low"  # "high" | "low"
    reasons: List[str] = field(default_factory=list)
    source: str = "heuristic"  # tools|heuristic|llm|sticky|mixed
    tools: List[str] = field(default_factory=list)


def _is_chatwoot(platform: Any) -> bool:
    return str(platform or "").strip().lower() == "chatwoot"


def reset_handoff_flag() -> None:
    """Clear the per-turn handoff flag (call at turn start)."""
    _handoff_this_turn.set(False)


def reset_contact_id() -> None:
    """Clear the cached Chatwoot contact id for the current turn."""
    _contact_id_this_turn.set("")


def reset_tool_evidence() -> None:
    """Clear the per-turn tool evidence bag."""
    _tool_evidence_this_turn.set(())


def _contact_id_for_turn(kwargs: Dict[str, Any]) -> str:
    explicit = str(kwargs.get("sender_id") or "").strip()
    if explicit:
        return explicit
    try:
        return str(_contact_id_this_turn.get() or "").strip()
    except LookupError:
        return ""


def handoff_requested_this_turn() -> bool:
    """Return True when ``crwd_handoff`` was invoked in the current turn."""
    try:
        return bool(_handoff_this_turn.get())
    except LookupError:
        return False


def tool_evidence_this_turn() -> Tuple[Dict[str, str], ...]:
    try:
        return tuple(_tool_evidence_this_turn.get() or ())
    except LookupError:
        return ()


def record_tool_evidence_hook(**kwargs: Any) -> None:
    """``post_tool_call`` — record tool name/action; mark handoff for ``crwd_handoff``."""
    tool_name = str(kwargs.get("tool_name") or "").strip()
    if not tool_name:
        return
    if tool_name == "crwd_handoff":
        _handoff_this_turn.set(True)

    args = kwargs.get("args")
    action = ""
    if isinstance(args, dict):
        action = str(args.get("action") or "").strip()
    elif isinstance(args, str) and args.strip():
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                action = str(parsed.get("action") or "").strip()
        except (json.JSONDecodeError, TypeError):
            action = ""

    entry = {"tool": tool_name, "action": action}
    try:
        current = list(_tool_evidence_this_turn.get() or ())
    except LookupError:
        current = []
    current.append(entry)
    _tool_evidence_this_turn.set(tuple(current))


def handoff_tool_hook(**kwargs: Any) -> None:
    """Backward-compatible alias for ``record_tool_evidence_hook``."""
    record_tool_evidence_hook(**kwargs)


def labels_from_tools(
    evidence: Optional[Sequence[Dict[str, str]]] = None,
    *,
    contact_id: str = "",
) -> Tuple[List[str], List[str]]:
    """Map this-turn tool evidence to labels. Returns (labels, reasons)."""
    evidence = list(evidence if evidence is not None else tool_evidence_this_turn())
    labels: List[str] = []
    reasons: List[str] = []
    actions_seen = {
        str(e.get("action") or "").strip()
        for e in evidence
        if str(e.get("tool") or "").strip() == "crwd_db"
    }
    enrolled_hint = bool(actions_seen & _ENROLLED_CRWD_ACTIONS)

    for entry in evidence:
        tool = str(entry.get("tool") or "").strip()
        action = str(entry.get("action") or "").strip()
        if tool == "crwd_handoff":
            if "handoff-escalation" not in labels:
                labels.append("handoff-escalation")
                reasons.append("tool:crwd_handoff")
            continue
        if tool == "dot" and (not action or action in _DOT_ACTIONS):
            if "payment-payout" not in labels:
                labels.append("payment-payout")
                reasons.append(f"tool:dot:{action or 'any'}")
            continue
        if tool != "crwd_db" or not action:
            continue
        if action in _CRWD_DB_ACTION_LABELS:
            label = _CRWD_DB_ACTION_LABELS[action]
            if label not in labels:
                labels.append(label)
                reasons.append(f"tool:crwd_db:{action}")
            continue
        if action == "get_gig_details":
            if enrolled_hint:
                label = "mid-gig-support"
            else:
                membership = _member_enrollment(contact_id)
                if membership is None:
                    # Unknown enrollment — do not force a label from this alone.
                    reasons.append("tool:crwd_db:get_gig_details:skipped_unknown_enrollment")
                    continue
                label = "mid-gig-support" if membership[0] else "gig-discovery"
            if label not in labels:
                labels.append(label)
                reasons.append(f"tool:crwd_db:get_gig_details->{label}")

    return labels, reasons


def _is_greeting_message(user_message: str) -> bool:
    """True for bare hellos with no actionable member intent."""
    return bool(_GREETING_RE.match((user_message or "").strip()))


def _is_meta_identity_message(user_message: str) -> bool:
    """True for coach identity / capability questions (who are you, …)."""
    return bool(_META_IDENTITY_RE.match((user_message or "").strip()))


def _is_ambiguous_message(user_message: str) -> bool:
    text = (user_message or "").strip()
    if not text:
        return True
    if _is_greeting_message(text) or _is_meta_identity_message(text):
        return True
    if len(text) <= _AMBIGUOUS_MAX_LEN and _AMBIGUOUS_RE.match(text):
        return True
    if len(text) <= 8 and not _CRWD_ANCHOR_RE.search(text):
        return True
    return False


def _prior_user_content(
    conversation_history: Sequence[Any],
    current_user_message: str,
) -> str:
    """Return the previous distinct user message, if any."""
    current = (current_user_message or "").strip()
    if not conversation_history:
        return ""
    for msg in reversed(conversation_history):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if content.strip() == current:
            continue
        return content.strip()
    return ""


def _build_turn_context(
    user_message: str,
    conversation_history: Optional[Sequence[Any]] = None,
    assistant_response: str = "",
) -> Tuple[str, str]:
    """Return (primary_text_for_regex, llm_context_blob).

    Regex uses the **member** message only. For ambiguous follow-ups (yes/ok),
    also include the prior **user** turn so short replies keep topic context.

    Never include assistant prose in regex **or** LLM context — coach copy
    often mentions "get paid" / "gigs" and false-fires topic labels.
    """
    del assistant_response  # Never fed to regex or LLM (API compat only).
    primary = (user_message or "").strip()
    history = conversation_history or ()
    prior = _prior_user_content(history, primary)

    if _is_greeting_message(primary) or _is_meta_identity_message(primary):
        # Greetings / identity stand alone — do not pull prior user topic.
        regex_text = primary.lower()
    elif _is_ambiguous_message(primary) and prior:
        regex_text = f"{primary} {prior}".lower()
    else:
        regex_text = primary.lower()

    llm_bits = [f"Member: {primary or '(empty)'}"]
    if (
        prior
        and not _is_greeting_message(primary)
        and not _is_meta_identity_message(primary)
    ):
        llm_bits.append(f"Prior member: {prior}")
    return regex_text, "\n".join(llm_bits)


def _has_crwd_anchor(text: str) -> bool:
    return bool(_CRWD_ANCHOR_RE.search(text))


def _matches_any(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def _extract_gig_name(message: str) -> str:
    """Best-effort gig name from the member message."""
    text = (message or "").strip()
    for prefix in (
        "next steps for ",
        "status for ",
        "tell me about ",
        "details about ",
        "give me details about ",
        "how is ",
        "how's ",
    ):
        if text.lower().startswith(prefix):
            name = text[len(prefix):].strip(" ?.")
            if name.lower() not in {"you", "yourself", "u", "me", "my gig", "my gigs"}:
                return name
    quoted = re.search(r'"([^"]+)"', text)
    if quoted:
        return quoted.group(1).strip()
    about = re.search(
        r"\b(?:about|for|on) (?:the )?(.+?) gig\b",
        text,
        re.IGNORECASE,
    )
    if about:
        return about.group(1).strip()
    return ""


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _gig_name_in_enrolled(gig_name: str, enrolled_names: Set[str]) -> bool:
    if not gig_name:
        return True
    needle = _normalize_name(gig_name)
    if not needle:
        return True
    for name in enrolled_names:
        hay = _normalize_name(name)
        if not hay:
            continue
        if needle in hay or hay in needle:
            return True
    return False


def _member_enrollment(contact_id: str) -> Optional[Tuple[bool, Set[str]]]:
    """Return enrollment state: ``(enrolled, names)``, or ``None`` if unknown."""
    contact_id = str(contact_id or "").strip()
    if not contact_id:
        return None
    now = time.monotonic()
    with _enrollment_cache_lock:
        cached = _enrollment_cache.get(contact_id)
        if cached and (now - cached[0]) < _ENROLLMENT_CACHE_TTL_S:
            return cached[1]

    if not os.getenv("CRWD_MONGO_URI"):
        result: Optional[Tuple[bool, Set[str]]] = None
    else:
        try:
            from plugins.platforms.chatwoot.coach_context import resolve_member_crwd_id
            from tools.crwd_db_tool import build_user_gig_status

            user_id = resolve_member_crwd_id(contact_id)
            if not user_id:
                result = None
            else:
                payload = build_user_gig_status(user_id, limit=10)
                items = payload.get("items") or []
                names = {
                    str(row.get("gig_name")).strip()
                    for row in items
                    if row.get("gig_name")
                }
                result = (bool(items), names)
        except Exception as exc:
            logger.debug("[chatwoot-labels-auto] membership lookup failed: %s", exc)
            result = None

    with _enrollment_cache_lock:
        _enrollment_cache[contact_id] = (now, result)
    return result


def _member_has_active_gigs(contact_id: str) -> Tuple[bool, Set[str]]:
    """Backward-compat wrapper: unknown/unenrolled → ``(False, set())``."""
    membership = _member_enrollment(contact_id)
    if membership is None:
        return False, set()
    return membership


def _handoff_in_current_turn(
    conversation_history: Sequence[Any],
    user_message: str = "",
) -> bool:
    """Scan the current turn for a ``crwd_handoff`` tool call (fallback path)."""
    del user_message  # unused; kept for call-site compatibility
    if not conversation_history:
        return False

    last_user_idx: Optional[int] = None
    for idx in range(len(conversation_history) - 1, -1, -1):
        msg = conversation_history[idx]
        if isinstance(msg, dict) and msg.get("role") == "user":
            last_user_idx = idx
            break
    if last_user_idx is None:
        return False

    for msg in conversation_history[last_user_idx + 1:]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = fn.get("name") or tc.get("name") or ""
                if str(name).strip() == "crwd_handoff":
                    return True
        if role == "tool":
            name = str(msg.get("name") or msg.get("tool_name") or "").strip()
            if name == "crwd_handoff":
                return True
            content = msg.get("content")
            if isinstance(content, str) and '"_type": "crwd_handoff"' in content:
                return True
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and parsed.get("_type") == "crwd_handoff":
                        return True
                except (json.JSONDecodeError, TypeError):
                    pass
    return False


def _ensure_label(matched: List[str], label: str, *, prefer_front: bool = False) -> None:
    if label in matched:
        return
    if prefer_front:
        matched.insert(0, label)
    else:
        matched.append(label)


def _apply_proof_and_mid_gig_labels(
    text: str,
    user_message: str,
    contact_id: str,
    matched: List[str],
    reasons: List[str],
) -> Tuple[float, bool]:
    """Add proof/mid-gig/discovery from text.

    Returns ``(score, suppress_discovery_fallback)``. When membership is
    unknown and mid-gig language was present, suppress bare-gig/anchor
    discovery invention so we under-tag rather than mis-tag.
    """
    proof = _matches_any(text, _PROOF_PATTERNS)
    mid = _matches_any(text, _MID_GIG_PATTERNS)
    if not proof and not mid:
        return 0.0, False

    membership = _member_enrollment(contact_id) if (proof or mid) else None

    if proof:
        _ensure_label(matched, "proof-submission", prefer_front=True)
        reasons.append("heuristic:proof")
        if membership is not None and membership[0]:
            if "gig-discovery" in matched:
                matched.remove("gig-discovery")
            _ensure_label(matched, "mid-gig-support")
            reasons.append("heuristic:proof+enrolled")
        return 1.0, False

    if "mid-gig-support" in matched or "gig-discovery" in matched:
        return 0.8, False

    if not mid:
        return 0.0, False

    if membership is None:
        # Unknown membership — do not invent gig-discovery.
        reasons.append("heuristic:mid-gig:skipped_unknown_enrollment")
        return 0.0, True

    enrolled, gig_names = membership
    if not enrolled:
        matched.append("gig-discovery")
        reasons.append("heuristic:mid-gig:unenrolled->discovery")
        return 0.9, False

    gig_name = _extract_gig_name(user_message)
    if gig_name and not _gig_name_in_enrolled(gig_name, gig_names):
        matched.append("gig-discovery")
        reasons.append("heuristic:mid-gig:unmatched_name->discovery")
        return 0.9, False

    matched.append("mid-gig-support")
    reasons.append("heuristic:mid-gig:enrolled")
    return 1.0, False


def _finalize_labels(topics: Sequence[str], handoff: bool) -> List[str]:
    """Dedupe and keep every predefined label; always attach handoff when requested."""
    deduped: List[str] = []
    for label in topics:
        title = str(label).strip().lower()
        if title not in PREDEFINED_LABEL_TITLES or title in deduped:
            continue
        if title == "handoff-escalation":
            continue
        deduped.append(title)
    if handoff:
        deduped.append("handoff-escalation")
    return deduped


def _heuristic_classify(
    text: str,
    user_message: str,
    contact_id: str,
) -> Tuple[List[str], List[str], float, bool]:
    """Return (labels, reasons, best_score, used_fallback_only)."""
    matched: List[str] = []
    reasons: List[str] = []
    best = 0.0
    strong = False

    if not text.strip():
        return ["off-topic"], ["heuristic:empty->off-topic"], 0.2, True

    for label, score, patterns in _COMPILED_RULES:
        if any(p.search(text) for p in patterns):
            if label not in matched:
                matched.append(label)
                reasons.append(f"heuristic:{label}")
            best = max(best, score)
            if label != "off-topic":
                strong = True

    proof_score, suppress_discovery = _apply_proof_and_mid_gig_labels(
        text, user_message, contact_id, matched, reasons
    )
    if proof_score:
        best = max(best, proof_score)
        strong = True

    # Navigation/app-help already matched — do not invent gig-discovery from
    # the word "gig(s)" (e.g. "where can i find irl gigs?").
    allow_discovery_fallback = "app-help" not in matched and not suppress_discovery

    if not matched and allow_discovery_fallback and re.search(r"\bgig", text, re.IGNORECASE):
        matched.append("gig-discovery")
        reasons.append("heuristic:bare-gig->discovery")
        best = max(best, 0.4)

    fallback_only = False
    if not matched:
        fallback_only = True
        if suppress_discovery:
            # Defer to LLM/sticky rather than inventing discovery.
            reasons.append("heuristic:mid-gig:no_fallback")
            best = 0.2
        elif allow_discovery_fallback and _has_crwd_anchor(text):
            matched.append("gig-discovery")
            reasons.append("heuristic:anchor->discovery")
            best = 0.35
        else:
            matched.append("off-topic")
            reasons.append("heuristic:fallback->off-topic")
            best = 0.25

    return matched, reasons, best, fallback_only or (not strong and best < 0.6)


def _labels_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        platforms = (cfg.get("display") or {}).get("platforms") or {}
        chatwoot = platforms.get("chatwoot") or {}
        labels = chatwoot.get("labels")
        return dict(labels) if isinstance(labels, dict) else {}
    except Exception:
        return {}


def _llm_fallback_enabled() -> bool:
    cfg = _labels_config()
    if "llm_fallback" in cfg:
        return bool(cfg.get("llm_fallback"))
    return True


def _llm_label_grounded(
    label: str,
    member_text: str,
    tool_topic_labels: Sequence[str],
) -> bool:
    """False for topic labels invented without member/tool evidence."""
    if label not in _LLM_MUST_GROUND:
        return True
    if label in tool_topic_labels:
        return True
    text = (member_text or "").lower()
    if label == "gig-discovery":
        return bool(
            re.search(
                r"\b(gig|gigs|crwd|campaign|browse|near me|apply|available)\b",
                text,
                re.IGNORECASE,
            )
        )
    if label == "payment-payout":
        return bool(
            re.search(
                r"\b(paid|payment|payout|dot|money|refund|chargeback)\b",
                text,
                re.IGNORECASE,
            )
        )
    if label == "account-eligibility":
        return bool(
            re.search(
                r"\b(eligible|ineligible|qualify|can'?t join|too young|"
                r"wrong state|age requirement)\b",
                text,
                re.IGNORECASE,
            )
        )
    if label == "account-info":
        return bool(
            re.search(
                r"\b(my account|membership|account status|deactivat|"
                r"ban(?:ned|s)?|suspend)\b",
                text,
                re.IGNORECASE,
            )
        )
    if label == "scam":
        return bool(
            re.search(
                r"\b(phishing|wire transfer|gift card|bitcoin|suspicious|"
                r"password|scam|fraud)\b",
                text,
                re.IGNORECASE,
            )
        )
    return True


def classify_with_auxiliary(llm_context: str) -> Optional[List[str]]:
    """Low-confidence fallback via auxiliary LLM. Fail-open → ``None``.

    Uses plain JSON in message content (no tools / tool_choice) so a cheap
    non-tool-calling model can be configured via ``auxiliary.chatwoot_labels``.
    """
    titles = ", ".join(sorted(PREDEFINED_LABEL_TITLES - {"handoff-escalation"}))
    system = (
        "You classify a CRWD Coach Chatwoot conversation into inbox labels. "
        "Classify ONLY from the member's message (and prior member turn if given). "
        "Do not invent gig-discovery or payment-payout from coach self-description. "
        "Identity questions (who are you) → off-topic. "
        "account-eligibility = not eligible / can't join / wrong state / age. "
        "account-info = account status / membership / ban / suspension. "
        "scam = phishing / wire transfer / bitcoin / gift card / password asks. "
        "Do NOT use a topic label for opt-out or stop-contact alone "
        "(stop texting, unsubscribe, remove me) — use off-topic if nothing else fits. "
        f"Allowed labels (use only these): {titles}. "
        "Return JSON only: {\"labels\": [\"...\"]}. "
        "Choose every label that applies; use [] if unsure. "
        "Never invent labels. Never include handoff-escalation."
    )
    try:
        from agent.auxiliary_client import call_llm

        response = call_llm(
            task="chatwoot_labels",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": llm_context or "(empty)"},
            ],
            temperature=0.0,
            max_tokens=200,
            timeout=15,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return None
        # Strip optional markdown fences
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            raw = parsed.get("labels")
        elif isinstance(parsed, list):
            raw = parsed
        else:
            return None
        if not isinstance(raw, list):
            return None
        out: List[str] = []
        for item in raw:
            title = str(item).strip().lower()
            if title in PREDEFINED_LABEL_TITLES and title != "handoff-escalation" and title not in out:
                out.append(title)
        return out
    except Exception as exc:
        logger.debug("[chatwoot-labels-auto] auxiliary classify failed: %s", exc)
        return None


def _conversation_key(account_id: str, conversation_id: str) -> str:
    return f"{account_id}:{conversation_id}"


def _get_sticky_topics(account_id: str, conversation_id: str) -> List[str]:
    key = _conversation_key(account_id, conversation_id)
    with _last_labels_lock:
        return list(_last_topic_labels.get(key) or [])


def _store_sticky_topics(account_id: str, conversation_id: str, labels: Sequence[str]) -> None:
    topics = [l for l in labels if l != "handoff-escalation"]
    key = _conversation_key(account_id, conversation_id)
    with _last_labels_lock:
        _last_topic_labels[key] = list(topics)


def clear_sticky_labels_for_tests() -> None:
    """Test helper — wipe in-memory sticky + enrollment caches."""
    with _last_labels_lock:
        _last_topic_labels.clear()
    with _enrollment_cache_lock:
        _enrollment_cache.clear()


def classify_conversation(
    user_message: str = "",
    conversation_history: Optional[Sequence[Any]] = None,
    contact_id: str = "",
    handoff_requested: bool = False,
    assistant_response: str = "",
    tool_evidence: Optional[Sequence[Dict[str, str]]] = None,
    *,
    allow_llm: bool = True,
    sticky_topics: Optional[Sequence[str]] = None,
) -> ClassificationResult:
    """Full classification with tools → heuristics → optional LLM → sticky."""
    evidence = list(tool_evidence if tool_evidence is not None else tool_evidence_this_turn())
    tool_labels, tool_reasons = labels_from_tools(evidence, contact_id=contact_id)
    # Handoff from explicit flag or evidence
    handoff = bool(handoff_requested) or any(
        str(e.get("tool") or "").strip() == "crwd_handoff" for e in evidence
    )

    tool_topic_labels = [l for l in tool_labels if l != "handoff-escalation"]
    tool_keys = [
        f"{e.get('tool')}:{e.get('action') or '-'}"
        for e in evidence
    ]

    # Bare hi / identity with no tools → off-topic (high confidence). Skip LLM
    # so coach bio ("gigs", "get paid") cannot invent topic labels.
    if (
        (_is_greeting_message(user_message) or _is_meta_identity_message(user_message))
        and not tool_topic_labels
    ):
        reason = (
            "heuristic:meta->off-topic"
            if _is_meta_identity_message(user_message)
            else "heuristic:greeting->off-topic"
        )
        return ClassificationResult(
            labels=_finalize_labels(["off-topic"], handoff),
            confidence="high",
            reasons=[reason],
            source="heuristic",
            tools=tool_keys,
        )

    regex_text, llm_context = _build_turn_context(
        user_message, conversation_history, assistant_response
    )
    heur_labels, heur_reasons, heur_score, fallback_only = _heuristic_classify(
        regex_text, user_message, contact_id
    )

    high_from_tools = bool(tool_topic_labels) or any(
        str(e.get("tool") or "").strip() == "crwd_handoff" for e in evidence
    )
    high_from_heur = (not fallback_only) and heur_score >= 0.6 and not _is_ambiguous_message(
        user_message
    )

    reasons = list(tool_reasons) + list(heur_reasons)
    sources: List[str] = []
    labels: List[str] = []

    if tool_topic_labels:
        for lab in tool_topic_labels:
            if lab not in labels:
                labels.append(lab)
        sources.append("tools")

    # Merge complementary heuristic labels (uncapped)
    if high_from_heur or not tool_topic_labels:
        for lab in heur_labels:
            if lab not in labels:
                labels.append(lab)
        if heur_labels:
            sources.append("heuristic")

    confidence = "high" if (high_from_tools or high_from_heur) else "low"

    # Low confidence: sticky first (keeps prior topic), then LLM only if no sticky.
    if confidence == "low" and sticky_topics:
        sticky = [
            l
            for l in sticky_topics
            if l in PREDEFINED_LABEL_TITLES and l != "handoff-escalation"
        ]
        if sticky:
            labels = list(sticky)
            reasons.append("sticky:previous_topics")
            sources.append("sticky")

    if (
        confidence == "low"
        and "sticky" not in sources
        and allow_llm
        and _llm_fallback_enabled()
    ):
        llm_labels = classify_with_auxiliary(llm_context)
        if llm_labels:
            grounded = [
                lab
                for lab in llm_labels
                if _llm_label_grounded(lab, user_message, tool_topic_labels)
            ]
            dropped = [lab for lab in llm_labels if lab not in grounded]
            if grounded:
                labels = list(grounded)
                reasons.append("llm:auxiliary")
                if dropped:
                    reasons.append(f"llm:ungrounded_dropped:{','.join(dropped)}")
                sources.append("llm")
                confidence = "high"
            elif dropped:
                reasons.append(f"llm:ungrounded_dropped:{','.join(dropped)}")

    if not labels:
        labels = ["off-topic"]
        reasons.append("fallback:off-topic")
        if "heuristic" not in sources:
            sources.append("heuristic")

    final = _finalize_labels(labels, handoff)
    if handoff and "handoff-escalation" not in reasons:
        reasons.append("tool:crwd_handoff" if handoff_requested else "handoff")

    source = sources[0] if len(sources) == 1 else ("mixed" if sources else "heuristic")
    return ClassificationResult(
        labels=final,
        confidence=confidence,
        reasons=reasons,
        source=source,
        tools=tool_keys,
    )


def classify_conversation_labels(
    user_message: str = "",
    conversation_history: Optional[Sequence[Any]] = None,
    contact_id: str = "",
    handoff_requested: bool = False,
    assistant_response: str = "",
    tool_evidence: Optional[Sequence[Dict[str, str]]] = None,
) -> List[str]:
    """Return predefined label titles for the conversation (uncapped)."""
    result = classify_conversation(
        user_message=user_message,
        conversation_history=conversation_history,
        contact_id=contact_id,
        handoff_requested=handoff_requested,
        assistant_response=assistant_response,
        tool_evidence=tool_evidence,
        allow_llm=False,
        sticky_topics=None,
    )
    return list(result.labels)


def auto_label_conversation(
    user_message: str = "",
    conversation_history: Optional[Sequence[Any]] = None,
    contact_id: str = "",
    handoff_requested: bool = False,
    assistant_response: str = "",
    tool_evidence: Optional[Sequence[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Bootstrap labels and assign classified tags to the current conversation."""
    if not check_chatwoot_labels_requirements():
        return {"success": False, "skipped": True, "reason": "chatwoot not configured"}

    account_id, conversation_id = _resolve_conversation()
    if not account_id or not conversation_id:
        return {"success": False, "skipped": True, "reason": "no chatwoot conversation"}

    sticky = _get_sticky_topics(account_id, conversation_id)
    result = classify_conversation(
        user_message=user_message,
        conversation_history=conversation_history,
        contact_id=contact_id,
        handoff_requested=handoff_requested,
        assistant_response=assistant_response,
        tool_evidence=tool_evidence,
        allow_llm=True,
        sticky_topics=sticky,
    )
    labels = list(result.labels)

    # High-confidence replace is implied by using this turn's labels alone.
    # Low-confidence sticky already folded previous topics into ``labels``.
    bootstrap = _create_labels_if_not_exists(account_id)
    if not bootstrap.get("success") and not bootstrap.get("existing"):
        return {
            "success": False,
            "skipped": False,
            "labels": labels,
            "classified": labels,
            "error": bootstrap.get("error"),
            "confidence": result.confidence,
            "source": result.source,
            "reasons": result.reasons,
        }

    # Always replace with the final set so stale topics drop on high-conf switches.
    assign = _assign_labels(account_id, conversation_id, labels, replace=True)
    assign["classified"] = labels
    assign["skipped"] = False
    assign["confidence"] = result.confidence
    assign["source"] = result.source
    assign["reasons"] = result.reasons
    assign["tools"] = result.tools

    if assign.get("success"):
        _store_sticky_topics(account_id, conversation_id, labels)
        logger.info(
            "[chatwoot-labels-auto] applied %s to conversation %s:%s "
            "(confidence=%s source=%s tools=%s reasons=%s)",
            labels,
            account_id,
            conversation_id,
            result.confidence,
            result.source,
            result.tools,
            result.reasons,
        )
    else:
        logger.warning(
            "[chatwoot-labels-auto] assign failed for %s:%s — %s",
            account_id,
            conversation_id,
            assign.get("error"),
        )
    return assign


def labeling_reminder_hook(**kwargs: Any) -> Optional[Dict[str, str]]:
    """``pre_llm_call`` — reset turn state and remind about auto-labeling."""
    reset_handoff_flag()
    reset_contact_id()
    reset_tool_evidence()
    contact_id = str(kwargs.get("sender_id") or "").strip()
    if contact_id:
        _contact_id_this_turn.set(contact_id)
    if not _is_chatwoot(kwargs.get("platform")):
        return None
    if not check_chatwoot_labels_requirements():
        return None
    return {
        "context": (
            "[Chatwoot triage] Conversation labels are applied automatically after "
            "each turn from tool use and message intent. `handoff-escalation` is "
            "added only when you call `crwd_handoff`. You may override via "
            "`chatwoot_labels` `assign_labels`. Do not mention labels to the member."
        ),
    }


def auto_label_hook(**kwargs: Any) -> None:
    """``post_llm_call`` — classify and assign labels every Chatwoot turn."""
    if not _is_chatwoot(kwargs.get("platform")):
        return
    try:
        handoff = handoff_requested_this_turn() or _handoff_in_current_turn(
            kwargs.get("conversation_history") or (),
            str(kwargs.get("user_message") or ""),
        )
        auto_label_conversation(
            user_message=str(kwargs.get("user_message") or ""),
            conversation_history=kwargs.get("conversation_history"),
            contact_id=_contact_id_for_turn(kwargs),
            handoff_requested=handoff,
            assistant_response=str(kwargs.get("assistant_response") or ""),
            tool_evidence=tool_evidence_this_turn(),
        )
    except Exception as exc:
        logger.warning("[chatwoot-labels-auto] hook failed: %s", exc)
    finally:
        reset_handoff_flag()
        reset_contact_id()
        reset_tool_evidence()
