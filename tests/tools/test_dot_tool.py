"""Tests for the dot transfers tool module (no live Dot API required)."""

import base64
import json
from unittest.mock import patch

import pytest

from tools import dot_tool as t


@pytest.fixture
def dot_env(monkeypatch):
    monkeypatch.setenv("DOTS_CLIENT_ID", "client-abc")
    monkeypatch.setenv("DOTS_API_KEY", "key-xyz")
    monkeypatch.delenv("DOTS_BASE_URL", raising=False)


class TestAvailability:
    def test_unavailable_without_client_id(self, monkeypatch):
        monkeypatch.delenv("DOTS_CLIENT_ID", raising=False)
        monkeypatch.setenv("DOTS_API_KEY", "key-xyz")
        assert t.check_dot_requirements() is False

    def test_unavailable_without_api_key(self, monkeypatch):
        monkeypatch.setenv("DOTS_CLIENT_ID", "client-abc")
        monkeypatch.delenv("DOTS_API_KEY", raising=False)
        assert t.check_dot_requirements() is False

    def test_available_with_both(self, dot_env):
        assert t.check_dot_requirements() is True


class TestAuthAndBaseUrl:
    def test_basic_auth_header(self, dot_env):
        expected = base64.b64encode(b"client-abc:key-xyz").decode()
        assert t._auth_headers() == {"Authorization": f"Basic {expected}"}

    def test_base_url_defaults_to_sandbox(self, dot_env):
        assert t._base_url() == t._DEFAULT_BASE_URL

    def test_base_url_override_strips_trailing_slash(self, dot_env, monkeypatch):
        monkeypatch.setenv("DOTS_BASE_URL", "https://api.dot.example.com/api/v2/")
        assert t._base_url() == "https://api.dot.example.com/api/v2"


class TestHandler:
    def test_noop_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("DOTS_CLIENT_ID", raising=False)
        monkeypatch.delenv("DOTS_API_KEY", raising=False)
        out = json.loads(t.dot_tool({"action": "get_user_transfers", "user_id": "u1"}))
        assert "error" in out and out["error"]

    def test_unknown_action(self, dot_env):
        out = json.loads(t.dot_tool({"action": "bogus", "user_id": "u1"}))
        assert "error" in out and out["error"]

    def test_missing_user_id(self, dot_env):
        out = json.loads(t.dot_tool({"action": "get_user_transfers", "user_id": ""}))
        assert "error" in out and out["error"]

    def test_missing_transfer_id(self, dot_env):
        out = json.loads(t.dot_tool({"action": "get_transfer", "transfer_id": ""}))
        assert "error" in out and out["error"]

    def test_user_transfers_success(self, dot_env):
        payload = {"data": [{"id": "tr1", "status": "sent"}]}
        with patch.object(t, "_dot_get", return_value=(payload, None)) as g:
            out = json.loads(t.dot_tool({"action": "get_user_transfers", "user_id": "u1"}))
        assert out["_type"] == "dot_user_transfers"
        assert out["user_id"] == "u1"
        assert out["data"] == payload
        assert out["error"] is None
        path, params = g.call_args.args
        assert path == "/transfers"
        assert params == {"user_id": "u1"}

    def test_get_transfer_success(self, dot_env):
        payload = {"id": "tr1", "amount": 1000, "status": "sent"}
        with patch.object(t, "_dot_get", return_value=(payload, None)) as g:
            out = json.loads(t.dot_tool({"action": "get_transfer", "transfer_id": "tr1"}))
        assert out["_type"] == "dot_transfer"
        assert out["transfer_id"] == "tr1"
        assert out["data"] == payload
        assert out["error"] is None
        path, _params = g.call_args.args
        assert path == "/transfers/tr1"

    def test_get_transfer_url_encodes_id(self, dot_env):
        with patch.object(t, "_dot_get", return_value=({}, None)) as g:
            t.dot_tool({"action": "get_transfer", "transfer_id": "a/b c"})
        path, _params = g.call_args.args
        assert path == "/transfers/a%2Fb%20c"

    def test_dot_error_degrades_gracefully(self, dot_env):
        with patch.object(t, "_dot_get", return_value=(None, "HTTP 502")):
            out = json.loads(t.dot_tool({"action": "get_user_transfers", "user_id": "u1"}))
        assert "error" in out and "502" in out["error"]

    def test_handler_never_raises_on_internal_error(self, dot_env):
        with patch.object(t, "_get_user_transfers", side_effect=RuntimeError("boom")):
            out = json.loads(t.dot_tool({"action": "get_user_transfers", "user_id": "u1"}))
        assert "error" in out and out["error"]


class TestDotGet:
    def test_http_error_is_captured(self, dot_env):
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "u", 404, "nf", {}, None
        )):
            data, err = t._dot_get("/transfers", {"user_id": "u1"})
        assert data is None
        assert err == "HTTP 404"

    def test_builds_url_with_base_and_params(self, dot_env):
        captured = {}

        class _Resp:
            status = 200

            def read(self):
                return b"[]"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return _Resp()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            data, err = t._dot_get("/transfers", {"user_id": "u1"})
        assert err is None
        assert data == []
        assert captured["url"] == f"{t._DEFAULT_BASE_URL}/transfers?user_id=u1"
