"""Unit tests for the unregistered-contact pre-LLM short-circuit.

Fake adapter + monkeypatched Mongo lookup and label assignment. No live
Mongo/Chatwoot is touched.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugins.platforms.chatwoot import unregistered as un


# --- fakes ------------------------------------------------------------------

class FakeAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append((chat_id, content))


def make_event(
    *,
    contact_id="77",
    account_id="1",
    conversation_id="42",
    email=None,
    phone="+15551234567",
    joincrwd_user_id=None,
):
    sender = {"id": contact_id, "email": email, "phone_number": phone}
    if joincrwd_user_id is not None:
        sender["custom_attributes"] = {"joincrwd_user_id": joincrwd_user_id}
    payload = {
        "sender": sender,
        "account": {"id": account_id},
        "conversation": {"id": conversation_id},
    }
    return SimpleNamespace(
        raw_message=payload,
        source=SimpleNamespace(chat_id=f"{account_id}:{conversation_id}"),
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    un._reset_caches()
    monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://test")
    monkeypatch.setenv("CRWD_APP_BASE_URL", "https://live-staging.joincrwd.com")
    monkeypatch.delenv("CRWD_SIGNUP_URL", raising=False)
    yield
    un._reset_caches()


@pytest.fixture
def assigned(monkeypatch):
    calls = []

    def fake_assign(account_id, conversation_id, labels, replace):
        calls.append((account_id, conversation_id, labels, replace))
        return {"success": True, "labels": labels, "error": None}

    from plugins.platforms.chatwoot import labels_tool

    monkeypatch.setattr(labels_tool, "_assign_labels", fake_assign)
    return calls


def _patch_fetch(monkeypatch, result):
    from plugins.platforms.chatwoot import enrichment

    calls = []

    def fake_fetch(email, phone):
        calls.append((email, phone))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(enrichment, "fetch_user", fake_fetch)
    return calls


# --- signup URL -------------------------------------------------------------

def test_signup_url_from_app_base():
    assert un.signup_url() == "https://live-staging.joincrwd.com/signup"


def test_signup_url_explicit_override(monkeypatch):
    monkeypatch.setenv("CRWD_SIGNUP_URL", "https://example.com/join")
    assert un.signup_url() == "https://example.com/join"


# --- short-circuit decisions ------------------------------------------------

def test_unregistered_sends_reply_and_label(monkeypatch, assigned):
    _patch_fetch(monkeypatch, None)
    adapter = FakeAdapter()
    handled = asyncio.run(un.maybe_short_circuit(adapter, make_event()))
    assert handled is True
    assert len(adapter.sent) == 1
    chat_id, text = adapter.sent[0]
    assert chat_id == "1:42"
    assert "https://live-staging.joincrwd.com/signup" in text
    assert assigned == [("1", "42", ["unregistered-user"], False)]


def test_cooldown_suppresses_second_reply(monkeypatch, assigned):
    _patch_fetch(monkeypatch, None)
    adapter = FakeAdapter()
    assert asyncio.run(un.maybe_short_circuit(adapter, make_event())) is True
    assert asyncio.run(un.maybe_short_circuit(adapter, make_event())) is True
    # Still short-circuits (no LLM), but only one canned reply was sent.
    assert len(adapter.sent) == 1


def test_registered_user_falls_through(monkeypatch, assigned):
    _patch_fetch(monkeypatch, {"_id": "abc"})
    adapter = FakeAdapter()
    assert asyncio.run(un.maybe_short_circuit(adapter, make_event())) is False
    assert adapter.sent == []
    assert assigned == []


def test_mongo_error_falls_through(monkeypatch, assigned):
    _patch_fetch(monkeypatch, RuntimeError("mongo down"))
    adapter = FakeAdapter()
    assert asyncio.run(un.maybe_short_circuit(adapter, make_event())) is False
    assert adapter.sent == []
    assert assigned == []


def test_webhook_hint_skips_mongo(monkeypatch, assigned):
    calls = _patch_fetch(monkeypatch, None)
    adapter = FakeAdapter()
    event = make_event(joincrwd_user_id="69e273fb1d163ce2fd86754c")
    assert asyncio.run(un.maybe_short_circuit(adapter, event)) is False
    assert calls == []
    assert adapter.sent == []


def test_no_email_no_phone_falls_through(monkeypatch, assigned):
    calls = _patch_fetch(monkeypatch, None)
    adapter = FakeAdapter()
    event = make_event(email=None, phone=None)
    assert asyncio.run(un.maybe_short_circuit(adapter, event)) is False
    assert calls == []


def test_negative_cache_expiry_rechecks(monkeypatch, assigned):
    calls = _patch_fetch(monkeypatch, None)
    adapter = FakeAdapter()
    assert asyncio.run(un.maybe_short_circuit(adapter, make_event())) is True
    assert len(calls) == 1

    # Within TTL: cached, no new Mongo call.
    assert asyncio.run(un.maybe_short_circuit(adapter, make_event())) is True
    assert len(calls) == 1

    # Expire the negative entry; user has now signed up.
    un._reset_caches()
    from plugins.platforms.chatwoot import enrichment

    monkeypatch.setattr(enrichment, "fetch_user", lambda e, p: {"_id": "abc"})
    assert asyncio.run(un.maybe_short_circuit(adapter, make_event())) is False


def test_enrichment_disabled_falls_through(monkeypatch, assigned):
    monkeypatch.delenv("CRWD_MONGO_URI", raising=False)
    calls = _patch_fetch(monkeypatch, None)
    adapter = FakeAdapter()
    assert asyncio.run(un.maybe_short_circuit(adapter, make_event())) is False
    assert calls == []


def test_send_failure_still_short_circuits(monkeypatch, assigned):
    _patch_fetch(monkeypatch, None)

    class BrokenAdapter:
        async def send(self, chat_id, content, **kw):
            raise RuntimeError("chatwoot down")

    assert asyncio.run(un.maybe_short_circuit(BrokenAdapter(), make_event())) is True


def test_label_in_applied_taxonomy():
    from plugins.platforms.chatwoot.labels import APPLIED_LABEL_TITLES

    assert un.UNREGISTERED_LABEL in APPLIED_LABEL_TITLES
