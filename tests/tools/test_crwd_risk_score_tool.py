"""Tests for the crwd_risk_score tool module (no live Chatwoot required)."""

import json
from unittest.mock import patch

import pytest

from tools import crwd_risk_score_tool as t


@pytest.fixture
def chatwoot_env(monkeypatch):
    monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
    monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "agent-tok")
    monkeypatch.setenv("CHATWOOT_ACCOUNT_ID", "1")
    monkeypatch.delenv("CHATWOOT_TOKEN", raising=False)


class TestAvailability:
    def test_unavailable_without_base_url(self, monkeypatch):
        monkeypatch.delenv("CHATWOOT_BASE_URL", raising=False)
        monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "x")
        assert t.check_crwd_risk_score_requirements() is False

    def test_unavailable_without_token(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.delenv("CHATWOOT_AGENT_TOKEN", raising=False)
        monkeypatch.delenv("CHATWOOT_TOKEN", raising=False)
        assert t.check_crwd_risk_score_requirements() is False

    def test_available_with_creds(self, chatwoot_env):
        assert t.check_crwd_risk_score_requirements() is True

    def test_falls_back_to_plain_token(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.delenv("CHATWOOT_AGENT_TOKEN", raising=False)
        monkeypatch.setenv("CHATWOOT_TOKEN", "plain-tok")
        assert t.check_crwd_risk_score_requirements() is True
        assert t._agent_token() == "plain-tok"


class TestResolveContact:
    def _with_session(self, platform, chat_id, user_id):
        return patch(
            "gateway.session_context.get_session_env",
            side_effect=lambda name, default="": {
                "HERMES_SESSION_PLATFORM": platform,
                "HERMES_SESSION_CHAT_ID": chat_id,
                "HERMES_SESSION_USER_ID": user_id,
            }.get(name, default),
        )

    def test_parses_account_and_contact(self, chatwoot_env):
        with self._with_session("chatwoot", "7:42", "99"):
            assert t._resolve_contact() == ("7", "99")

    def test_bare_chat_id_uses_account_env(self, chatwoot_env):
        with self._with_session("chatwoot", "42", "99"):
            assert t._resolve_contact() == ("1", "99")

    def test_wrong_platform_returns_none(self, chatwoot_env):
        with self._with_session("telegram", "7:42", "99"):
            assert t._resolve_contact() == (None, None)

    def test_missing_contact_returns_none(self, chatwoot_env):
        with self._with_session("chatwoot", "7:42", ""):
            assert t._resolve_contact() == (None, None)


class TestClampAndCoerce:
    def test_clamp_bounds(self):
        assert t._clamp(-5) == 0
        assert t._clamp(0) == 0
        assert t._clamp(50) == 50
        assert t._clamp(100) == 100
        assert t._clamp(150) == 100

    def test_coerce_int_handles_bad_values(self):
        assert t._coerce_int("20") == 20
        assert t._coerce_int(17.6) == 18
        assert t._coerce_int(None) == 0
        assert t._coerce_int("garbage", default=3) == 3


class TestHandler:
    def test_noop_when_no_delta(self, chatwoot_env):
        out = json.loads(t.crwd_risk_score_tool({}))
        assert out["updated"] is False
        assert out["error"] is None

    def test_noop_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("CHATWOOT_BASE_URL", raising=False)
        out = json.loads(t.crwd_risk_score_tool({"delta": 20}))
        assert out["updated"] is False
        assert out["error"] is None

    def test_noop_when_no_contact(self, chatwoot_env):
        with patch.object(t, "_resolve_contact", return_value=(None, None)):
            out = json.loads(t.crwd_risk_score_tool({"delta": 20}))
        assert out["updated"] is False
        assert out["error"] is None

    def test_adds_delta_and_preserves_other_attributes(self, chatwoot_env):
        contact = {
            "custom_attributes": {
                "risk_score": 10,
                "joincrwd_user_id": "abc123",
                "crwd_synced_at": "2026-01-01T00:00:00Z",
            }
        }
        with patch.object(t, "_resolve_contact", return_value=("1", "99")), patch.object(
            t, "_get_contact", return_value=contact
        ), patch.object(t, "_put_custom_attributes", return_value=(True, "")) as put:
            out = json.loads(
                t.crwd_risk_score_tool({"delta": 20, "reason": "wrong product"})
            )
        assert out["updated"] is True
        assert out["previous"] == 10
        assert out["new_score"] == 30
        written = put.call_args.args[2]
        assert written["risk_score"] == 30
        assert written["joincrwd_user_id"] == "abc123"
        assert written["crwd_synced_at"] == "2026-01-01T00:00:00Z"

    def test_clamps_at_100(self, chatwoot_env):
        contact = {"custom_attributes": {"risk_score": 90}}
        with patch.object(t, "_resolve_contact", return_value=("1", "99")), patch.object(
            t, "_get_contact", return_value=contact
        ), patch.object(t, "_put_custom_attributes", return_value=(True, "")) as put:
            out = json.loads(t.crwd_risk_score_tool({"delta": 30}))
        assert out["new_score"] == 100
        assert put.call_args.args[2]["risk_score"] == 100

    def test_clamps_at_zero_with_negative_delta(self, chatwoot_env):
        contact = {"custom_attributes": {"risk_score": 5}}
        with patch.object(t, "_resolve_contact", return_value=("1", "99")), patch.object(
            t, "_get_contact", return_value=contact
        ), patch.object(t, "_put_custom_attributes", return_value=(True, "")):
            out = json.loads(t.crwd_risk_score_tool({"delta": -20}))
        assert out["new_score"] == 0

    def test_missing_attribute_starts_from_zero(self, chatwoot_env):
        contact = {"custom_attributes": {}}
        with patch.object(t, "_resolve_contact", return_value=("1", "99")), patch.object(
            t, "_get_contact", return_value=contact
        ), patch.object(t, "_put_custom_attributes", return_value=(True, "")):
            out = json.loads(t.crwd_risk_score_tool({"delta": 15}))
        assert out["previous"] == 0
        assert out["new_score"] == 15

    def test_get_contact_failure_degrades(self, chatwoot_env):
        with patch.object(t, "_resolve_contact", return_value=("1", "99")), patch.object(
            t, "_get_contact", return_value=None
        ):
            out = json.loads(t.crwd_risk_score_tool({"delta": 15}))
        assert out["updated"] is False
        assert out["error"] is None

    def test_put_failure_degrades_gracefully(self, chatwoot_env):
        contact = {"custom_attributes": {"risk_score": 10}}
        with patch.object(t, "_resolve_contact", return_value=("1", "99")), patch.object(
            t, "_get_contact", return_value=contact
        ), patch.object(t, "_put_custom_attributes", return_value=(False, "HTTP 403")):
            out = json.loads(t.crwd_risk_score_tool({"delta": 15}))
        assert out["updated"] is False
        assert out["error"] is None
