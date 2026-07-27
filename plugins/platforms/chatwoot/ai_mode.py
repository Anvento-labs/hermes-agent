"""Pre-LLM short-circuit when contact ``ai_mode`` is not explicitly true.

Chatwoot contact custom attribute ``ai_mode`` (checkbox: "allow AI to
conversate") is an opt-in gate. The adapter calls :func:`maybe_short_circuit`
before spawning the agent turn: if the value is not explicitly true, the LLM
turn is skipped silently (no reply, no enrichment).

Missing attributes, falsey values, and unreadable payloads all count as
"not enabled". Gate failures fail closed (skip LLM) so an opt-in flag can
never accidentally spend tokens.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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


async def maybe_short_circuit(adapter: Any, event: Any) -> bool:
    """Return True when the turn should be skipped (``ai_mode`` not true).

    True means: caller must skip enrichment and the agent turn. False means:
    proceed with the normal pipeline. ``adapter`` is unused but kept so the
    call site matches :func:`unregistered.maybe_short_circuit`.
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
