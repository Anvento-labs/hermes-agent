"""Gig-scope intent classification for Chatwoot coach turns.

Classifies member messages as enrolled-gig, available-gig, or ambiguous using
the current message plus recent user turns. Ambiguous queries default to
enrolled-gig handling with a clarifying follow-up about open gigs.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional, Sequence, Tuple

GigScope = Literal["enrolled", "available", "ambiguous"]

_AVAILABLE_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bactive gigs?\b",
        r"\bopen gigs?\b",
        r"\bavailable (?:gigs?|campaigns?)\b",
        r"\bwhat can i join\b",
        r"\bfind (?:a )?gig",
        r"\bbrowse\b",
        r"\bavailable gig",
        r"\bnear me\b",
        r"\bnew gig",
        r"\bdiscover\b",
        r"\bany gig",
        r"\bhow do i apply\b",
        r"\bgigs? to join\b",
        r"\bgigs? i can join\b",
    )
)

_ENROLLED_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bnext steps?\b",
        r"\bwhat should i do\b",
        r"\bwhat(?:'s| is) my status\b",
        r"\bmy gigs?\b",
        r"\bcurrent gigs?\b",
        r"\bmy joined gigs?\b",
        r"\bgigs? i(?:'ve| have) joined\b",
        r"\bgigs? i am (?:in|on)\b",
        r"\bwaitlist(?:ed)? gigs?\b",
        r"\bpending approval\b",
        r"\b(?:my )?receipt\b",
        r"\b(?:my )?proof\b",
        r"\b(?:my )?review\b",
        r"\b(?:my )?payout\b",
        r"\b(?:my )?payment\b",
        r"\bhow(?:'s| is) .+ going\b",
        r"\bgig details\b",
        r"\btell me about .+ gig\b",
        r"\bwhere am i\b",
        r"\bwhat(?:'s| is) left\b",
        r"\bmy joined gigs?\b",
        r"\b(?:my )?active gig\b",
        r"\bnext step for\b",
        r"\bstuck on\b",
        r"\bcomplete (?:the )?gig\b",
        r"\bhow (?:do|to) (?:i )?(?:complete|do)\b",
        r"\b(what now|help me|what do i do|i'm stuck|im stuck)\b",
    )
)

_AMBIGUOUS_GIG_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bshow me gigs?\b",
        r"\bwhat are (?:the )?.+ gigs?\b",
        r"\bwhat gigs?\b",
        r"\bwhich gigs?\b",
        r"\b(?:target|walmart|amazon|costco|kroger|cvs|walgreens).{0,40}gigs?\b",
        r"\bgigs?.{0,40}(?:target|walmart|amazon|costco|kroger|cvs|walgreens)\b",
        r"\bstore gigs?\b",
        r"\b\w+ gigs?\b",
    )
)

_GIG_RELATED_RE = re.compile(
    r"\b(?:gigs?|campaigns?|crwd)\b",
    re.IGNORECASE,
)

_STATUS_ONLY_RE = re.compile(
    r"^\s*status\s*\??\s*$",
    re.IGNORECASE,
)


def stitch_user_text(
    user_message: str,
    conversation_history: Optional[Sequence[Any]] = None,
) -> str:
    """Lowercase text from the latest user message plus up to 3 prior user turns."""
    parts: list[str] = []
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


def _matches_any(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def is_gig_related(text: str) -> bool:
    """Return True when stitched text likely refers to gigs."""
    if not text or not text.strip():
        return False
    if _GIG_RELATED_RE.search(text):
        return True
    return _matches_any(text, _ENROLLED_PATTERNS + _AVAILABLE_PATTERNS + _AMBIGUOUS_GIG_PATTERNS)


def extract_gig_query_hint(user_message: str) -> str:
    """Best-effort store/topic/gig-name hint from the member message."""
    text = (user_message or "").strip()
    if not text:
        return ""

    for prefix in (
        "next steps for ",
        "status for ",
        "tell me about ",
        "how is ",
        "how's ",
        "details about ",
        "give me details about ",
    ):
        if text.lower().startswith(prefix):
            name = text[len(prefix):].strip(" ?.")
            if name.lower().endswith(" gig"):
                name = name[:-4].strip()
            if name.lower().endswith(" gigs"):
                name = name[:-5].strip()
            if name.lower() not in {"you", "yourself", "u", "me", "my gig", "my gigs"}:
                return name

    quoted = re.search(r'"([^"]+)"', text)
    if quoted:
        return quoted.group(1).strip()

    what_are = re.search(r"\bwhat are (?:the )?(.+?) gigs?\b", text, re.IGNORECASE)
    if what_are:
        return what_are.group(1).strip()

    about = re.search(r"\b(?:about|for) (?:the )?(.+?) gig\b", text, re.IGNORECASE)
    if about:
        return about.group(1).strip()

    store_gig = re.search(
        r"\b((?:target|walmart|amazon|costco|kroger|cvs|walgreens)(?:\s+store)?)\s+gigs?\b",
        text,
        re.IGNORECASE,
    )
    if store_gig:
        return store_gig.group(1).strip()

    return ""


def classify_gig_scope(
    user_message: str,
    conversation_history: Optional[Sequence[Any]] = None,
) -> Optional[GigScope]:
    """Classify gig scope from message + recent user history, or None if not gig-related."""
    text = stitch_user_text(user_message, conversation_history)
    if not is_gig_related(text):
        return None

    if _matches_any(text, _AVAILABLE_PATTERNS):
        return "available"
    if _matches_any(text, _ENROLLED_PATTERNS):
        return "enrolled"

    msg = (user_message or "").strip().lower()
    if _STATUS_ONLY_RE.match(msg) and not conversation_history:
        return "enrolled"

    if _matches_any(text, _AMBIGUOUS_GIG_PATTERNS):
        return "ambiguous"

    if _GIG_RELATED_RE.search(text):
        return "ambiguous"

    return None


def ambiguity_guidance_block(query_hint: str = "") -> str:
    """Instruction block appended when scope is ambiguous."""
    hint = (query_hint or "").strip()
    topic = f" ({hint})" if hint else ""
    return "\n".join([
        "[Gig intent guidance]",
        "The member's question could refer to gigs they are already enrolled in "
        "OR open/available gigs they have not joined yet.",
        f"Default assumption: enrolled gigs{topic}. Answer from [CRWD gig context] first "
        "(filter by store/topic in the message when relevant).",
        "Then ask exactly one short clarifying question: were they looking for "
        f"open/available gigs{topic} they have not joined yet?",
        "Do not call list_active_gigs or list available gigs in this turn — wait for confirmation.",
    ])
