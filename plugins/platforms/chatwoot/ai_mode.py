"""Pre-LLM gates for Chatwoot handoff status and contact ``ai_mode``.

Two separate short-circuits (called in this order from the adapter):

1. **Handoff** (:func:`maybe_skip_handoff`) — Chatwoot ``conversation.status ==
   "open"`` means a human owns the thread. Skips canned replies and the LLM.
   Missing/unreadable status is not treated as handoff.
2. **AI opt-in** (:func:`maybe_short_circuit`) — contact custom attribute
   ``ai_mode`` must be explicitly true before spending LLM tokens. Does **not**
   block the unregistered canned signup reply (that runs between these gates).

Gate failures fail closed (skip) so an opt-in flag can never accidentally spend
tokens / talk over a human.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Same convention as labels_auto._HANDOFF_ACTIVE_STATUS / crwd_handoff.
_HANDOFF_ACTIVE_STATUS = "open"


def _is_enabled(value: Any) -> bool:
    """Return True only for boolean True or case-insensitive string ``"true"``."""
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() == "true":
        return True
    return False


def _ai_mode_value(event: Any) -> Any:
    """Pull ``sender.custom_attributes.ai_mode`` from the webhook event, or None."""
    raw = getattr(event, "raw_message", None)
    if not isinstance(raw, dict):
        return None
    sender = raw.get("sender")
    if not isinstance(sender, dict):
        return None
    attrs = sender.get("custom_attributes")
    if not isinstance(attrs, dict):
        return None
    return attrs.get("ai_mode")


def _conversation_status(event: Any) -> Optional[str]:
    """Pull ``conversation.status`` from the webhook event, or None if absent/bad."""
    raw = getattr(event, "raw_message", None)
    if not isinstance(raw, dict):
        return None
    conversation = raw.get("conversation")
    if not isinstance(conversation, dict):
        return None
    status = conversation.get("status")
    if status is None:
        return None
    text = str(status).strip().lower()
    return text or None


async def maybe_skip_handoff(adapter: Any, event: Any) -> bool:
    """Return True when the turn should be skipped because status is handoff/open.

    True means: caller must skip canned replies, enrichment, and the agent turn.
    ``adapter`` is unused but kept for call-site parity with other gates.
    """
    try:
        return await _maybe_skip_handoff(adapter, event)
    except Exception:
        logger.warning("[crwd-ai-mode] handoff check failed; skipping turn", exc_info=True)
        return True


async def _maybe_skip_handoff(adapter: Any, event: Any) -> bool:
    del adapter
    status = _conversation_status(event)
    if status == _HANDOFF_ACTIVE_STATUS:
        logger.info("[crwd-ai-mode] skipping turn (conversation status=open)")
        return True
    return False


async def maybe_short_circuit(adapter: Any, event: Any) -> bool:
    """Return True when the LLM turn should be skipped (``ai_mode`` not true).

    True means: caller must skip enrichment and the agent turn. Does not imply
    skipping the unregistered canned reply (run that gate first). ``adapter`` is
    unused but kept so the call site matches :func:`unregistered.maybe_short_circuit`.
    """
    try:
        return await _maybe_short_circuit(adapter, event)
    except Exception:
        logger.warning("[crwd-ai-mode] check failed; skipping turn", exc_info=True)
        return True


async def _maybe_short_circuit(adapter: Any, event: Any) -> bool:
    del adapter  # API parity with unregistered; unused for webhook-only check.
    value = _ai_mode_value(event)
    if _is_enabled(value):
        return False
    logger.info("[crwd-ai-mode] skipping turn (ai_mode=%r)", value)
    return True
