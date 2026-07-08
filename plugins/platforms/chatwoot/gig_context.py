"""Prefetch personalized gig progress into Chatwoot turns.

On gig-related member messages, resolves the authenticated CRWD user id and
injects a compact ``[CRWD gig context]`` block with per-gig ``stage`` and
``next_step`` so the coach answers from real membership/progress data.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional, Sequence

from plugins.platforms.chatwoot.coach_context import (
    cross_user_request_active,
    resolve_member_crwd_id,
)
from plugins.platforms.chatwoot.gig_intent import (
    GigScope,
    ambiguity_guidance_block,
    classify_gig_scope,
    extract_gig_query_hint,
)

logger = logging.getLogger(__name__)

_WAITLIST_PATTERNS = (
    re.compile(r"\bwaitlist(?:ed)?\b", re.I),
    re.compile(r"\bpending approval\b", re.I),
    re.compile(r"\bwaiting (?:for )?approval\b", re.I),
)

_AMBIGUOUS_FALLBACK = re.compile(
    r"\b(what now|help me|what do i do|i'm stuck|im stuck|status)\b",
    re.I,
)


def _is_chatwoot(platform: Any) -> bool:
    if str(platform or "").strip().lower() == "chatwoot":
        return True
    try:
        from gateway.session_context import get_session_env

        return (get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower() == "chatwoot"
    except Exception:
        return False


def _matches(message: str, patterns) -> bool:
    return any(p.search(message) for p in patterns)


def should_prefetch_gig_context(
    user_message: str,
    conversation_history: Optional[Sequence[Any]] = None,
) -> bool:
    """Return True when the inbound message needs enrolled gig progress data."""
    scope = classify_gig_scope(user_message, conversation_history)
    return scope in ("enrolled", "ambiguous")


def build_gig_context_block(
    user_id: str,
    user_message: str = "",
    *,
    limit: int | None = None,
    scope: GigScope = "enrolled",
) -> Optional[str]:
    """Fetch gig status and format the injection block, or None on failure."""
    if not user_id:
        return None
    try:
        from tools.crwd_db_tool import _HARD_LIMIT, build_user_gig_status
    except Exception as exc:
        logger.debug("[crwd-gig-ctx] import failed: %s", exc)
        return None

    row_limit = _HARD_LIMIT if limit is None else limit

    include_waitlisted = _matches(user_message, _WAITLIST_PATTERNS)
    gig_name = extract_gig_query_hint(user_message)
    query_hint = gig_name

    try:
        payload = build_user_gig_status(
            user_id,
            gig_name=gig_name,
            include_waitlisted=include_waitlisted,
            limit=row_limit,
        )
    except Exception as exc:
        logger.debug("[crwd-gig-ctx] build_user_gig_status failed: %s", exc)
        return None

    items = payload.get("items") or []
    if not items and not include_waitlisted and scope == "enrolled":
        if not _AMBIGUOUS_FALLBACK.search(user_message or ""):
            return None
        try:
            payload = build_user_gig_status(user_id, limit=row_limit)
            items = payload.get("items") or []
        except Exception:
            return None
        if not items or len(items) > 3:
            return None

    if not items and scope != "ambiguous":
        return None

    parts: list[str] = []
    if items:
        slim = {
            "active_gigs": [
                {
                    "gig_id": row.get("gig_id"),
                    "gig_name": row.get("gig_name"),
                    "gig_name_plain": row.get("gig_name_plain"),
                    "gig_url": row.get("gig_url"),
                    "gig_type": row.get("gig_type"),
                    "stage": row.get("stage"),
                    "next_step": row.get("next_step"),
                    "buy_link": row.get("buy_link"),
                    "products": row.get("products") or [],
                    "handoff_recommended": row.get("handoff_recommended"),
                }
                for row in items
            ],
            "count": len(items),
        }
        parts.extend([
            "[CRWD gig context]",
            "Source: get_user_gig_status (crwd_staging). Answer from this data; "
            "do not give generic lifecycle steps when a next_step is present.",
            "When naming a gig, paste gig_name verbatim — it is already "
            "[Title](gig_url) markdown so the title is clickable. Do NOT also "
            "append a bare URL after the name.",
            "CRITICAL product links: each gig may have multiple products[]. "
            "When the member asks for product/buy links, list EVERY item in "
            "products[] as markdown [product_name](product_url) — one per line. "
            "Never say there is only one link if products[] has more. "
            "buy_link is only the first product URL (legacy); prefer products[]. "
            "Never use gig_url as a buy/product link. Keep gig-title markdown "
            "and product markdown on separate lines.",
            json.dumps(slim, indent=2, default=str),
        ])

    if scope == "ambiguous":
        parts.append(ambiguity_guidance_block(query_hint))

    if not parts:
        return None

    return "\n".join(parts)


def gig_context_hook(**kwargs: Any) -> Optional[Dict[str, str]]:
    """``pre_llm_call`` hook: inject personalized gig progress when relevant."""
    try:
        if not _is_chatwoot(kwargs.get("platform")):
            return None
        if not os.getenv("CRWD_MONGO_URI"):
            return None
        if cross_user_request_active():
            return None

        user_message = str(kwargs.get("user_message") or "")
        history = kwargs.get("conversation_history")
        scope = classify_gig_scope(user_message, history)
        if scope is None or scope == "available":
            return None

        contact_id = str(kwargs.get("sender_id") or "").strip()
        if not contact_id:
            return None
        user_id = resolve_member_crwd_id(contact_id)
        if not user_id:
            return None

        block = build_gig_context_block(user_id, user_message, scope=scope)
        if not block:
            return None
        return {"context": block}
    except Exception as exc:
        logger.debug("[crwd-gig-ctx] hook failed: %s", exc)
        return None
