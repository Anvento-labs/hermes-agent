"""Automatic Chatwoot conversation labeling on every turn.

The ``chatwoot-conversation-labels`` skill instructs the agent to call
``chatwoot_labels``, but models often skip optional triage steps. This module
applies labels deterministically via ``post_llm_call`` so inbox tags appear even
when the agent never invokes the tool.

Classification is keyword/heuristic-based (fast, no extra LLM call). The skill
remains the source of truth for nuanced multi-label rules; this hook covers the
common CRWD Coach intents.

``handoff-escalation`` is applied only when the agent calls ``crwd_handoff`` in
the current turn (tracked via ``post_tool_call``), not from member message text.
"""

from __future__ import annotations

import json
import logging
import os
import re
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from plugins.platforms.chatwoot.labels import PREDEFINED_LABEL_TITLES
from plugins.platforms.chatwoot.labels_tool import (
    _assign_labels,
    _create_labels_if_not_exists,
    _resolve_conversation,
    check_chatwoot_labels_requirements,
)

logger = logging.getLogger(__name__)

_MAX_LABELS = 2

_handoff_this_turn: ContextVar[bool] = ContextVar("chatwoot_handoff_this_turn", default=False)
_contact_id_this_turn: ContextVar[str] = ContextVar("chatwoot_contact_id_this_turn", default="")

_CRWD_ANCHOR_RE = re.compile(
    r"\b(crwd|gig|gigs|payout|proof|campaign|dot)\b|"
    r"\bin the app\b|\bpayment\b",
    re.IGNORECASE,
)

# Topic rules in priority order (handoff-escalation is action-based, not here).
_LABEL_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "payment-payout",
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
        (
            r"\bnot eligible\b",
            r"\bineligible\b",
            r"\bcan'?t join\b",
            r"\bdon'?t qualify\b",
            r"\btoo young\b",
            r"\bwrong state\b",
            r"\bage requirement\b",
            r"\bmy account\b",
            r"\bmembership\b",
            r"\baccount status\b",
            r"\bdeactivat",
            r"\bban(?:ned|s)?\b",
            r"\bsuspend",
            r"\bstop messaging\b",
            r"\bstop texting\b",
            r"\bunsubscribe\b",
            r"\bopt out\b",
            r"\bdon'?t text\b",
            r"\bremove me\b",
            r"\bstop contacting\b",
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
        (
            r"\bwhere (?:is|do i find)\b",
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

_COMPILED_RULES: Tuple[Tuple[str, Tuple[re.Pattern[str], ...]], ...] = tuple(
    (label, tuple(re.compile(p, re.IGNORECASE) for p in patterns))
    for label, patterns in _LABEL_RULES
)


def _is_chatwoot(platform: Any) -> bool:
    return str(platform or "").strip().lower() == "chatwoot"


def reset_handoff_flag() -> None:
    """Clear the per-turn handoff flag (call at turn start)."""
    _handoff_this_turn.set(False)


def reset_contact_id() -> None:
    """Clear the cached Chatwoot contact id for the current turn."""
    _contact_id_this_turn.set("")


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


def handoff_tool_hook(**kwargs: Any) -> None:
    """``post_tool_call`` — mark handoff when the agent calls ``crwd_handoff``."""
    if str(kwargs.get("tool_name") or "").strip() == "crwd_handoff":
        _handoff_this_turn.set(True)


def _text_for_classification(user_message: str, conversation_history: Sequence[Any]) -> str:
    """Build lowercase text from the latest user message plus recent user turns."""
    parts: List[str] = []
    if user_message and user_message.strip():
        parts.append(user_message.strip())
    if conversation_history:
        user_turns = 0
        for msg in reversed(conversation_history):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
            user_turns += 1
            if user_turns >= 3:
                break
    return " ".join(parts).lower()


def _has_crwd_anchor(text: str) -> bool:
    return bool(_CRWD_ANCHOR_RE.search(text))


def _matches_any(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def _extract_gig_name(message: str) -> str:
    """Best-effort gig name from the member message (mirrors gig_context)."""
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
    about = re.search(r"\b(?:about|for) (?:the )?(.+?) gig\b", text, re.IGNORECASE)
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


def _member_has_active_gigs(contact_id: str) -> Tuple[bool, Set[str]]:
    """Return (has_memberships, enrolled_gig_names). Best-effort; False on failure."""
    contact_id = str(contact_id or "").strip()
    if not contact_id or not os.getenv("CRWD_MONGO_URI"):
        return False, set()
    try:
        from plugins.platforms.chatwoot.coach_context import resolve_member_crwd_id
        from tools.crwd_db_tool import build_user_gig_status

        user_id = resolve_member_crwd_id(contact_id)
        if not user_id:
            return False, set()
        payload = build_user_gig_status(user_id, limit=10)
        items = payload.get("items") or []
        names = {
            str(row.get("gig_name")).strip()
            for row in items
            if row.get("gig_name")
        }
        return bool(items), names
    except Exception as exc:
        logger.debug("[chatwoot-labels-auto] membership lookup failed: %s", exc)
        return False, set()


def _handoff_in_current_turn(
    conversation_history: Sequence[Any],
    user_message: str,
) -> bool:
    """Scan the current turn for a ``crwd_handoff`` tool call (fallback path)."""
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


def _apply_gig_active_label(
    text: str,
    user_message: str,
    contact_id: str,
    matched: List[str],
) -> None:
    """Add ``gig-active`` or ``gig-discovery`` based on proof vs mid-gig intent."""
    if "gig-active" in matched or "gig-discovery" in matched:
        return

    if _matches_any(text, _PROOF_PATTERNS):
        matched.append("gig-active")
        return

    if not _matches_any(text, _MID_GIG_PATTERNS):
        return

    enrolled, gig_names = _member_has_active_gigs(contact_id)
    if not enrolled:
        matched.append("gig-discovery")
        return

    gig_name = _extract_gig_name(user_message)
    if _gig_name_in_enrolled(gig_name, gig_names):
        matched.append("gig-active")
    else:
        matched.append("gig-discovery")


def _finalize_labels(topics: List[str], handoff: bool) -> List[str]:
    deduped: List[str] = []
    for label in topics:
        if label not in PREDEFINED_LABEL_TITLES or label in deduped:
            continue
        deduped.append(label)
        if handoff:
            break
        if len(deduped) >= _MAX_LABELS:
            break
    if handoff and "handoff-escalation" not in deduped:
        if deduped:
            return [deduped[0], "handoff-escalation"]
        return ["handoff-escalation"]
    return deduped[:_MAX_LABELS]


def classify_conversation_labels(
    user_message: str = "",
    conversation_history: Optional[Sequence[Any]] = None,
    contact_id: str = "",
    handoff_requested: bool = False,
) -> List[str]:
    """Return up to 2 predefined label titles for the conversation text."""
    history = conversation_history or ()
    text = _text_for_classification(user_message, history)
    if not text.strip():
        fallback = ["off-topic"] if not handoff_requested else ["off-topic", "handoff-escalation"]
        return _finalize_labels(fallback, handoff_requested)

    matched: List[str] = []

    for label, patterns in _COMPILED_RULES:
        if any(p.search(text) for p in patterns):
            matched.append(label)
        if len(matched) >= _MAX_LABELS and not handoff_requested:
            break

    _apply_gig_active_label(text, user_message, contact_id, matched)

    if not matched and re.search(r"\bgig", text, re.IGNORECASE):
        matched.append("gig-discovery")

    if not matched:
        if _has_crwd_anchor(text):
            matched.append("gig-discovery")
        else:
            matched.append("off-topic")

    return _finalize_labels(matched, handoff_requested)


def auto_label_conversation(
    user_message: str = "",
    conversation_history: Optional[Sequence[Any]] = None,
    contact_id: str = "",
    handoff_requested: bool = False,
) -> Dict[str, Any]:
    """Bootstrap labels and assign classified tags to the current conversation."""
    if not check_chatwoot_labels_requirements():
        return {"success": False, "skipped": True, "reason": "chatwoot not configured"}

    account_id, conversation_id = _resolve_conversation()
    if not account_id or not conversation_id:
        return {"success": False, "skipped": True, "reason": "no chatwoot conversation"}

    labels = classify_conversation_labels(
        user_message,
        conversation_history,
        contact_id=contact_id,
        handoff_requested=handoff_requested,
    )
    bootstrap = _create_labels_if_not_exists(account_id)
    if not bootstrap.get("success") and not bootstrap.get("existing"):
        return {
            "success": False,
            "skipped": False,
            "labels": labels,
            "error": bootstrap.get("error"),
        }

    result = _assign_labels(account_id, conversation_id, labels, replace=True)
    result["classified"] = labels
    result["skipped"] = False
    if not result.get("success"):
        logger.warning(
            "[chatwoot-labels-auto] assign failed for %s:%s — %s",
            account_id,
            conversation_id,
            result.get("error"),
        )
    else:
        logger.info(
            "[chatwoot-labels-auto] applied %s to conversation %s:%s",
            labels,
            account_id,
            conversation_id,
        )
    return result


def labeling_reminder_hook(**kwargs: Any) -> Optional[Dict[str, str]]:
    """``pre_llm_call`` — reset handoff flag and remind about auto-labeling."""
    reset_handoff_flag()
    reset_contact_id()
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
            "each turn. `handoff-escalation` is added only when you call "
            "`crwd_handoff`. You may override via `chatwoot_labels` `assign_labels`. "
            "Do not mention labels to the member."
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
        )
    except Exception as exc:
        logger.warning("[chatwoot-labels-auto] hook failed: %s", exc)
    finally:
        reset_handoff_flag()
        reset_contact_id()
