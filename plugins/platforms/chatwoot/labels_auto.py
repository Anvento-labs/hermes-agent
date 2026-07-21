"""Automatic Chatwoot conversation labeling on every turn.

Applies labels via ``post_llm_call`` so inbox tags appear even when the agent
never invokes ``chatwoot_labels``.

Two-stage classification (accuracy-first):
1. **Dialogue act** — auxiliary LLM (JSON text, no tool-calling API) maps
   member intent to a closed act set. Pattern heuristics run only as fallback
   when the LLM is disabled or fails.
2. **Label map** — deterministic act → Chatwoot label titles (+ enrollment).

Sticky inheritance covers short ambiguous replies and pronoun/contextual
follow-ups when a prior topic exists. Member message defines the topic; coach
tool calls are **soft evidence** in the LLM feature bundle — only
``crwd_handoff`` is an exclusive hard tool label. Soft ``gig_hint`` values may
ground LLM gig acts when the member text fuzzy-matches the looked-up title.

``handoff-escalation`` is applied only when the agent calls ``crwd_handoff``.
``proof-rejection`` / ``proof-acceptance`` / ``gig-complete`` come from
``store_proof`` this turn (``gig-complete`` when ``is_gig_completed`` is true).
On a proof turn, intent topics ``payment-issue`` / ``app-help`` are suppressed
unless the member text independently grounds them (a receipt upload is not a
payout question). ``new-user`` is data-first (member has not completed a gig yet).
Classification observability is process logs only — never Chatwoot private notes.

**Preserved labels.** This module emits topic / turn labels and assigns with
``replace=True`` every turn — so state owned elsewhere is wiped unless carried
over. ``_preserved_labels`` re-reads Chatwoot and re-attaches:

* ``handoff-escalation`` — only while conversation status is ``open`` (human
  owns the thread). Cleared when status is no longer ``open`` (typically
  ``pending`` after handback to the bot), unless ``crwd_handoff`` ran this turn.
* ``risk-*`` — fraud band from ``crwd-risk-analyser``.

``gig-complete`` is **not** preserved — it is turn-scoped like proof verdicts.
Sticky-topic memory is separate: it caches this classifier's own
``payment-issue`` / ``app-help`` output in-process and is lost on restart.
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

from plugins.platforms.chatwoot.labels import APPLIED_LABEL_TITLES
from plugins.platforms.chatwoot.labels_tool import (
    _assign_labels,
    _create_labels_if_not_exists,
    _resolve_conversation,
    check_chatwoot_labels_requirements,
)

logger = logging.getLogger(__name__)

_AMBIGUOUS_MAX_LEN = 24
_CONTEXTUAL_FOLLOWUP_MAX_LEN = 120
_ENROLLMENT_CACHE_TTL_S = 60.0
_LLM_MEMBER_TURNS = 5
_LLM_ASSISTANT_TURNS = 2
_LLM_ASSISTANT_TRUNCATE = 200

# Sticky gig continuity — grounding may reuse these when member text is deictic.
# (Unapplied gig topic titles are retained only for sticky/act continuity.)
_GIG_TOPIC_LABELS = frozenset({
    "gig-discovery",
    "mid-gig-support",
    "proof-submission",
})
_GIG_TOPIC_ACTS = frozenset({
    "browse_open_gigs",
    "enrolled_gig_help",
    "proof",
})

# Applied intent topics that may participate in sticky inheritance.
_STICKY_TOPIC_LABELS = frozenset({"payment-issue", "app-help"})

# Turn / state labels excluded from sticky topic memory.
_NON_STICKY_LABELS = frozenset({
    "handoff-escalation",
    "gig-complete",
    "new-user",
    "proof-rejection",
    "proof-acceptance",
})

# Closed dialogue-act set (Stage 1 LLM output).
DIALOGUE_ACTS = frozenset({
    "account_status",
    "eligibility",
    "payout",
    "proof",
    "enrolled_gig_help",
    "browse_open_gigs",
    "general_inquiry",
    "app_nav",
    "scam",
    "chitchat",
    "ambiguous_followup",
    "escalate",
})

_LABEL_TO_ACT: Dict[str, str] = {
    "account-info": "account_status",
    "account-eligibility": "eligibility",
    "payment-issue": "payout",
    "payment-payout": "payout",  # legacy unapplied title → same act
    "proof-submission": "proof",
    "mid-gig-support": "enrolled_gig_help",
    "gig-discovery": "browse_open_gigs",
    "general-inquiry": "general_inquiry",
    "app-help": "app_nav",
    "scam": "scam",
    "off-topic": "chitchat",
}

# Profile/self asks — must not fire mid-gig ``details about`` heuristics.
_PROFILE_SELF_RE = re.compile(
    r"\b(?:about|details?\s+about)\s+(?:me|myself|my(?:self)?)\b|"
    r"\b(?:my\s+(?:info|profile|account\s+details?|status))\b|"
    r"\bgive\s+(?:me\s+)?details\s+about\s+me\b|"
    r"\btell\s+me\s+about\s+me\b|"
    r"\b(?:what(?:'s|\s+is)\s+my\s+name|tell\s+me\s+my\s+name|who\s+am\s+i)\b",
    re.IGNORECASE,
)

_handoff_this_turn: ContextVar[bool] = ContextVar("chatwoot_handoff_this_turn", default=False)
_contact_id_this_turn: ContextVar[str] = ContextVar("chatwoot_contact_id_this_turn", default="")
_tool_evidence_this_turn: ContextVar[Tuple[Dict[str, str], ...]] = ContextVar(
    "chatwoot_tool_evidence_this_turn", default=()
)

_last_labels_lock = threading.Lock()
# conversation key -> last applied topic labels (handoff excluded from sticky store)
_last_topic_labels: Dict[str, List[str]] = {}
# conversation key -> last dialogue acts (parallel to sticky labels)
_last_topic_acts: Dict[str, List[str]] = {}

# Always-preserved exact titles (none today — handoff is status-gated below).
_PRESERVED_LABELS: frozenset[str] = frozenset()
# Matched by prefix: the risk band is one of risk-low/medium/high/critical.
_PRESERVED_PREFIXES = ("risk-",)
# Chatwoot status while a human owns the thread after crwd_handoff.
_HANDOFF_ACTIVE_STATUS = "open"
# Turn-scoped hard labels appended in _finalize_labels (not sticky topics).
_TURN_HARD_LABELS = frozenset({
    "proof-rejection",
    "proof-acceptance",
    "gig-complete",
})

_enrollment_cache_lock = threading.Lock()
# contact_id -> (monotonic_ts, payload) where payload is None=unknown or (enrolled, names)
_enrollment_cache: Dict[str, Tuple[float, Optional[Tuple[bool, Set[str]]]]] = {}
# contact_id -> (monotonic_ts, completed) where completed is None=unknown or bool
_completed_gig_cache: Dict[str, Tuple[float, Optional[bool]]] = {}

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

# Pronoun / deixis follow-ups that refer to the prior turn's topic.
_CONTEXTUAL_DEIXIS_RE = re.compile(
    r"\b(?:it|this|that|them|those)\b|"
    r"\bfor it\b|\babout it\b|\babout that\b|\babout this\b|"
    r"\bfor that\b|\bfor this\b",
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
    "payment-issue",
    "app-help",
})

# Scored topic rules for *applied* intent labels only.
# Unapplied titles are never emitted from heuristics.
_LABEL_RULES: Tuple[Tuple[str, float, Tuple[str, ...]], ...] = (
    (
        "payment-issue",
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
        r"\bdetails? about (?:the )?\w+ gig\b",
        r"\bgig details?\b",
        r"\btell me about (?:the )?\w+ gig\b",
        r"\bgive me details about (?:the )?\w+ gig\b",
        r"\bhow (?:do|to) (?:i )?(?:complete|do)\b",
        r"\bamazon gig\b",
        r"\brejected\b",
    )
)

# Purchase/quantity language — only mid-gig when CRWD/gig context is also present
# (avoids inventing discovery on cold-start "how many products for it?").
_MID_GIG_BUY_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bhow many (?:products?|items?)\b",
        r"\b(?:need|have) to buy\b",
        r"\bbuy (?:the )?(?:product|item|products|items)\b",
        r"\border (?:the )?(?:product|item)\b",
        r"\bhow many (?:do i|to) (?:need|buy|order)\b",
    )
)

_COMPILED_RULES: Tuple[Tuple[str, float, Tuple[re.Pattern[str], ...]], ...] = tuple(
    (label, score, tuple(re.compile(p, re.IGNORECASE) for p in patterns))
    for label, score, patterns in _LABEL_RULES
)

# Contextual crwd_db reads — soft evidence only (never hard-label mid-gig/discovery).
_CONTEXTUAL_CRWD_ACTIONS = frozenset({
    "get_user_gigs",
    "get_user_gig_status",
    "get_user_gig_history",
    "get_waitlisted_gigs",
    "get_gig_details",
    "list_active_gigs",
    "get_user",
})

_SOFT_PROOF_ACTIONS = frozenset({"get_user_receipts"})
_DOT_ACTIONS = frozenset({"get_user_transfers", "get_transfer"})

_SOFT_TOOL_DESCRIPTIONS: Dict[str, str] = {
    "get_user": "profile lookup (context only)",
    "get_user_gigs": "enrolled gigs lookup (context only)",
    "get_user_gig_status": "gig status lookup (context only)",
    "get_user_gig_history": "gig history lookup (context only)",
    "get_waitlisted_gigs": "waitlisted gigs lookup (context only)",
    "get_gig_details": "gig details lookup (context only)",
    "list_active_gigs": "open gigs browse lookup (context only)",
    "get_user_receipts": "receipts lookup (context only)",
    "get_user_transfers": "payout lookup (context only)",
    "get_transfer": "payout lookup (context only)",
}


@dataclass
class ClassificationResult:
    labels: List[str] = field(default_factory=list)
    acts: List[str] = field(default_factory=list)
    confidence: str = "low"  # "high" | "low"
    reasons: List[str] = field(default_factory=list)
    source: str = "heuristic"  # tools|heuristic|llm|sticky|mixed|acts
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


def _tool_args_dict(args: Any) -> Dict[str, Any]:
    """Normalize tool args to a dict (may be JSON string from hooks)."""
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _gig_hint_from_tool_args(args: Dict[str, Any]) -> str:
    """Best-effort gig name/id from tool args for soft LLM context only."""
    for key in (
        "gig_name",
        "name",
        "title",
        "gig_title",
        "gig_id",
        "id",
        "identifier",
    ):
        raw = args.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def record_tool_evidence_hook(**kwargs: Any) -> None:
    """``post_tool_call`` — record tool name/action; mark handoff for ``crwd_handoff``."""
    tool_name = str(kwargs.get("tool_name") or "").strip()
    if not tool_name:
        return
    if tool_name == "crwd_handoff":
        _handoff_this_turn.set(True)

    args_dict = _tool_args_dict(kwargs.get("args"))
    action = str(args_dict.get("action") or "").strip()
    gig_hint = _gig_hint_from_tool_args(args_dict)

    entry: Dict[str, str] = {"tool": tool_name, "action": action}
    if gig_hint:
        entry["gig_hint"] = gig_hint

    # Capture store_proof verdicts for proof-acceptance / proof-rejection labels.
    if tool_name == "crwd_db" and action == "store_proof":
        status = str(args_dict.get("status") or "").strip().lower()
        is_completed = ""
        raw_result = kwargs.get("result")
        if isinstance(raw_result, str) and raw_result.strip():
            try:
                parsed = json.loads(raw_result)
                if isinstance(parsed, dict):
                    status = str(parsed.get("status") or status).strip().lower()
                    if parsed.get("is_gig_completed") is True:
                        is_completed = "true"
                    elif parsed.get("is_gig_completed") is False:
                        is_completed = "false"
            except (json.JSONDecodeError, TypeError):
                pass
        if status:
            entry["proof_status"] = status
        if is_completed:
            entry["is_gig_completed"] = is_completed

    try:
        current = list(_tool_evidence_this_turn.get() or ())
    except LookupError:
        current = []
    current.append(entry)
    _tool_evidence_this_turn.set(tuple(current))


def handoff_tool_hook(**kwargs: Any) -> None:
    """Backward-compatible alias for ``record_tool_evidence_hook``."""
    record_tool_evidence_hook(**kwargs)


def soft_tool_facts(
    evidence: Optional[Sequence[Dict[str, str]]] = None,
) -> List[str]:
    """Describe this-turn tools as soft context (not hard topic labels)."""
    evidence = list(evidence if evidence is not None else tool_evidence_this_turn())
    facts: List[str] = []
    for entry in evidence:
        tool = str(entry.get("tool") or "").strip()
        action = str(entry.get("action") or "").strip()
        gig_hint = str(entry.get("gig_hint") or "").strip()
        if tool == "crwd_handoff":
            continue
        if tool == "dot" and (not action or action in _DOT_ACTIONS):
            facts.append("dot payout lookup (context only)")
            continue
        if tool != "crwd_db" or not action:
            continue
        desc = _SOFT_TOOL_DESCRIPTIONS.get(action)
        if not desc:
            continue
        if gig_hint and action in {
            "get_gig_details",
            "get_user_gig_status",
            "list_active_gigs",
            "get_user_gigs",
        }:
            facts.append(
                f"crwd_db.{action} — looked up {gig_hint} (context only)"
            )
        else:
            facts.append(f"crwd_db.{action} ({desc})")
    return facts


def _tool_gig_hints(
    evidence: Optional[Sequence[Dict[str, str]]] = None,
) -> List[str]:
    """Deduped gig_hint values from this-turn soft tool evidence."""
    evidence = list(evidence if evidence is not None else tool_evidence_this_turn())
    hints: List[str] = []
    seen: Set[str] = set()
    for entry in evidence:
        hint = str(entry.get("gig_hint") or "").strip()
        if not hint:
            continue
        key = _compact_name(hint) or _normalize_name(hint)
        if not key or key in seen:
            continue
        seen.add(key)
        hints.append(hint)
    return hints


def _member_mentions_tool_gig_hint(
    member_text: str,
    gig_hints: Sequence[str],
) -> bool:
    """True when member text overlaps a this-turn tool gig_hint (fuzzy).

    Requires the hint (or a substantial compact form) to appear in the member
    message — never the reverse alone — so short replies like ``ok`` cannot
    inherit a looked-up gig title.
    """
    if not member_text or not gig_hints:
        return False
    extracted = _extract_gig_name(member_text)
    hint_set = {h for h in gig_hints if h}
    if extracted and _gig_name_in_enrolled(extracted, hint_set):
        return True

    member_n = _normalize_name(member_text)
    member_c = _compact_name(member_text)
    if not member_n and not member_c:
        return False
    for hint in hint_set:
        hint_n = _normalize_name(hint)
        hint_c = _compact_name(hint)
        if hint_c and len(hint_c) >= _COMPACT_NAME_MIN_LEN:
            if hint_c in member_c:
                return True
            if (
                member_c
                and len(member_c) >= _COMPACT_NAME_MIN_LEN
                and member_c in hint_c
            ):
                return True
        if hint_n and len(hint_n) >= _COMPACT_NAME_MIN_LEN and hint_n in member_n:
            return True
    return False


def hard_labels_from_tools(
    evidence: Optional[Sequence[Dict[str, str]]] = None,
) -> Tuple[List[str], List[str]]:
    """Hard labels from tools — ``crwd_handoff`` and this-turn ``store_proof``."""
    evidence = list(evidence if evidence is not None else tool_evidence_this_turn())
    labels: List[str] = []
    reasons: List[str] = []
    for entry in evidence:
        tool = str(entry.get("tool") or "").strip()
        if tool == "crwd_handoff" and "handoff-escalation" not in labels:
            labels.append("handoff-escalation")
            reasons.append("tool:crwd_handoff")

    proof_labels, proof_reasons = proof_verdict_labels_from_tools(evidence)
    for label in proof_labels:
        if label not in labels:
            labels.append(label)
    reasons.extend(proof_reasons)

    if _turn_completed_gig_from_tools(evidence) and "gig-complete" not in labels:
        labels.append("gig-complete")
        reasons.append("tool:store_proof:gig_complete")
    return labels, reasons


def proof_verdict_labels_from_tools(
    evidence: Optional[Sequence[Dict[str, str]]] = None,
) -> Tuple[List[str], List[str]]:
    """Turn-scoped proof labels from ``store_proof`` results this turn.

    * Any non-accepted status → ``proof-rejection``
    * Else all accepted (≥1) → ``proof-acceptance``
    """
    evidence = list(evidence if evidence is not None else tool_evidence_this_turn())
    statuses: List[str] = []
    for entry in evidence:
        tool = str(entry.get("tool") or "").strip()
        action = str(entry.get("action") or "").strip()
        if tool != "crwd_db" or action != "store_proof":
            continue
        status = str(entry.get("proof_status") or "").strip().lower()
        if status:
            statuses.append(status)
    if not statuses:
        return [], []
    if any(s != "accepted" for s in statuses):
        return ["proof-rejection"], ["tool:store_proof:rejection"]
    return ["proof-acceptance"], ["tool:store_proof:acceptance"]


def _turn_completed_gig_from_tools(
    evidence: Optional[Sequence[Dict[str, str]]] = None,
) -> bool:
    """True when this turn's store_proof completed a gig."""
    evidence = list(evidence if evidence is not None else tool_evidence_this_turn())
    for entry in evidence:
        if (
            str(entry.get("tool") or "").strip() == "crwd_db"
            and str(entry.get("action") or "").strip() == "store_proof"
            and str(entry.get("is_gig_completed") or "").strip().lower() == "true"
        ):
            return True
    return False


_JAILBREAK_IMPERSONATION_RE = re.compile(
    r"\b(?:ignore\s+(?:previous|all|your)\s+(?:instructions?|rules?|prompts?)|"
    r"jailbreak|developer\s+mode|dan\s+mode|"
    r"bypass\s+(?:your|the|all)|you\s+are\s+now|"
    r"pretend\s+(?:i\s+am|to\s+be|you(?:'re|\s+are))|"
    r"act\s+(?:as|like)\s+(?:user|member|admin|me)|"
    r"impersonat|log\s*in\s+as|"
    r"i\s+am\s+user\s+[0-9a-fA-F]{24})\b",
    re.IGNORECASE,
)


def _resolve_member_id_for_labels(contact_id: str) -> str:
    """Best-effort CRWD user id for unauthorized-request detection."""
    contact_id = str(contact_id or "").strip()
    if not contact_id:
        return ""
    try:
        from plugins.platforms.chatwoot.coach_context import resolve_member_crwd_id

        return str(resolve_member_crwd_id(contact_id) or "").strip()
    except Exception:
        return ""


def hard_scam_signals(
    user_message: str,
    contact_id: str = "",
    *,
    member_id: str = "",
) -> Tuple[bool, List[str]]:
    """Hard ``scam`` when unauthorized cross-user ask or jailbreak/impersonation.

    Returns ``(force_scam, reasons)``. Unauthorized reason tags start with
    ``hard:unauthorized`` / ``hard:cross_user`` / ``hard:participant`` /
    ``hard:third_party_pii`` so callers can strip conflicting topic labels.
    """
    reasons: List[str] = []
    text = (user_message or "").strip()
    if not text:
        return False, reasons

    if _JAILBREAK_IMPERSONATION_RE.search(text):
        reasons.append("hard:jailbreak_or_impersonation")

    try:
        from plugins.platforms.chatwoot.coach_context import (
            cross_user_request_active,
            message_requests_other_member,
            message_requests_unauthorized_info,
        )
    except Exception:
        cross_user_request_active = None  # type: ignore[assignment]
        message_requests_other_member = None  # type: ignore[assignment]
        message_requests_unauthorized_info = None  # type: ignore[assignment]

    unauthorized = False
    if message_requests_unauthorized_info is not None:
        try:
            matched, kind = message_requests_unauthorized_info(text)
            if matched:
                unauthorized = True
                if kind == "participant_list":
                    reasons.append("hard:participant_list")
                elif kind == "third_party_pii":
                    reasons.append("hard:third_party_pii")
                else:
                    reasons.append("hard:unauthorized_other_member")
        except Exception as exc:
            logger.debug("[chatwoot-labels-auto] unauthorized-info detect failed: %s", exc)

    if cross_user_request_active is not None:
        try:
            if cross_user_request_active():
                unauthorized = True
                if "hard:cross_user_flag" not in reasons:
                    reasons.append("hard:cross_user_flag")
        except LookupError:
            pass

    if not unauthorized and message_requests_other_member is not None:
        resolved = (member_id or "").strip() or _resolve_member_id_for_labels(contact_id)
        try:
            if message_requests_other_member(text, resolved):
                unauthorized = True
                if not any(r.startswith("hard:") and r != "hard:jailbreak_or_impersonation" for r in reasons):
                    reasons.append("hard:unauthorized_other_member")
        except Exception as exc:
            logger.debug("[chatwoot-labels-auto] unauthorized detect failed: %s", exc)

    return bool(reasons), reasons


_UNAUTHORIZED_HARD_REASON_PREFIXES = (
    "hard:unauthorized",
    "hard:cross_user",
    "hard:participant_list",
    "hard:third_party_pii",
)
_UNAUTHORIZED_STRIP_TOPICS = frozenset({
    "gig-discovery",
    "mid-gig-support",
    "general-inquiry",
})


def _is_unauthorized_hard_reason(reason: str) -> bool:
    return any(
        reason == p or reason.startswith(p)
        for p in _UNAUTHORIZED_HARD_REASON_PREFIXES
    )


def _strip_topics_for_unauthorized(labels: List[str]) -> List[str]:
    """Drop discovery/mid-gig/general when unauthorized hard-scam wins triage."""
    return [l for l in labels if l not in _UNAUTHORIZED_STRIP_TOPICS]

def labels_from_tools(
    evidence: Optional[Sequence[Dict[str, str]]] = None,
    *,
    contact_id: str = "",
) -> Tuple[List[str], List[str]]:
    """Backward-compatible wrapper — returns hard tool labels only (handoff)."""
    del contact_id
    return hard_labels_from_tools(evidence)


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


def _has_strong_topic_signal(user_message: str) -> bool:
    """True when current member text alone clearly signals a topic switch."""
    text = (user_message or "").strip()
    if not text:
        return False
    lowered = text.lower()
    for label, _score, patterns in _COMPILED_RULES:
        if label == "off-topic":
            continue
        if any(p.search(lowered) for p in patterns):
            return True
    if _matches_any(lowered, _PROOF_PATTERNS):
        return True
    if _extract_gig_name(text):
        return True
    return False


def _is_contextual_followup(user_message: str) -> bool:
    """Pronoun/deixis follow-ups that refer to the prior turn's topic.

    Does not invent a topic on its own — caller must require sticky exists.
    Clear topic switches (strong pattern / named gig) are excluded so those
    still go through LLM / heuristic classification.
    """
    text = (user_message or "").strip()
    if not text or len(text) > _CONTEXTUAL_FOLLOWUP_MAX_LEN:
        return False
    if _is_greeting_message(text) or _is_meta_identity_message(text):
        return False
    if _is_ambiguous_message(text):
        return False
    if not _CONTEXTUAL_DEIXIS_RE.search(text):
        return False
    if _has_strong_topic_signal(text):
        return False
    return True


def _should_inherit_sticky(
    user_message: str,
    sticky_topics: Sequence[str],
    sticky_acts: Sequence[str],
) -> bool:
    if not sticky_topics and not sticky_acts:
        return False
    return _is_ambiguous_message(user_message) or _is_contextual_followup(
        user_message
    )


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


def _collect_member_messages(
    conversation_history: Sequence[Any],
    current_user_message: str,
    *,
    limit: int = _LLM_MEMBER_TURNS,
) -> List[str]:
    """Return up to ``limit`` member messages, oldest first."""
    current = (current_user_message or "").strip()
    prior: List[str] = []
    for msg in reversed(conversation_history or ()):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        if text == current:
            continue
        prior.append(text)
        if len(prior) >= max(0, limit - 1):
            break
    prior.reverse()
    if current:
        prior.append(current)
    return prior[-limit:]


def _collect_assistant_replies(
    conversation_history: Sequence[Any],
    assistant_response: str = "",
    *,
    limit: int = _LLM_ASSISTANT_TURNS,
) -> List[str]:
    """Return up to ``limit`` recent coach replies (truncated), newest last."""
    replies: List[str] = []
    for msg in reversed(conversation_history or ()):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        replies.append(content.strip()[:_LLM_ASSISTANT_TRUNCATE])
        if len(replies) >= limit:
            break
    replies.reverse()
    latest = (assistant_response or "").strip()
    if latest:
        truncated = latest[:_LLM_ASSISTANT_TRUNCATE]
        if not replies or replies[-1] != truncated:
            replies.append(truncated)
    return replies[-limit:]


def _build_regex_context(
    user_message: str,
    conversation_history: Optional[Sequence[Any]] = None,
) -> str:
    """Narrow member-only text for heuristic fallback and grounding (no coach prose)."""
    primary = (user_message or "").strip()
    history = conversation_history or ()
    prior = _prior_user_content(history, primary)

    if _is_greeting_message(primary) or _is_meta_identity_message(primary):
        return primary.lower()
    if (
        (_is_ambiguous_message(primary) or _is_contextual_followup(primary))
        and prior
    ):
        return f"{primary} {prior}".lower()
    return primary.lower()


def _build_llm_feature_bundle(
    user_message: str,
    conversation_history: Optional[Sequence[Any]] = None,
    assistant_response: str = "",
    *,
    soft_facts: Optional[Sequence[str]] = None,
    enrollment_summary: str = "",
    sticky_acts: Optional[Sequence[str]] = None,
    sticky_labels: Optional[Sequence[str]] = None,
) -> str:
    """Rich multi-turn context for Stage-1 dialogue-act LLM."""
    history = conversation_history or ()
    members = _collect_member_messages(history, user_message)
    coaches = _collect_assistant_replies(history, assistant_response)

    lines: List[str] = ["=== Member messages (newest last) ==="]
    for idx, text in enumerate(members, start=1):
        lines.append(f"Member {idx}: {text or '(empty)'}")
    if coaches:
        lines.append("=== Coach replies (context only — do not infer topic) ===")
        for idx, text in enumerate(coaches, start=1):
            lines.append(f"Coach {idx}: {text}")
    if enrollment_summary:
        lines.append(f"Enrollment: {enrollment_summary}")
    if soft_facts:
        lines.append("Tools this turn (context only): " + "; ".join(soft_facts))
    if sticky_acts:
        lines.append("Prior acts (sticky): " + ", ".join(sticky_acts))
    elif sticky_labels:
        lines.append("Prior labels (sticky): " + ", ".join(sticky_labels))
    return "\n".join(lines)


def _build_turn_context(
    user_message: str,
    conversation_history: Optional[Sequence[Any]] = None,
    assistant_response: str = "",
) -> Tuple[str, str]:
    """Return (regex_text, llm_feature_bundle)."""
    regex_text = _build_regex_context(user_message, conversation_history)
    llm_blob = _build_llm_feature_bundle(
        user_message,
        conversation_history,
        assistant_response,
    )
    return regex_text, llm_blob


def _enrollment_summary(contact_id: str) -> Tuple[Optional[Tuple[bool, Set[str]]], str]:
    membership = _member_enrollment(contact_id) if contact_id else None
    if membership is None:
        return None, "unknown"
    enrolled, names = membership
    if not enrolled:
        return membership, "not enrolled"
    sample = ", ".join(sorted(names)[:5])
    suffix = "…" if len(names) > 5 else ""
    return membership, f"enrolled ({sample}{suffix})" if sample else "enrolled"


def _labels_to_acts(labels: Sequence[str]) -> List[str]:
    acts: List[str] = []
    for label in labels:
        act = _LABEL_TO_ACT.get(label)
        if act and act not in acts:
            acts.append(act)
    return acts


def acts_to_labels(
    acts: Sequence[str],
    user_message: str,
    contact_id: str,
    membership: Optional[Tuple[bool, Set[str]]] = None,
    *,
    sticky_topics: Optional[Sequence[str]] = None,
) -> List[str]:
    """Stage 2 — map dialogue acts to *applied* Chatwoot label titles only.

    Unapplied taxonomy titles (mid-gig, discovery, scam, …) are never emitted.
    """
    del membership  # enrollment remaps only mattered for unapplied gig labels
    labels: List[str] = []
    for act in acts:
        if act not in DIALOGUE_ACTS:
            continue
        if act == "payout":
            _ensure_label(labels, "payment-issue")
        elif act == "app_nav":
            _ensure_label(labels, "app-help")
        elif act == "ambiguous_followup":
            if sticky_topics:
                for topic in sticky_topics:
                    title = str(topic).strip().lower()
                    if title in _STICKY_TOPIC_LABELS:
                        _ensure_label(labels, title)
        # Unapplied acts (account_status, eligibility, proof, enrolled_gig_help,
        # browse_open_gigs, general_inquiry, scam, chitchat, escalate) → no topic.

    return labels


# Intent topics that must not ride along on a store_proof turn unless grounded.
_PROOF_TURN_SUPPRESS_TOPICS = frozenset({"payment-issue", "app-help"})


def _store_proof_missing_status_reason(
    evidence: Sequence[Dict[str, str]],
) -> Optional[str]:
    """Observability when ``store_proof`` ran but no verdict status was recorded."""
    for entry in evidence:
        if (
            str(entry.get("tool") or "").strip() == "crwd_db"
            and str(entry.get("action") or "").strip() == "store_proof"
            and not str(entry.get("proof_status") or "").strip()
        ):
            return "tool:store_proof:missing_status"
    return None


def _strip_topics_when_proof_turn(
    labels: Sequence[str],
    user_message: str,
    evidence: Sequence[Dict[str, str]],
) -> Tuple[List[str], List[str]]:
    """Drop ungrounded payment/app topics when this turn has a proof verdict.

    Receipt / proof uploads often inherit sticky ``payment-issue`` or pick up
    payment-ish OCR words. Hard ``store_proof`` evidence means the turn is a
    proof outcome — keep ``proof-*`` / ``gig-complete`` via finalize, and only
    keep intent topics when member text independently grounds them.

    Returns ``(labels, extra_reasons)``.
    """
    extra: List[str] = []
    missing = _store_proof_missing_status_reason(evidence)
    if missing:
        extra.append(missing)

    proof_labels, _proof_reasons = proof_verdict_labels_from_tools(evidence)
    applied = [l for l in labels if l in APPLIED_LABEL_TITLES]
    if not proof_labels:
        return applied, extra

    kept: List[str] = []
    for label in applied:
        if label in _PROOF_TURN_SUPPRESS_TOPICS and not _llm_label_grounded(
            label, user_message, []
        ):
            extra.append(f"proof_turn:suppress:{label}")
            continue
        kept.append(label)
    return kept, extra


def _apply_conflict_post_checks(
    labels: List[str],
    acts: Sequence[str],
    user_message: str,
    soft_facts: Sequence[str],
    membership: Optional[Tuple[bool, Set[str]]],
) -> List[str]:
    """Member-primary applied topics win over soft tool noise."""
    del soft_facts, membership, acts, user_message
    # Keep only applied titles that Stage 2 may emit.
    # Proof-turn intent suppress runs later in classify_conversation._finish.
    return [l for l in labels if l in APPLIED_LABEL_TITLES]


def _filter_grounded_acts(
    acts: Sequence[str],
    user_message: str,
    *,
    sticky_labels: Optional[Sequence[str]] = None,
    sticky_acts: Optional[Sequence[str]] = None,
    tool_gig_hints: Optional[Sequence[str]] = None,
) -> List[str]:
    """Drop applied acts that lack member-text support."""
    act_label = {
        "payout": "payment-issue",
        "app_nav": "app-help",
    }
    kept: List[str] = []
    for act in acts:
        if act not in DIALOGUE_ACTS:
            continue
        label = act_label.get(act)
        if label and not _llm_label_grounded(
            label,
            user_message,
            [],
            sticky_labels=sticky_labels,
            sticky_acts=sticky_acts,
            tool_gig_hints=tool_gig_hints,
        ):
            continue
        if act not in kept:
            kept.append(act)
    return kept


def _has_crwd_anchor(text: str) -> bool:
    return bool(_CRWD_ANCHOR_RE.search(text))


def _matches_any(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


_COMPACT_NAME_MIN_LEN = 4

# Pronouns / chitchat that must never become a gig name via prefix or bare path.
_GIG_NAME_REJECT = frozenset({
    "you",
    "yourself",
    "u",
    "me",
    "myself",
    "my gig",
    "my gigs",
    "ok",
    "okay",
    "yes",
    "yeah",
    "yep",
    "no",
    "nope",
    "sure",
    "thanks",
    "thank you",
    "thx",
    "k",
    "hi",
    "hello",
    "hey",
    "yo",
    "sup",
})

# Single-token bare titles that are CRWD vocabulary, not product names.
_BARE_NAME_BLOCKLIST = frozenset({
    "payment",
    "payout",
    "refund",
    "money",
    "proof",
    "account",
    "membership",
    "eligible",
    "ineligible",
    "crwd",
    "gig",
    "gigs",
    "help",
    "support",
    "campaign",
    "browse",
    "apply",
    "deadline",
    "receipt",
    "receipts",
    "status",
    "profile",
    "info",
})

# Message starts with a question/command word — not a bare product title.
# (Prefixed forms like "what about X" are handled before the bare path.)
_BARE_NAME_INTERROGATIVE_RE = re.compile(
    r"^(?:what|who|where|when|why|how|is|are|do|does|did|can|could|would|will|"
    r"should|tell|give|show|find|help|please|i|i'm|im|my)\b",
    re.IGNORECASE,
)


def _trim_gig_name_candidate(name: str) -> str:
    """Strip trailing punctuation / clause boundaries from an extracted name."""
    name = (name or "").strip(" ?.")
    for sep in (".", "?", "!", "\n"):
        if sep in name:
            name = name.split(sep, 1)[0].strip(" ?.")
    return name.strip(" ?.")


def _is_rejected_gig_name(name: str) -> bool:
    lowered = (name or "").strip().lower()
    if not lowered or lowered in _GIG_NAME_REJECT:
        return True
    if lowered.startswith("my "):
        return True
    if _PROFILE_SELF_RE.search(name or ""):
        return True
    return False


def _looks_like_bare_gig_name(candidate: str) -> bool:
    """True when the whole message is plausibly just a product/gig title."""
    text = _trim_gig_name_candidate(candidate)
    if not text:
        return False
    if _is_greeting_message(text) or _is_meta_identity_message(text):
        return False
    if _is_rejected_gig_name(text):
        return False
    if _BARE_NAME_INTERROGATIVE_RE.match(text):
        return False
    if _PROFILE_SELF_RE.search(text):
        return False
    if len(text) <= _AMBIGUOUS_MAX_LEN and _AMBIGUOUS_RE.match(text):
        return False

    normalized = _normalize_name(text)
    tokens = [t for t in normalized.split() if t]
    compact = _compact_name(text)
    if not tokens and not compact:
        return False
    if len(tokens) == 1:
        if tokens[0] in _BARE_NAME_BLOCKLIST:
            return False
        return len(compact) >= _COMPACT_NAME_MIN_LEN
    # Multi-word title (e.g. "crown of glory"); reject if every token is blocklisted.
    if all(t in _BARE_NAME_BLOCKLIST for t in tokens):
        return False
    return True


def _extract_gig_name(message: str) -> str:
    """Best-effort gig name from the member message (or concatenated context)."""
    text = (message or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    for prefix in (
        "next steps for ",
        "status for ",
        "tell me about ",
        "details about ",
        "give me details about ",
        "give details about ",
        "what about ",
        "how about ",
        "how is ",
        "how's ",
    ):
        idx = lowered.find(prefix)
        if idx < 0:
            continue
        name = _trim_gig_name_candidate(text[idx + len(prefix) :])
        if not _is_rejected_gig_name(name):
            return name
    quoted = re.search(r'"([^"]+)"', text)
    if quoted:
        name = quoted.group(1).strip()
        if not _is_rejected_gig_name(name):
            return name
    about = re.search(
        r"\b(?:about|for|on) (?:the )?(.+?) gig\b",
        text,
        re.IGNORECASE,
    )
    if about:
        name = about.group(1).strip()
        if not _is_rejected_gig_name(name):
            return name
    # Bare product title: "crown of glory ?" / "smokeboxbbq"
    bare = _trim_gig_name_candidate(text)
    if bare and _looks_like_bare_gig_name(bare):
        return bare
    return ""


def _normalize_name(value: str) -> str:
    """Spaced lowercase tokens (punctuation → space)."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _compact_name(value: str) -> str:
    """Alphanumeric-only lowercase key (``SmokeBox BBQ`` → ``smokeboxbbq``)."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _gig_name_in_enrolled(gig_name: str, enrolled_names: Set[str]) -> bool:
    """True when member gig name fuzzy-matches an enrolled gig title.

    Supports compact forms (``smokeboxbbq`` ↔ ``SmokeBox BBQ``) plus spaced
    substring containment.
    """
    if not gig_name:
        return True
    needle = _normalize_name(gig_name)
    needle_c = _compact_name(gig_name)
    if not needle and not needle_c:
        return True
    for name in enrolled_names:
        hay = _normalize_name(name)
        hay_c = _compact_name(name)
        if not hay and not hay_c:
            continue
        if needle and hay and (needle in hay or hay in needle):
            return True
        if needle_c and hay_c:
            if needle_c == hay_c:
                return True
            if (
                len(needle_c) >= _COMPACT_NAME_MIN_LEN
                and len(hay_c) >= _COMPACT_NAME_MIN_LEN
                and (needle_c in hay_c or hay_c in needle_c)
            ):
                return True
    return False


def _member_has_completed_gig(contact_id: str) -> Optional[bool]:
    """Return whether the member completed ≥1 gig, or ``None`` if unknown."""
    contact_id = str(contact_id or "").strip()
    if not contact_id:
        return None
    now = time.monotonic()
    with _enrollment_cache_lock:
        cached = _completed_gig_cache.get(contact_id)
        if cached and (now - cached[0]) < _ENROLLMENT_CACHE_TTL_S:
            return cached[1]

    result: Optional[bool] = None
    if os.getenv("CRWD_MONGO_URI"):
        try:
            from plugins.platforms.chatwoot.coach_context import resolve_member_crwd_id
            from tools.crwd_db_tool import user_has_completed_gig

            user_id = resolve_member_crwd_id(contact_id)
            if user_id:
                result = user_has_completed_gig(user_id)
        except Exception as exc:
            logger.debug("[chatwoot-labels-auto] completed-gig lookup failed: %s", exc)
            result = None

    with _enrollment_cache_lock:
        _completed_gig_cache[contact_id] = (now, result)
    return result


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
    """Legacy proof/mid-gig heuristics — no longer emit unapplied titles.

    Returns ``(score, suppress_discovery_fallback)``. Kept so call sites and
    enrollment-unknown suppress behavior stay stable; assignment is a no-op.
    """
    del matched  # unapplied titles are not appended
    proof = _matches_any(text, _PROOF_PATTERNS)
    mid = _matches_any(text, _MID_GIG_PATTERNS)
    if not mid and _matches_any(text, _MID_GIG_BUY_PATTERNS) and (
        _has_crwd_anchor(text) or bool(_extract_gig_name(user_message))
        or bool(_extract_gig_name(text))
    ):
        mid = True
    if not mid and _extract_gig_name(user_message):
        mid = True
    if _PROFILE_SELF_RE.search(user_message or ""):
        mid = False
    if not proof and not mid:
        return 0.0, False

    membership = _member_enrollment(contact_id) if (proof or mid) else None

    if proof:
        reasons.append("heuristic:proof:unapplied")
        return 0.0, False

    if membership is None and mid:
        reasons.append("heuristic:mid-gig:skipped_unknown_enrollment")
        return 0.0, True

    if mid:
        reasons.append("heuristic:mid-gig:unapplied")
    return 0.0, False


def _finalize_labels(
    topics: Sequence[str],
    handoff: bool,
    *,
    proof_verdicts: Optional[Sequence[str]] = None,
    new_user: bool = False,
) -> List[str]:
    """Dedupe to applied titles; attach handoff / turn-hard / new-user.

    ``proof_verdicts`` may include ``proof-acceptance``, ``proof-rejection``,
    and ``gig-complete`` (turn-scoped hard labels from tools).
    """
    deduped: List[str] = []
    for label in topics:
        title = str(label).strip().lower()
        if title not in APPLIED_LABEL_TITLES or title in deduped:
            continue
        if title in _NON_STICKY_LABELS:
            continue
        if title.startswith("risk-"):
            continue
        deduped.append(title)
    for title in proof_verdicts or ():
        t = str(title).strip().lower()
        if t in APPLIED_LABEL_TITLES and t not in deduped:
            deduped.append(t)
    if new_user and "new-user" not in deduped:
        deduped.append("new-user")
    if handoff and "handoff-escalation" not in deduped:
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
        return [], ["heuristic:empty->no-topic"], 0.2, True

    for label, score, patterns in _COMPILED_RULES:
        if any(p.search(text) for p in patterns):
            if label not in matched:
                matched.append(label)
                reasons.append(f"heuristic:{label}")
            best = max(best, score)
            strong = True

    proof_score, _suppress = _apply_proof_and_mid_gig_labels(
        text, user_message, contact_id, matched, reasons
    )
    if proof_score:
        best = max(best, proof_score)
        strong = True

    fallback_only = False
    if not matched:
        fallback_only = True
        reasons.append("heuristic:fallback->no-topic")
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
    *,
    sticky_labels: Optional[Sequence[str]] = None,
    sticky_acts: Optional[Sequence[str]] = None,
    tool_gig_hints: Optional[Sequence[str]] = None,
) -> bool:
    """False for applied topic labels invented without member evidence."""
    del sticky_labels, sticky_acts, tool_gig_hints  # gig sticky unused for applied set
    if label not in _LLM_MUST_GROUND:
        return True
    if label in tool_topic_labels:
        return True
    text = (member_text or "").lower()
    if label == "payment-issue":
        return bool(
            re.search(
                r"\b(paid|payment|payout|dot|money|refund|chargeback)\b",
                text,
                re.IGNORECASE,
            )
        )
    if label == "app-help":
        return bool(
            re.search(
                r"\b(where|tab|section|navigate|broken|error|bug|crash|login|"
                r"won'?t load|not working|in the app)\b",
                text,
                re.IGNORECASE,
            )
        )
    return True


def classify_acts_with_auxiliary(llm_context: str) -> Optional[Dict[str, Any]]:
    """Stage 1 — dialogue-act classification via auxiliary LLM.

    Uses plain JSON in message content (no tools / tool_choice).
    """
    acts_list = ", ".join(sorted(DIALOGUE_ACTS))
    system = (
        "You classify a CRWD Coach Chatwoot conversation into dialogue acts. "
        "Classify ONLY from member messages. Coach replies are context only — "
        "do NOT infer payout or browse_open_gigs from coach phrasing (get paid, gigs). "
        "Looking up enrolled gigs (get_user_gigs) is default coach behavior and must "
        "NOT imply enrolled_gig_help unless the MEMBER asks about enrolled-gig "
        "steps/deadline/requirements/proof. "
        "account_status = profile / account details / member's own name / "
        "membership / ban / suspension. "
        "eligibility = not eligible / can't join / wrong state / age. "
        "general_inquiry = what CRWD is, how the platform/app works, what gigs are, "
        "how to apply/join, legitimacy/trust questions (not fraud signals). "
        "browse_open_gigs = finding/browsing available gigs (near me, explore) — "
        "not explaining what CRWD is. "
        "scam = phishing/fraud OR requesting another member's private data "
        "(name/gigs/account by foreign user_id) OR gig participant/roster lists "
        "OR asking for another person's phone/email/contact OR impersonation OR "
        "jailbreak/prompt-injection / bad-actor instructions. "
        "Do NOT use scam for benign 'is CRWD legit?' (that is general_inquiry). "
        "Do NOT use browse_open_gigs for participant lists of a named gig — that is scam. "
        "Do NOT use a topic act for opt-out alone (stop texting, unsubscribe). "
        f"Allowed acts (use only these): {acts_list}. "
        'Return JSON only: {"acts": ["..."], "primary": "...", '
        '"confidence": "high|low", "reasons": ["..."]}. '
        "Choose every act that applies. Never invent acts."
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
            max_tokens=250,
            timeout=15,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return None
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        raw_acts = parsed.get("acts")
        if not isinstance(raw_acts, list):
            return None
        acts: List[str] = []
        for item in raw_acts:
            act = str(item).strip().lower()
            if act in DIALOGUE_ACTS and act not in acts:
                acts.append(act)
        if not acts:
            return None
        primary = str(parsed.get("primary") or acts[0]).strip().lower()
        if primary not in acts:
            primary = acts[0]
        confidence = str(parsed.get("confidence") or "low").strip().lower()
        if confidence not in {"high", "low"}:
            confidence = "low"
        reasons = parsed.get("reasons")
        reason_list = (
            [str(r) for r in reasons]
            if isinstance(reasons, list)
            else []
        )
        return {
            "acts": acts,
            "primary": primary,
            "confidence": confidence,
            "reasons": reason_list,
        }
    except Exception as exc:
        logger.debug("[chatwoot-labels-auto] auxiliary act classify failed: %s", exc)
        return None


def classify_with_auxiliary(llm_context: str) -> Optional[List[str]]:
    """Backward-compatible label fallback — maps acts → labels."""
    result = classify_acts_with_auxiliary(llm_context)
    if not result:
        return None
    return acts_to_labels(result["acts"], "", "")


def _conversation_key(account_id: str, conversation_id: str) -> str:
    return f"{account_id}:{conversation_id}"


def _conversation_status(account_id: str, conversation_id: str) -> Optional[str]:
    """Return Chatwoot conversation status (``open`` / ``pending`` / …), or None.

    Best-effort — any lookup failure returns None so labeling still proceeds.
    """
    try:
        from plugins.platforms.chatwoot.labels_tool import _api_request

        ok, data, _err = _api_request(
            "GET",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}",
        )
        if not ok or not isinstance(data, dict):
            return None
        # Chatwoot may wrap the conversation under payload or return it flat.
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        status = payload.get("status") if isinstance(payload, dict) else None
        if status is None and isinstance(data.get("conversation"), dict):
            status = data["conversation"].get("status")
        text = str(status or "").strip().lower()
        return text or None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[chatwoot-labels-auto] conversation status lookup failed: %s", exc)
        return None


def _preserved_labels(account_id: str, conversation_id: str) -> List[str]:
    """Labels already on the conversation that this classifier must not clear.

    Classification emits topic / turn labels and assigns with ``replace=True``,
    so state owned elsewhere is erased unless re-attached:

    * ``handoff-escalation`` — only while conversation status is ``open``
      (human owns the thread after ``crwd_handoff``). Dropped when status is
      no longer ``open`` (bot owns again, typically ``pending``).
    * ``risk-*`` — fraud band from crwd-risk-analyser.

    ``gig-complete`` is turn-scoped (this-turn ``is_gig_completed``) and is
    **not** preserved.

    Unlike the in-process sticky-topic cache, this reads Chatwoot itself — the
    state lives there, survives restarts, and a human clearing a label sticks.

    Best-effort: any lookup failure returns [] and labeling proceeds rather than
    blocking the turn.
    """
    try:
        from plugins.platforms.chatwoot.labels_tool import (
            _api_request,
            _extract_conversation_labels,
        )

        ok, data, _err = _api_request(
            "GET",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/labels",
        )
        if not ok:
            return []
        current = _extract_conversation_labels(data)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[chatwoot-labels-auto] preserved-label lookup failed: %s", exc)
        return []

    status = _conversation_status(account_id, conversation_id)
    handoff_active = status == _HANDOFF_ACTIVE_STATUS

    preserved: List[str] = []
    for label in current:
        if label == "handoff-escalation":
            if handoff_active:
                preserved.append(label)
            continue
        if label in _PRESERVED_LABELS or any(
            label.startswith(p) for p in _PRESERVED_PREFIXES
        ):
            preserved.append(label)
    return preserved


def _get_sticky_topics(account_id: str, conversation_id: str) -> List[str]:
    key = _conversation_key(account_id, conversation_id)
    with _last_labels_lock:
        return list(_last_topic_labels.get(key) or [])


def _get_sticky_acts(account_id: str, conversation_id: str) -> List[str]:
    key = _conversation_key(account_id, conversation_id)
    with _last_labels_lock:
        return list(_last_topic_acts.get(key) or [])


def _store_sticky_topics(
    account_id: str,
    conversation_id: str,
    labels: Sequence[str],
    acts: Optional[Sequence[str]] = None,
) -> None:
    topics = [l for l in labels if l in _STICKY_TOPIC_LABELS]
    key = _conversation_key(account_id, conversation_id)
    stored_acts = list(acts) if acts else _labels_to_acts(topics)
    with _last_labels_lock:
        _last_topic_labels[key] = list(topics)
        _last_topic_acts[key] = stored_acts


def clear_sticky_labels_for_tests() -> None:
    """Test helper — wipe in-memory sticky + enrollment caches."""
    with _last_labels_lock:
        _last_topic_labels.clear()
        _last_topic_acts.clear()
    with _enrollment_cache_lock:
        _enrollment_cache.clear()
        _completed_gig_cache.clear()


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
    sticky_acts: Optional[Sequence[str]] = None,
) -> ClassificationResult:
    """Full classification: gates → sticky → act LLM → heuristic fallback."""
    evidence = list(tool_evidence if tool_evidence is not None else tool_evidence_this_turn())
    soft_facts = soft_tool_facts(evidence)
    hard_tool_labels, hard_tool_reasons = hard_labels_from_tools(evidence)
    proof_verdicts = [
        l for l in hard_tool_labels if l in _TURN_HARD_LABELS
    ]
    handoff = bool(handoff_requested) or any(
        str(e.get("tool") or "").strip() == "crwd_handoff" for e in evidence
    )
    tool_keys = [
        f"{e.get('tool')}:{e.get('action') or '-'}"
        for e in evidence
    ]

    membership, enroll_text = _enrollment_summary(contact_id)
    completed = _member_has_completed_gig(contact_id)
    if _turn_completed_gig_from_tools(evidence):
        completed = True
    # Known incomplete → new-user; unknown → skip (do not guess).
    is_new_user = completed is False

    def _finish(
        topic_labels: Sequence[str],
        *,
        acts_out: Sequence[str],
        confidence_out: str,
        reasons_out: Sequence[str],
        source_out: str,
    ) -> ClassificationResult:
        stripped, strip_reasons = _strip_topics_when_proof_turn(
            topic_labels, user_message, evidence
        )
        final = _finalize_labels(
            stripped,
            handoff,
            proof_verdicts=proof_verdicts,
            new_user=is_new_user,
        )
        reason_list = list(reasons_out)
        for reason in strip_reasons:
            if reason not in reason_list:
                reason_list.append(reason)
        if handoff and "handoff-escalation" not in reason_list and "tool:crwd_handoff" not in reason_list:
            reason_list.append("tool:crwd_handoff" if handoff_requested else "handoff")
        if is_new_user and "data:new-user" not in reason_list:
            reason_list.append("data:new-user")
        return ClassificationResult(
            labels=final,
            acts=list(acts_out),
            confidence=confidence_out,
            reasons=reason_list,
            source=source_out,
            tools=tool_keys,
        )

    # Deterministic gates → no topic (unapplied off-topic removed)
    if not (user_message or "").strip():
        return _finish(
            [],
            acts_out=["chitchat"],
            confidence_out="high",
            reasons_out=["gate:empty->no-topic"],
            source_out="heuristic",
        )

    if _is_greeting_message(user_message) or _is_meta_identity_message(user_message):
        reason = (
            "gate:meta->no-topic"
            if _is_meta_identity_message(user_message)
            else "gate:greeting->no-topic"
        )
        return _finish(
            [],
            acts_out=["chitchat"],
            confidence_out="high",
            reasons_out=[reason],
            source_out="heuristic",
        )

    regex_text = _build_regex_context(user_message, conversation_history)
    force_scam, hard_scam_reasons = hard_scam_signals(user_message, contact_id)
    reasons: List[str] = list(hard_tool_reasons)
    source = "heuristic"
    confidence = "low"
    acts: List[str] = []
    labels: List[str] = []

    sticky_list = [
        l for l in (sticky_topics or []) if l in _STICKY_TOPIC_LABELS
    ]
    sticky_act_list = [
        a for a in (sticky_acts or []) if a in DIALOGUE_ACTS
    ]

    # Ambiguous / contextual follow-up → sticky applied topics only.
    if (
        not force_scam
        and _should_inherit_sticky(user_message, sticky_list, sticky_act_list)
    ):
        acts = ["ambiguous_followup"]
        labels = acts_to_labels(
            acts,
            user_message,
            contact_id,
            membership,
            sticky_topics=sticky_list,
        )
        labels = _apply_conflict_post_checks(
            labels, acts, user_message, soft_facts, membership
        )
        reason = (
            "sticky:contextual_followup"
            if _is_contextual_followup(user_message)
            else "sticky:ambiguous_followup"
        )
        return _finish(
            labels,
            acts_out=acts,
            confidence_out="low",
            reasons_out=reasons + [reason],
            source_out="sticky",
        )

    # Aux LLM is primary (accuracy-first). Pattern heuristics are fallback only.
    if allow_llm and _llm_fallback_enabled():
        llm_blob = _build_llm_feature_bundle(
            user_message,
            conversation_history,
            assistant_response,
            soft_facts=soft_facts,
            enrollment_summary=enroll_text,
            sticky_acts=sticky_act_list,
            sticky_labels=sticky_list,
        )
        act_result = classify_acts_with_auxiliary(llm_blob)
        if act_result:
            gig_hints = _tool_gig_hints(evidence)
            acts = _filter_grounded_acts(
                act_result["acts"],
                user_message,
                sticky_labels=sticky_list,
                sticky_acts=sticky_act_list,
                tool_gig_hints=gig_hints,
            )
            if not acts:
                reasons.append("llm:acts_ungrounded")
            else:
                labels = acts_to_labels(
                    acts, user_message, contact_id, membership, sticky_topics=sticky_list
                )
                labels = _apply_conflict_post_checks(
                    labels, acts, user_message, soft_facts, membership
                )
                reasons.extend(
                    [f"llm_act:{a}" for a in acts]
                    + [f"llm:{r}" for r in act_result.get("reasons") or []]
                )
                confidence = str(act_result.get("confidence") or "high")
                source = "llm"
        else:
            reasons.append("llm:act_classify_failed")

    # Heuristic fallback when LLM did not produce labels
    if not labels:
        heur_labels, heur_reasons, heur_score, fallback_only = _heuristic_classify(
            regex_text, user_message, contact_id
        )
        acts = _labels_to_acts(heur_labels) or (["chitchat"] if not heur_labels else [])
        labels = acts_to_labels(
            acts, user_message, contact_id, membership, sticky_topics=sticky_list
        )
        # Prefer direct heuristic applied titles when acts map empty.
        for hl in heur_labels:
            if hl in APPLIED_LABEL_TITLES and hl not in labels:
                labels.append(hl)
        labels = _apply_conflict_post_checks(
            labels, acts, user_message, soft_facts, membership
        )
        reasons.extend(heur_reasons)
        confidence = "high" if (not fallback_only and heur_score >= 0.6) else "low"
        source = "heuristic"

        if confidence == "low" and _should_inherit_sticky(
            user_message, sticky_list, sticky_act_list
        ):
            acts = ["ambiguous_followup"]
            labels = acts_to_labels(
                acts,
                user_message,
                contact_id,
                membership,
                sticky_topics=sticky_list,
            )
            reasons.append("sticky:previous_topics")
            source = "sticky"

    if not labels:
        acts = acts or ["chitchat"]
        reasons.append("fallback:no-topic")

    # Scam is unapplied — keep act/reasons for observability, do not assign.
    if force_scam:
        if "scam" not in acts:
            acts.append("scam")
        for reason in hard_scam_reasons:
            if reason not in reasons:
                reasons.append(reason)
        labels = _strip_topics_for_unauthorized(labels)
        labels = [l for l in labels if l != "scam"]
        if confidence == "low":
            confidence = "high"
        if source in {"heuristic", "sticky"}:
            source = "mixed" if labels else "heuristic"

    return _finish(
        labels,
        acts_out=acts,
        confidence_out=confidence,
        reasons_out=reasons,
        source_out=source,
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
    sticky_acts = _get_sticky_acts(account_id, conversation_id)
    result = classify_conversation(
        user_message=user_message,
        conversation_history=conversation_history,
        contact_id=contact_id,
        handoff_requested=handoff_requested,
        assistant_response=assistant_response,
        tool_evidence=tool_evidence,
        allow_llm=True,
        sticky_topics=sticky,
        sticky_acts=sticky_acts,
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

    # Carry over state this classifier cannot derive (handoff while status is
    # open, the risk band) -- replace=True would otherwise wipe it.
    # Kept OUT of ``labels`` on purpose: that list feeds _store_sticky_topics
    # below, and folding these in would absorb them into the topic memory and
    # re-emit them as topics forever.
    final_labels = labels + [
        label
        for label in _preserved_labels(account_id, conversation_id)
        if label not in labels
    ]

    # Always replace with the final set so stale topics drop on high-conf switches.
    assign = _assign_labels(account_id, conversation_id, final_labels, replace=True)
    assign["classified"] = final_labels
    assign["skipped"] = False
    assign["confidence"] = result.confidence
    assign["source"] = result.source
    assign["reasons"] = result.reasons
    assign["tools"] = result.tools

    if assign.get("success"):
        _store_sticky_topics(account_id, conversation_id, labels, acts=result.acts)
        logger.info(
            "[chatwoot-labels-auto] applied %s (acts=%s) to conversation %s:%s "
            "(confidence=%s source=%s tools=%s reasons=%s)",
            labels,
            result.acts,
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
            "[Chatwoot triage] Labels are applied automatically after each turn. "
            "Applied topics: payment-issue, app-help; plus new-user (no completed "
            "gig yet), proof-acceptance/proof-rejection/gig-complete from "
            "store_proof this turn, and handoff-escalation when you call "
            "crwd_handoff (cleared when conversation status is no longer open). "
            "Do not call `chatwoot_labels` `assign_labels` during normal turns; "
            "the end-of-turn hook replaces labels. Do not mention labels to the member."
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
