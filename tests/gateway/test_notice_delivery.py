from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import SendResult
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.SLACK,
        chat_id="C123",
        chat_type="channel",
        user_id="U123",
        thread_id="111.222",
    )


def _make_runner(extra=None):
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.SLACK: PlatformConfig(enabled=True, token="***", extra=extra or {})
        }
    )
    adapter = MagicMock()
    adapter.notices_always_private = False
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="public-1"))
    adapter.send_private_notice = AsyncMock(return_value=SendResult(success=True, message_id="private-1"))
    runner.adapters = {Platform.SLACK: adapter}
    return runner, adapter


@pytest.mark.asyncio
async def test_deliver_platform_notice_uses_private_delivery_when_configured():
    runner, adapter = _make_runner(extra={"notice_delivery": "private"})

    await runner._deliver_platform_notice(_make_source(), "hello")

    adapter.send_private_notice.assert_awaited_once_with(
        "C123",
        "U123",
        "hello",
        metadata={"thread_id": "111.222"},
    )
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_platform_notice_falls_back_to_public_when_private_fails():
    runner, adapter = _make_runner(extra={"notice_delivery": "private"})
    adapter.send_private_notice = AsyncMock(return_value=SendResult(success=False, error="nope"))

    await runner._deliver_platform_notice(_make_source(), "hello")

    adapter.send.assert_awaited_once_with("C123", "hello", metadata={"thread_id": "111.222"})


@pytest.mark.asyncio
async def test_deliver_platform_notice_uses_public_delivery_by_default():
    runner, adapter = _make_runner()

    await runner._deliver_platform_notice(_make_source(), "hello")

    adapter.send.assert_awaited_once_with("C123", "hello", metadata={"thread_id": "111.222"})
    adapter.send_private_notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_forces_private_notice_without_config():
    """Adapters that opt in (notices_always_private) route to private notes
    even when no notice_delivery config is set."""
    runner, adapter = _make_runner()
    adapter.notices_always_private = True

    await runner._deliver_platform_notice(_make_source(), "hello")

    adapter.send_private_notice.assert_awaited_once_with(
        "C123",
        "U123",
        "hello",
        metadata={"thread_id": "111.222"},
    )
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_forces_private_notice_even_without_user_id():
    """A forced-private adapter still uses the private path when there is no
    user_id (e.g. Chatwoot private notes are conversation-scoped)."""
    runner, adapter = _make_runner()
    adapter.notices_always_private = True
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="C123",
        chat_type="channel",
        user_id=None,
        thread_id="111.222",
    )

    await runner._deliver_platform_notice(source, "hello")

    adapter.send_private_notice.assert_awaited_once()
    adapter.send.assert_not_awaited()
