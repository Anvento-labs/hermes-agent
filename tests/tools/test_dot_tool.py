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


class TestCreateUser:
    _member = {
        "action": "create_user",
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phone_number": "(415) 555-1234",
        "country_code": "+1",
    }

    def test_returns_user_id_and_flags_new(self, dot_env):
        with patch.object(t, "_dot_post", return_value=({"id": "dot-u-1"}, None)):
            out = json.loads(t.dot_tool(dict(self._member)))
        assert out["user_id"] == "dot-u-1"
        assert out["user_is_new"] is True
        assert out["error"] is None

    def test_accepts_user_id_key_from_dot(self, dot_env):
        with patch.object(t, "_dot_post", return_value=({"user_id": "dot-u-2"}, None)):
            out = json.loads(t.dot_tool(dict(self._member)))
        assert out["user_id"] == "dot-u-2"

    def test_posts_normalized_body_to_users(self, dot_env):
        with patch.object(t, "_dot_post", return_value=({"id": "x"}, None)) as post:
            t.dot_tool(dict(self._member))
        path, body = post.call_args.args
        assert path == "/users"
        assert body["phone_number"] == "4155551234"
        assert body["country_code"] == "1"
        assert body["email"] == "jane@example.com"
        assert body["idempotency_key"]

    def test_idempotency_key_stable_across_formatting(self, dot_env):
        keys = []
        with patch.object(t, "_dot_post", return_value=({"id": "x"}, None)) as post:
            t.dot_tool(dict(self._member))
            keys.append(post.call_args.args[1]["idempotency_key"])
            t.dot_tool({**self._member, "email": "JANE@example.com ", "phone_number": "415-555-1234"})
            keys.append(post.call_args.args[1]["idempotency_key"])
        assert keys[0] == keys[1]

    def test_different_members_get_different_keys(self, dot_env):
        with patch.object(t, "_dot_post", return_value=({"id": "x"}, None)) as post:
            t.dot_tool(dict(self._member))
            first = post.call_args.args[1]["idempotency_key"]
            t.dot_tool({**self._member, "email": "someone.else@example.com", "phone_number": "2125550000"})
            second = post.call_args.args[1]["idempotency_key"]
        assert first != second

    def test_requires_email_or_phone(self, dot_env):
        out = json.loads(t.dot_tool({"action": "create_user", "first_name": "Jane"}))
        assert out["error"]

    def test_duplicate_rejection_surfaces_and_says_hand_off(self, dot_env):
        # Production rejects a duplicate phone; the coach must not guess.
        err = 'HTTP 400: {"error":"user with phone_number already exists"}'
        with patch.object(t, "_dot_post", return_value=(None, err)):
            out = json.loads(t.dot_tool(dict(self._member)))
        assert out["error"]
        assert "already exists" in out["error"]
        assert "hand off" in out["error"].lower()

    def test_missing_id_in_response_is_an_error(self, dot_env):
        with patch.object(t, "_dot_post", return_value=({"status": "ok"}, None)):
            out = json.loads(t.dot_tool(dict(self._member)))
        assert out["error"]

    def test_never_raises(self, dot_env):
        with patch.object(t, "_dot_post", side_effect=RuntimeError("boom")):
            out = json.loads(t.dot_tool(dict(self._member)))
        assert out["error"]


class TestDotPost:
    def test_http_error_body_is_kept(self, dot_env):
        import urllib.error
        import io

        exc = urllib.error.HTTPError(
            "u", 400, "Bad Request", {}, io.BytesIO(b'{"error":"already exists"}')
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            data, err = t._dot_post("/users", {"email": "a@b.com"})
        assert data is None
        assert "400" in err and "already exists" in err
