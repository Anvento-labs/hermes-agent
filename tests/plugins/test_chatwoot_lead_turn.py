"""Tests for lead Coach dispatch (no live LLM / Chatwoot)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from plugins.platforms.chatwoot import adapter as cw
from plugins.platforms.chatwoot import lead_turn as lt
from plugins.platforms.chatwoot.coach_context import (
    reset_webhook_conversation_status,
    reset_webhook_crwd_hint,
    webhook_conversation_status,
    _webhook_crwd_hint_value,
)


def _adapter(**extra) -> cw.ChatwootAdapter:
    base = {"base_url": "https://cw.example.com", "account_id": "1"}
    base.update(extra)
    a = cw.ChatwootAdapter(PlatformConfig(enabled=True, token="bot-tok", extra=base))
    a._running = True
    return a


def _ctx(**overrides):
    data = {
        "user_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
        "created": True,
        "gig_id": "69b8614f1083b9302fd0a9a7",
        "business_owner_id": "69a6f191cb29b0b371b3a14f",
        "account_id": "1",
        "conversation_id": "42",
        "chat_id": "1:42",
        "inbox_id": "5",
        "contact_id": "10",
        "conversation_created": True,
        "conversation_status": "pending",
        "membership_id": "bbbbbbbbbbbbbbbbbbbbbbbb",
    }
    data.update(overrides)
    return data


class TestCoachTurnShouldStart:
    def test_skip_without_handler(self):
        a = _adapter()
        assert lt.coach_turn_should_start(a, _ctx()) == "no message handler"

    def test_skip_open_status(self):
        a = _adapter()
        a._message_handler = object()
        reason = lt.coach_turn_should_start(a, _ctx(conversation_status="open"))
        assert reason is not None
        assert "open" in reason

    def test_ok_when_pending_and_handler(self):
        a = _adapter()
        a._message_handler = object()
        assert lt.coach_turn_should_start(a, _ctx()) is None

    def test_skip_repeat_ingest_nothing_new(self):
        a = _adapter()
        a._message_handler = object()
        reason = lt.coach_turn_should_start(
            a,
            _ctx(created=False, conversation_created=False, interest_created=False),
        )
        assert reason is not None
        assert "repeat" in reason

    def test_ok_when_only_interest_created(self):
        a = _adapter()
        a._message_handler = object()
        reason = lt.coach_turn_should_start(
            a,
            _ctx(created=False, conversation_created=False, interest_created=True),
        )
        assert reason is None


class TestDispatchLeadTurn:
    @pytest.mark.asyncio
    async def test_builds_direct_session_and_calls_base_handle_message(self):
        a = _adapter()
        a._message_handler = AsyncMock(return_value=None)
        captured = {}

        async def fake_handle(self, event):
            captured["event"] = event
            captured["self"] = self

        reset_webhook_crwd_hint()
        reset_webhook_conversation_status()
        with patch.object(BasePlatformAdapter, "handle_message", fake_handle):
            with patch.object(cw.ChatwootAdapter, "handle_message", new=AsyncMock()) as cw_handle:
                await lt.dispatch_lead_turn(a, _ctx())
        assert cw_handle.await_count == 0
        event = captured["event"]
        assert captured["self"] is a
        assert event.source.chat_id == "1:42"
        assert event.source.user_id == "10"
        assert event.source.chat_type == "direct"
        assert event.internal is True
        assert "crwd_user_id:" in event.text
        assert _webhook_crwd_hint_value() == "aaaaaaaaaaaaaaaaaaaaaaaa"
        assert webhook_conversation_status() == "pending"
        assert a._conv_channel.get("1:42") == "Channel::Api"
        reset_webhook_crwd_hint()
        reset_webhook_conversation_status()

    @pytest.mark.asyncio
    async def test_does_not_call_ai_mode(self):
        a = _adapter()
        a._message_handler = object()

        async def fake_handle(self, event):
            return None

        with patch("plugins.platforms.chatwoot.ai_mode.maybe_short_circuit") as ai:
            with patch.object(BasePlatformAdapter, "handle_message", fake_handle):
                await lt.dispatch_lead_turn(a, _ctx())
        ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_open_does_not_call_base(self):
        a = _adapter()
        a._message_handler = object()
        with patch.object(BasePlatformAdapter, "handle_message", new=AsyncMock()) as base:
            await lt.dispatch_lead_turn(a, _ctx(conversation_status="open"))
        base.assert_not_awaited()

    def test_schedule_false_without_handler(self):
        a = _adapter()
        assert lt.schedule_coach_turn(a, _ctx()) is False

    def test_schedule_true_with_handler(self):
        a = _adapter()
        a._message_handler = object()
        with patch("plugins.platforms.chatwoot.lead_turn.asyncio.create_task") as create:
            dummy = type("T", (), {"add_done_callback": lambda *a, **k: None})()
            create.return_value = dummy
            assert lt.schedule_coach_turn(a, _ctx()) is True
            create.assert_called_once()
            coro = create.call_args[0][0]
            if hasattr(coro, "close"):
                coro.close()
