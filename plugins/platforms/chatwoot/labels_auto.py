"""Minimal Chatwoot labeling context for the primary agent.

The eight titles owned by ``chatwoot-conversation-labels`` are added and
removed by the agent via ``chatwoot_labels`` (add/remove). This module no
longer classifies or writes labels. It only injects conversation status so
the agent can drop ``handoff-escalation`` when status is no longer ``open``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from plugins.platforms.chatwoot.labels_tool import check_chatwoot_labels_requirements

logger = logging.getLogger(__name__)


def _is_chatwoot(platform: Any) -> bool:
    return str(platform or "").strip().lower() == "chatwoot"


def _conversation_status() -> Optional[str]:
    """Return this turn's Chatwoot conversation status, or None.

    Prefers the webhook-bound value (same source as ``ai_mode`` / coach
    context). Missing/unreadable status is not treated as handoff.
    """
    try:
        from plugins.platforms.chatwoot.coach_context import webhook_conversation_status

        status = webhook_conversation_status()
    except Exception:
        logger.debug("[chatwoot-labels] conversation status lookup failed", exc_info=True)
        return None
    text = str(status or "").strip().lower()
    return text or None


def chatwoot_label_context_hook(**kwargs: Any) -> Optional[Dict[str, str]]:
    """``pre_llm_call`` — tell the agent the current conversation status."""
    if not _is_chatwoot(kwargs.get("platform")):
        return None
    if not check_chatwoot_labels_requirements():
        return None
    status = _conversation_status()
    return {
        "context": (
            f"[Chatwoot] Conversation status: {status or 'unknown'}. "
            "You manage conversation labels yourself per the "
            "chatwoot-conversation-labels skill — add/remove via "
            "`chatwoot_labels`, never replace the full set."
        ),
    }


# Backward-compatible alias used by older tests / imports.
labeling_reminder_hook = chatwoot_label_context_hook
