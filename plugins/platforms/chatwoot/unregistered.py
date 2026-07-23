"""Pre-LLM short-circuit for contacts with no CRWD account.

When an inbound Chatwoot message comes from a contact whose email/phone has no
match in the CRWD Mongo ``users`` collection, there is nothing the agent can do
for them — every account tool would be blocked by user_scope and the model ends
up improvising "temporary glitch" replies. Instead, the adapter calls
:func:`maybe_short_circuit` before spawning the agent turn: a confirmed
unregistered contact gets an ``unregistered-user`` conversation label plus a
hardcoded signup reply (rate-limited per conversation), and the LLM turn is
skipped entirely.

Safety rule: only a *clean* Mongo miss triggers the short-circuit. A Mongo
error/timeout falls through to the normal pipeline so an outage can never tell
registered members to sign up again.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import OrderedDict
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

UNREGISTERED_LABEL = "unregistered-user"

# Registration-status cache: contact_id -> (registered, monotonic_ts).
# Negative entries expire fast so a user who signs up mid-conversation gets
# normal service within a minute; positive entries just spare Mongo on bursts.
_STATUS_TTL_REGISTERED_S = 600.0
_STATUS_TTL_UNREGISTERED_S = 60.0
_STATUS_MAX = 2048
_status_cache: "OrderedDict[str, Tuple[bool, float]]" = OrderedDict()

# Canned-reply cooldown: chat_id -> monotonic ts of last signup reply.
_COOLDOWN_S = 600.0
_COOLDOWN_MAX = 2048
_last_replied: "OrderedDict[str, float]" = OrderedDict()


def signup_url() -> str:
    """Signup URL: ``CRWD_SIGNUP_URL`` env, else ``{CRWD_APP_BASE_URL}/signup``."""
    explicit = os.getenv("CRWD_SIGNUP_URL", "").strip()
    if explicit:
        return explicit
    from tools.crwd_urls import crwd_app_base_url

    base = crwd_app_base_url()
    return f"{base}/signup" if base else ""


def _canned_reply() -> str:
    url = signup_url()
    where = f" here: {url}" if url else " in the CRWD app"
    return (
        "Hey! This is the CRWD Coach \U0001f44b It looks like there's no CRWD "
        f"account linked to this number yet. To get started with gigs and "
        f"payouts, sign up{where}\n\n"
        "Once you've created your account, message me back and I'll take it "
        "from there!"
    )


# --- Caches -----------------------------------------------------------------

def _status_get(contact_id: str) -> Optional[bool]:
    entry = _status_cache.get(contact_id)
    if entry is None:
        return None
    registered, ts = entry
    ttl = _STATUS_TTL_REGISTERED_S if registered else _STATUS_TTL_UNREGISTERED_S
    if (time.monotonic() - ts) > ttl:
        _status_cache.pop(contact_id, None)
        return None
    _status_cache.move_to_end(contact_id)
    return registered


def _status_put(contact_id: str, registered: bool) -> None:
    _status_cache[contact_id] = (registered, time.monotonic())
    _status_cache.move_to_end(contact_id)
    while len(_status_cache) > _STATUS_MAX:
        _status_cache.popitem(last=False)


def _in_cooldown(chat_id: str) -> bool:
    ts = _last_replied.get(chat_id)
    if ts is None:
        return False
    if (time.monotonic() - ts) > _COOLDOWN_S:
        _last_replied.pop(chat_id, None)
        return False
    return True


def _mark_replied(chat_id: str) -> None:
    _last_replied[chat_id] = time.monotonic()
    _last_replied.move_to_end(chat_id)
    while len(_last_replied) > _COOLDOWN_MAX:
        _last_replied.popitem(last=False)


def _reset_caches() -> None:
    """Test helper."""
    _status_cache.clear()
    _last_replied.clear()


# --- Main entry -------------------------------------------------------------

async def maybe_short_circuit(adapter: Any, event: Any) -> bool:
    """Return True when the turn was fully handled (contact not registered).

    True means: canned signup reply sent (unless within the per-conversation
    cooldown) and the ``unregistered-user`` label applied — the caller must
    skip the agent turn. False means: proceed with the normal pipeline.
    """
    try:
        return await _maybe_short_circuit(adapter, event)
    except Exception:
        # Never let this gate break the webhook path.
        logger.warning("[crwd-unregistered] check failed; falling through", exc_info=True)
        return False


async def _maybe_short_circuit(adapter: Any, event: Any) -> bool:
    from plugins.platforms.chatwoot import enrichment

    if not enrichment._enabled():
        return False

    ctx = enrichment._parse_event(event)
    if ctx is None:
        return False

    # Webhook hint wins: the contact already carries a CRWD user id.
    raw = getattr(event, "raw_message", None)
    sender = raw.get("sender") if isinstance(raw, dict) else None
    if isinstance(sender, dict):
        attrs = sender.get("custom_attributes")
        if isinstance(attrs, dict) and str(attrs.get("joincrwd_user_id") or "").strip():
            return False

    email, phone = ctx["email"], ctx["phone"]
    if not email and not phone:
        return False

    contact_id = str(ctx["contact_id"])
    registered = _status_get(contact_id)
    if registered is None:
        try:
            user = await asyncio.to_thread(enrichment.fetch_user, email, phone)
        except Exception as exc:
            # Mongo error ≠ unregistered — fall through to the normal flow.
            logger.warning("[crwd-unregistered] mongo lookup failed for contact=%s: %s",
                           contact_id, exc)
            return False
        registered = user is not None
        _status_put(contact_id, registered)

    if registered:
        return False

    chat_id = str(getattr(getattr(event, "source", None), "chat_id", "") or "")
    conversation_id = ctx.get("conversation_id")

    if conversation_id:
        try:
            from plugins.platforms.chatwoot import labels_tool

            result = await asyncio.to_thread(
                labels_tool._assign_labels,
                str(ctx["account_id"]),
                str(conversation_id),
                [UNREGISTERED_LABEL],
                False,
            )
            if not result.get("success"):
                logger.warning("[crwd-unregistered] label assign failed: %s",
                               result.get("error"))
        except Exception:
            logger.warning("[crwd-unregistered] label assign errored", exc_info=True)

    if chat_id and not _in_cooldown(chat_id):
        try:
            await adapter.send(chat_id, _canned_reply())
            _mark_replied(chat_id)
            logger.info("[crwd-unregistered] signup reply sent for contact=%s chat=%s",
                        contact_id, chat_id)
        except Exception:
            logger.warning("[crwd-unregistered] signup reply failed", exc_info=True)
    else:
        logger.info("[crwd-unregistered] skipping turn for contact=%s (cooldown)",
                    contact_id)

    return True
