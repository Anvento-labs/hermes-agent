"""Unit tests for the Chatwoot contact ``ai_mode`` pre-LLM short-circuit."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugins.platforms.chatwoot import ai_mode as am


class FakeAdapter:
    pass


def _make_event(*, custom_attributes=Ellipsis, sender=Ellipsis, raw_message=Ellipsis):
    if raw_message is not Ellipsis:
        return SimpleNamespace(raw_message=raw_message, source=SimpleNamespace(chat_id="1:42"))

    if sender is not Ellipsis:
        payload_sender = sender
    else:
        payload_sender = {"id": "77", "phone_number": "+15551234567"}
        if custom_attributes is not Ellipsis:
            payload_sender["custom_attributes"] = custom_attributes

    return SimpleNamespace(
        raw_message={
            "sender": payload_sender,
            "account": {"id": "1"},
            "conversation": {"id": "42"},
        },
        source=SimpleNamespace(chat_id="1:42"),
    )


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def adapter():
    return FakeAdapter()


# --- _is_enabled ------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        (True, True),
        ("true", True),
        ("True", True),
        (" TRUE ", True),
        (False, False),
        ("false", False),
        ("False", False),
        (None, False),
        (0, False),
        (1, False),
        ("", False),
        ("yes", False),
        ([], False),
    ],
)
def test_is_enabled(value, expected):
    assert am._is_enabled(value) is expected


# --- maybe_short_circuit ----------------------------------------------------

@pytest.mark.parametrize(
    "ai_mode_value",
    [True, "true", "True", " TRUE "],
)
def test_proceed_when_explicitly_true(adapter, ai_mode_value):
    event = _make_event(custom_attributes={"ai_mode": ai_mode_value})
    assert _run(am.maybe_short_circuit(adapter, event)) is False


@pytest.mark.parametrize(
    "custom_attributes",
    [
        {},  # missing key
        {"ai_mode": False},
        {"ai_mode": "false"},
        {"ai_mode": None},
        {"ai_mode": 0},
        {"ai_mode": ""},
        {"ai_mode": "yes"},
        {"joincrwd_user_id": "abc"},  # other attrs only
    ],
)
def test_skip_when_not_true(adapter, custom_attributes):
    event = _make_event(custom_attributes=custom_attributes)
    assert _run(am.maybe_short_circuit(adapter, event)) is True


def test_skip_when_custom_attributes_missing(adapter):
    event = _make_event()  # sender without custom_attributes
    assert _run(am.maybe_short_circuit(adapter, event)) is True


@pytest.mark.parametrize(
    "raw_message",
    [
        None,
        "not-a-dict",
        {},
        {"sender": None},
        {"sender": "x"},
        {"sender": {"custom_attributes": "x"}},
    ],
)
def test_skip_malformed_payload_no_raise(adapter, raw_message):
    event = _make_event(raw_message=raw_message)
    assert _run(am.maybe_short_circuit(adapter, event)) is True
