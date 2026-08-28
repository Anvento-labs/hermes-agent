"""CRWD ``POST /leads`` ingress on the Chatwoot plugin listener."""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.chatwoot import adapter as cw
from plugins.platforms.chatwoot.leads import sanitize_for_log

_GIG = "69b8614f1083b9302fd0a9a7"
_OWNER = "69a6f191cb29b0b371b3a14f"
_USER = "aaaaaaaaaaaaaaaaaaaaaaaa"


class _FakeRequest:
    def __init__(self, body: bytes = b"", token=None, content_length=None):
        self._body = body
        self.query = {} if token is None else {"token": token}
        self.content_length = content_length if content_length is not None else len(body)

    async def read(self):
        return self._body


def _make_adapter(**extra) -> cw.ChatwootAdapter:
    base_extra = {"base_url": "https://cw.example.com", "account_id": "1"}
    base_extra.update(extra)
    cfg = PlatformConfig(enabled=True, token="bot-tok", extra=base_extra)
    a = cw.ChatwootAdapter(cfg)
    a._running = True
    return a


def _lead_bytes(**overrides) -> bytes:
    body = {
        "gig_id": _GIG,
        "business_owner_id": _OWNER,
        "user": {"email": "test@example.com", "full_name": "Test User"},
        "source": "lovable",
    }
    body.update(overrides)
    return json.dumps(body).encode()


_CREATE_OK = json.dumps(
    {
        "_type": "user",
        "created": True,
        "items": [{"_id": {"$oid": _USER}, "email": "test@example.com"}],
        "error": None,
    }
)
_INTEREST_OK = json.dumps(
    {
        "_type": "user_gig_interest",
        "created": True,
        "items": [{"_id": {"$oid": "bbbbbbbbbbbbbbbbbbbbbbbb"}, "status": "Interested"}],
        "error": None,
    }
)


_ENSURE_OK = {
    "account_id": "1",
    "contact_id": "10",
    "conversation_id": "42",
    "inbox_id": "5",
    "chat_id": "1:42",
    "created": True,
    "conversation_status": "pending",
}


def _patch_writes(create_ret=_CREATE_OK, interest_ret=_INTEREST_OK, ensure_ret=None):
    if ensure_ret is None:
        ensure_ret = dict(_ENSURE_OK)
    return (
        patch("plugins.platforms.chatwoot.leads._create_user", return_value=create_ret),
        patch("plugins.platforms.chatwoot.leads._add_user_gig_interest", return_value=interest_ret),
        patch("plugins.platforms.chatwoot.leads.ensure_conversation", return_value=ensure_ret),
    )


class TestSanitizeForLog:
    def test_redacts_secret_keys(self):
        out = sanitize_for_log({"email": "a@b.c", "token": "super-secret", "nested": {"api_key": "k"}})
        assert out["email"] == "a@b.c"
        assert out["token"] == "<redacted>"
        assert out["nested"]["api_key"] == "<redacted>"


class TestLeadsIngress:
    @pytest.mark.asyncio
    async def test_bad_secret_403(self):
        a = _make_adapter(webhook_secret="s3cret")
        resp = await a._handle_leads(_FakeRequest(b"{}", token="wrong"))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_missing_token_403_when_secret_configured(self):
        a = _make_adapter(webhook_secret="s3cret")
        resp = await a._handle_leads(_FakeRequest(b"{}"))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_happy_path_200(self, caplog):
        a = _make_adapter(webhook_secret="s3cret")
        p_create, p_interest, p_ensure = _patch_writes()
        with caplog.at_level(logging.INFO, logger="plugins.platforms.chatwoot.leads"):
            with p_create as create, p_interest as interest, p_ensure as ensure:
                resp = await a._handle_leads(_FakeRequest(_lead_bytes(), token="s3cret"))
        assert resp.status == 200
        payload = json.loads(resp.body.decode() if isinstance(resp.body, (bytes, bytearray)) else resp.body)
        assert payload == {
            "status": "ok",
            "accepted": True,
            "gig_id": _GIG,
            "business_owner_id": _OWNER,
            "user_id": _USER,
            "created": True,
            "interest_created": True,
            "membership_id": "bbbbbbbbbbbbbbbbbbbbbbbb",
            "account_id": "1",
            "conversation_id": "42",
            "chat_id": "1:42",
            "inbox_id": "5",
            "conversation_created": True,
            "contact_id": "10",
            "conversation_status": "pending",
            "coach_turn_started": False,
        }
        create.assert_called_once()
        interest.assert_called_once()
        ensure.assert_called_once()
        assert ensure.call_args.kwargs["crwd_user_id"] == _USER
        assert ensure.call_args.kwargs["email"] == "test@example.com"
        assert interest.call_args.kwargs["user_id"] == _USER
        assert interest.call_args.kwargs["crwd_id"] == _GIG
        assert interest.call_args.kwargs["business_owner_id"] == _OWNER
        assert "[chatwoot-leads] received" in caplog.text
        assert "test@example.com" in caplog.text

    @pytest.mark.asyncio
    async def test_empty_body_400(self):
        a = _make_adapter()
        resp = await a._handle_leads(_FakeRequest(b""))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_missing_user_400(self):
        a = _make_adapter()
        resp = await a._handle_leads(
            _FakeRequest(json.dumps({"gig_id": _GIG, "business_owner_id": _OWNER}).encode())
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_missing_business_owner_allowed(self):
        a = _make_adapter()
        p_create, p_interest, p_ensure = _patch_writes()
        with p_create as create, p_interest as interest, p_ensure:
            resp = await a._handle_leads(
                _FakeRequest(json.dumps({
                    "gig_id": _GIG,
                    "user": {"email": "a@b.c"},
                }).encode())
            )
        assert resp.status == 200
        assert create.call_args.kwargs["email"] == "a@b.c"
        assert interest.call_args.kwargs["business_owner_id"] == ""

    @pytest.mark.asyncio
    async def test_invalid_business_owner_400(self):
        a = _make_adapter()
        resp = await a._handle_leads(
            _FakeRequest(json.dumps({
                "gig_id": _GIG,
                "business_owner_id": "not-an-oid",
                "user": {"email": "a@b.c"},
            }).encode())
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_no_email_or_phone_400(self):
        a = _make_adapter()
        body = json.dumps({
            "gig_id": _GIG,
            "business_owner_id": _OWNER,
            "user": {"full_name": "X"},
        }).encode()
        resp = await a._handle_leads(_FakeRequest(body))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_normalizes_ten_digit_phone(self):
        a = _make_adapter()
        body = json.dumps({
            "gig_id": _GIG,
            "business_owner_id": _OWNER,
            "user": {"phone": "5551234567", "name": "Ada Lovelace"},
        }).encode()
        p_create, p_interest, p_ensure = _patch_writes()
        with p_create as create, p_interest, p_ensure:
            resp = await a._handle_leads(_FakeRequest(body))
        assert resp.status == 200
        kwargs = create.call_args.kwargs
        assert kwargs["phone"] == "+15551234567"
        assert kwargs["first_name"] == "Ada"
        assert kwargs["last_name"] == "Lovelace"

    @pytest.mark.asyncio
    async def test_create_user_error_500(self):
        a = _make_adapter()
        p_create, p_interest, _p_ensure = _patch_writes(
            create_ret=json.dumps({"error": "could not resolve member role"}),
        )
        with p_create, p_interest as interest, _p_ensure:
            resp = await a._handle_leads(_FakeRequest(_lead_bytes()))
        assert resp.status == 500
        interest.assert_not_called()

    @pytest.mark.asyncio
    async def test_interest_error_500_keeps_user_id(self):
        a = _make_adapter()
        p_create, p_interest, p_ensure = _patch_writes(
            interest_ret=json.dumps({"error": "unknown gig"}),
        )
        with p_create, p_interest, p_ensure as ensure:
            resp = await a._handle_leads(_FakeRequest(_lead_bytes()))
        assert resp.status == 500
        payload = json.loads(resp.body.decode() if isinstance(resp.body, (bytes, bytearray)) else resp.body)
        assert payload["accepted"] is False
        assert payload["user_id"] == _USER
        assert payload["error"] == "unknown gig"
        ensure.assert_not_called()

    @pytest.mark.asyncio
    async def test_coach_turn_started_when_handler_present(self):
        a = _make_adapter()

        async def _handler(event):
            return None

        a.set_message_handler(_handler)
        p_create, p_interest, p_ensure = _patch_writes()
        with p_create, p_interest, p_ensure:
            with patch("plugins.platforms.chatwoot.lead_turn.asyncio.create_task") as create:
                dummy = type("T", (), {"add_done_callback": lambda *a, **k: None})()
                create.return_value = dummy
                resp = await a._handle_leads(_FakeRequest(_lead_bytes()))
        assert resp.status == 200
        payload = json.loads(resp.body.decode() if isinstance(resp.body, (bytes, bytearray)) else resp.body)
        assert payload["coach_turn_started"] is True
        create.assert_called_once()
        coro = create.call_args[0][0]
        if hasattr(coro, "close"):
            coro.close()

    @pytest.mark.asyncio
    async def test_coach_turn_not_started_when_conversation_open(self):
        a = _make_adapter()
        a.set_message_handler(lambda e: None)
        p_create, p_interest, p_ensure = _patch_writes(
            ensure_ret={**_ENSURE_OK, "created": False, "conversation_status": "open"},
        )
        with p_create, p_interest, p_ensure:
            resp = await a._handle_leads(_FakeRequest(_lead_bytes()))
        assert resp.status == 200
        payload = json.loads(resp.body.decode() if isinstance(resp.body, (bytes, bytearray)) else resp.body)
        assert payload["coach_turn_started"] is False
        assert payload["conversation_status"] == "open"

    @pytest.mark.asyncio
    async def test_conversation_ensure_error_500_keeps_user_and_membership(self):
        a = _make_adapter()
        p_create, p_interest, p_ensure = _patch_writes(
            ensure_ret={"error": "no Channel::Api inbox on account 1"},
        )
        with p_create, p_interest, p_ensure:
            resp = await a._handle_leads(_FakeRequest(_lead_bytes()))
        assert resp.status == 500
        payload = json.loads(resp.body.decode() if isinstance(resp.body, (bytes, bytearray)) else resp.body)
        assert payload["accepted"] is False
        assert payload["user_id"] == _USER
        assert payload["membership_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"
        assert "Channel::Api" in payload["error"]

    @pytest.mark.asyncio
    async def test_bad_json_400(self):
        a = _make_adapter()
        resp = await a._handle_leads(_FakeRequest(b"not json"))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_not_running_404(self):
        a = _make_adapter()
        a._running = False
        resp = await a._handle_leads(_FakeRequest(b"{}"))
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_redacts_token_in_log(self, caplog):
        a = _make_adapter()
        body = json.dumps({
            "gig_id": _GIG,
            "business_owner_id": _OWNER,
            "user": {"email": "x@y.z"},
            "token": "should-not-appear",
        }).encode()
        p_create, p_interest, p_ensure = _patch_writes()
        with caplog.at_level(logging.INFO, logger="plugins.platforms.chatwoot.leads"):
            with p_create, p_interest, p_ensure:
                resp = await a._handle_leads(_FakeRequest(body))
        assert resp.status == 200
        assert "should-not-appear" not in caplog.text
        assert "<redacted>" in caplog.text
