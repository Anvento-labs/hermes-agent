"""Dispatch a Hermes Coach turn after CRWD lead ingest.

Does not POST a fake Chatwoot inbound message. Builds the same
``MessageEvent`` shape as the webhook path (``chat_id`` =
``account:conversation``, ``user_id`` = contact id, ``chat_type`` =
``direct``) and runs ``BasePlatformAdapter.handle_message`` so the
gateway agent replies via ``adapter.send()`` (outgoing only).

Skips ChatwootAdapter.handle_message so ``ai_mode`` / unregistered gates
do not block a trusted lead POST.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Mapping, Optional

from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType
from plugins.platforms.chatwoot.conversations import LEADS_INBOX_CHANNEL

logger = logging.getLogger(__name__)

LEAD_SKILL = "crwd-lead-intro"
_HANDOFF_STATUS = "open"


def _truthy_status(value: Any) -> str:
    return str(value or "").strip().lower()


def coach_turn_should_start(adapter: Any, ctx: Mapping[str, Any]) -> Optional[str]:
    """Return a skip reason, or None if the turn should be scheduled."""
    if not getattr(adapter, "_message_handler", None):
        return "no message handler"
    if _truthy_status(ctx.get("conversation_status")) == _HANDOFF_STATUS:
        return "conversation status is open (human handoff)"
    if not str(ctx.get("chat_id") or "").strip():
        return "missing chat_id"
    if not str(ctx.get("contact_id") or "").strip():
        return "missing contact_id"
    if not (
        ctx.get("created")
        or ctx.get("interest_created")
        or ctx.get("conversation_created")
    ):
        return "repeat lead ingest (no new user, interest, or conversation)"
    return None


def _facts_block(ctx: Mapping[str, Any]) -> str:
    user_created = bool(ctx.get("created"))
    conv_created = bool(ctx.get("conversation_created"))
    return (
        "[Lead ingest facts — the member did not type this. Do not quote this block.]\n"
        f"crwd_user_id: {ctx.get('user_id') or ''}\n"
        f"user_created: {str(user_created).lower()}\n"
        f"gig_id: {ctx.get('gig_id') or ''}\n"
        f"business_owner_id: {ctx.get('business_owner_id') or ''}\n"
        f"conversation_id: {ctx.get('conversation_id') or ''}\n"
        f"conversation_created: {str(conv_created).lower()}\n"
        f"contact_id: {ctx.get('contact_id') or ''}\n"
        "Compose one Coach-voiced reply for this thread using the skill above."
    )


def _build_prompt(ctx: Mapping[str, Any]) -> str:
    skill_text = ""
    try:
        from agent.skill_commands import _build_skill_message, _load_skill_payload

        loaded = _load_skill_payload(LEAD_SKILL)
        if loaded:
            payload, skill_dir, display = loaded
            skill_text = _build_skill_message(
                payload,
                skill_dir,
                f"The {display} skill is active for this lead intro turn.",
            )
    except Exception:
        logger.debug("[chatwoot-leads] failed to load %s", LEAD_SKILL, exc_info=True)
    if not skill_text:
        skill_text = (
            "You are the CRWD Coach. Greet this lead using real gig data from "
            "`crwd_db`. Do not invent enrollment or payout."
        )
    return f"{skill_text}\n\n{_facts_block(ctx)}"


def _lead_event(adapter: Any, ctx: Mapping[str, Any]) -> MessageEvent:
    chat_id = str(ctx.get("chat_id") or "").strip()
    contact_id = str(ctx.get("contact_id") or "").strip()
    conv_id = str(ctx.get("conversation_id") or "").strip()
    status = _truthy_status(ctx.get("conversation_status")) or "pending"
    name = str(ctx.get("full_name") or "").strip() or f"conversation {conv_id}"
    crwd_id = str(ctx.get("user_id") or "").strip()
    # The real inbox ensure_conversation resolved -- API and SMS leads render
    # differently downstream (SMS strips markdown + caps length at send time,
    # per ChatwootAdapter._is_sms_conversation), so this must be the actual
    # resolved channel, never assumed. Falls back only if ctx is missing it.
    channel_type = str(ctx.get("channel_type") or LEADS_INBOX_CHANNEL).strip()
    inbox_name = str(ctx.get("inbox_name") or "API").strip()
    source = adapter.build_source(
        chat_id=chat_id,
        chat_name=name,
        chat_type="direct",
        user_id=contact_id,
        user_name=name if ctx.get("full_name") else None,
        chat_topic=f"inbox: {inbox_name} ({channel_type})",
    )
    raw = {
        "event": "lead_ingest",
        "conversation": {"id": conv_id, "status": status, "channel": channel_type},
        "sender": {
            "id": contact_id,
            "custom_attributes": {"joincrwd_user_id": crwd_id} if crwd_id else {},
        },
        "inbox": {"id": ctx.get("inbox_id"), "channel_type": channel_type},
    }
    return MessageEvent(
        text=_build_prompt(ctx),
        message_type=MessageType.TEXT,
        source=source,
        raw_message=raw,
        message_id=f"lead:{ctx.get('membership_id') or conv_id}",
        internal=True,
    )


def schedule_coach_turn(adapter: Any, ctx: Mapping[str, Any]) -> bool:
    """Queue ``dispatch_lead_turn`` on the adapter. Returns whether it was scheduled."""
    reason = coach_turn_should_start(adapter, ctx)
    if reason:
        logger.info("[chatwoot-leads] coach turn not started: %s", reason)
        return False
    task = asyncio.create_task(dispatch_lead_turn(adapter, dict(ctx)))
    adapter._background_tasks.add(task)
    task.add_done_callback(adapter._background_tasks.discard)
    return True


async def dispatch_lead_turn(adapter: Any, ctx: Dict[str, Any]) -> None:
    """Run the gateway agent on this Chatwoot session (outgoing reply only)."""
    reason = coach_turn_should_start(adapter, ctx)
    if reason:
        logger.info("[chatwoot-leads] coach turn skipped: %s", reason)
        return
    chat_id = str(ctx.get("chat_id") or "").strip()
    remember = getattr(adapter, "_remember_channel", None)
    if callable(remember):
        # Must be the real resolved channel (see _lead_event) -- this is what
        # send()-time formatting checks for the rest of the thread, not just
        # this first reply.
        remember(chat_id, str(ctx.get("channel_type") or LEADS_INBOX_CHANNEL).strip())
    try:
        from plugins.platforms.chatwoot import coach_context

        coach_context.bind_webhook_crwd_hint(str(ctx.get("user_id") or "").strip() or None)
        coach_context.bind_webhook_conversation_status(
            _truthy_status(ctx.get("conversation_status")) or "pending"
        )
    except Exception:
        logger.debug("[chatwoot-leads] coach_context bind failed", exc_info=True)
    event = _lead_event(adapter, ctx)
    try:
        await BasePlatformAdapter.handle_message(adapter, event)
    except Exception:
        logger.exception(
            "[chatwoot-leads] coach turn failed chat_id=%s",
            chat_id,
        )
