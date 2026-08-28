"""Tests for Chatwoot lead conversation ensure (no live Chatwoot)."""

from unittest.mock import patch

import pytest

from plugins.platforms.chatwoot import conversations as cw


@pytest.fixture
def chatwoot_env(monkeypatch):
    monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
    monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "agent-tok")
    monkeypatch.setenv("CHATWOOT_ACCOUNT_ID", "1")
    monkeypatch.delenv("CHATWOOT_INBOX_ID", raising=False)
    monkeypatch.delenv("CHATWOOT_TOKEN", raising=False)


API_INBOX = {"id": 5, "name": "API", "channel_type": "Channel::Api"}
SMS_INBOX = {"id": 9, "name": "SMS", "channel_type": "Channel::TwilioSms"}
API_INBOX_B = {"id": 6, "name": "API-2", "channel_type": "Channel::Api"}

CONTACT = {
    "id": 10,
    "name": "Test User",
    "email": "test@example.com",
    "phone_number": "+15551234567",
    "custom_attributes": {
        "joincrwd_user_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
        "ai_mode": True,
    },
    "contact_inboxes": [{"source_id": "src-api", "inbox": {"id": 5}}],
}


def _ensure(**overrides):
    kwargs = {
        "email": "test@example.com",
        "phone": "+15551234567",
        "name": "Test User",
        "crwd_user_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
    }
    kwargs.update(overrides)
    return cw.ensure_conversation(**kwargs)


class TestResolveApiInbox:
    def test_unique_api_inbox(self, chatwoot_env):
        with patch.object(
            cw, "api_request", return_value=(True, {"payload": [API_INBOX]}, "")
        ):
            inbox, err = cw.resolve_api_inbox("1")
        assert err == ""
        assert inbox["id"] == 5

    def test_unique_sms_inbox(self, chatwoot_env):
        """SMS/Twilio is an eligible lead inbox too, not just Channel::Api."""
        with patch.object(
            cw, "api_request", return_value=(True, {"payload": [SMS_INBOX]}, "")
        ):
            inbox, err = cw.resolve_api_inbox("1")
        assert err == ""
        assert inbox["id"] == 9

    def test_no_eligible_inbox(self, chatwoot_env):
        email_inbox = {"id": 3, "name": "Email", "channel_type": "Channel::Email"}
        with patch.object(
            cw, "api_request", return_value=(True, {"payload": [email_inbox]}, "")
        ):
            inbox, err = cw.resolve_api_inbox("1")
        assert inbox is None
        assert "no inbox of type" in err
        assert "Channel::Api" in err and "Channel::TwilioSms" in err

    def test_many_api_inboxes_without_prefer(self, chatwoot_env):
        with patch.object(
            cw,
            "api_request",
            return_value=(True, {"payload": [API_INBOX, API_INBOX_B]}, ""),
        ):
            inbox, err = cw.resolve_api_inbox("1")
        assert inbox is None
        assert "CHATWOOT_INBOX_ID" in err

    def test_api_and_sms_together_without_prefer_is_ambiguous(self, chatwoot_env):
        """Both types are eligible, so having one of each is still ambiguous --
        this is exactly prod's shape (an unused API inbox + several SMS
        inboxes), which is why prod must pin CHATWOOT_INBOX_ID explicitly."""
        with patch.object(
            cw,
            "api_request",
            return_value=(True, {"payload": [API_INBOX, SMS_INBOX]}, ""),
        ):
            inbox, err = cw.resolve_api_inbox("1")
        assert inbox is None
        assert "CHATWOOT_INBOX_ID" in err

    def test_preferred_sms_inbox_allowed(self, chatwoot_env, monkeypatch):
        """A preferred Twilio SMS inbox is now accepted, not rejected."""
        monkeypatch.setenv("CHATWOOT_INBOX_ID", "9")
        with patch.object(
            cw,
            "api_request",
            return_value=(True, {"payload": [API_INBOX, SMS_INBOX]}, ""),
        ):
            inbox, err = cw.resolve_api_inbox("1")
        assert err == ""
        assert inbox["id"] == 9

    def test_preferred_must_be_eligible_type(self, chatwoot_env, monkeypatch):
        monkeypatch.setenv("CHATWOOT_INBOX_ID", "3")
        email_inbox = {"id": 3, "name": "Email", "channel_type": "Channel::Email"}
        with patch.object(
            cw,
            "api_request",
            return_value=(True, {"payload": [API_INBOX, email_inbox]}, ""),
        ):
            inbox, err = cw.resolve_api_inbox("1")
        assert inbox is None
        assert "Channel::Email" in err
        assert "expected one of" in err

    def test_preferred_api_inbox(self, chatwoot_env, monkeypatch):
        monkeypatch.setenv("CHATWOOT_INBOX_ID", "6")
        with patch.object(
            cw,
            "api_request",
            return_value=(True, {"payload": [API_INBOX, API_INBOX_B]}, ""),
        ):
            inbox, err = cw.resolve_api_inbox("1")
        assert err == ""
        assert inbox["id"] == 6


class TestEnsureConversation:
    def test_reuses_api_thread_when_sms_also_exists(self, chatwoot_env, monkeypatch):
        # Both inbox types are now eligible on their own, so an account with
        # one of each is ambiguous -- pin the target explicitly, same as prod
        # would, so this test still exercises conversation-reuse and not
        # inbox-resolution ambiguity.
        monkeypatch.setenv("CHATWOOT_INBOX_ID", "5")
        calls = []

        def fake_api(method, path, body=None, query=None):
            calls.append((method, path, body, query))
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX, SMS_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [CONTACT]}, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {
                    "payload": [
                        {"id": 100, "inbox_id": 9, "status": "pending"},
                        {"id": 42, "inbox_id": 5, "status": "open"},
                    ]
                }, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out.get("error") is None
        assert out["conversation_id"] == "42"
        assert out["created"] is False
        assert out["conversation_status"] == "open"
        assert out["chat_id"] == "1:42"
        assert out["channel_type"] == "Channel::Api"
        assert out["inbox_name"] == "API"
        assert not any(c[0] == "POST" and c[1].endswith("/conversations") for c in calls)

    def test_creates_when_only_sms_conversation(self, chatwoot_env, monkeypatch):
        monkeypatch.setenv("CHATWOOT_INBOX_ID", "5")

        def fake_api(method, path, body=None, query=None):
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX, SMS_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [CONTACT]}, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {
                    "payload": [{"id": 100, "inbox_id": 9, "status": "pending"}]
                }, ""
            if method == "POST" and path.endswith("/conversations"):
                assert body["status"] == "pending"
                assert body["source_id"] == "src-api"
                assert "message" not in body
                return True, {"id": 77, "inbox_id": 5, "account_id": 1}, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out.get("error") is None
        assert out["conversation_id"] == "77"
        assert out["created"] is True
        assert out["inbox_id"] == "5"
        assert out["conversation_status"] == "pending"

    def test_reuses_existing_sms_conversation(self, chatwoot_env, monkeypatch):
        """Prod's actual shape: pin CHATWOOT_INBOX_ID to the preferred SMS
        inbox; if this contact already has a conversation there, reuse it
        rather than opening a second thread."""
        monkeypatch.setenv("CHATWOOT_INBOX_ID", "9")
        calls = []

        def fake_api(method, path, body=None, query=None):
            calls.append((method, path))
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX, SMS_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [CONTACT]}, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {
                    "payload": [{"id": 200, "inbox_id": 9, "status": "pending"}]
                }, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out.get("error") is None
        assert out["conversation_id"] == "200"
        assert out["created"] is False
        assert out["inbox_id"] == "9"
        assert out["channel_type"] == "Channel::TwilioSms"
        assert out["inbox_name"] == "SMS"
        assert not any(m == "POST" and p.endswith("/conversations") for m, p in calls)

    def test_creates_new_sms_conversation_when_none_exists(self, chatwoot_env, monkeypatch):
        """No existing thread in the SMS inbox -- create one there, using the
        phone number as source_id (Twilio needs the real number to deliver)."""
        monkeypatch.setenv("CHATWOOT_INBOX_ID", "9")
        contact = dict(CONTACT)
        contact["contact_inboxes"] = []

        def fake_api(method, path, body=None, query=None):
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX, SMS_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [contact]}, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {"payload": []}, ""
            if method == "POST" and path.endswith("/contact_inboxes"):
                assert body["inbox_id"] == 9
                return True, {"source_id": "+15551234567", "inbox": {"id": 9}}, ""
            if method == "POST" and path.endswith("/conversations"):
                assert body["inbox_id"] == 9
                assert body["source_id"] == "+15551234567"
                return True, {"id": 201, "inbox_id": 9}, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out.get("error") is None
        assert out["conversation_id"] == "201"
        assert out["created"] is True
        assert out["inbox_id"] == "9"
        assert out["channel_type"] == "Channel::TwilioSms"

    def test_creates_contact_and_conversation_when_missing(self, chatwoot_env):
        def fake_api(method, path, body=None, query=None):
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": []}, ""
            if method == "POST" and path.endswith("/contacts"):
                assert body["custom_attributes"]["joincrwd_user_id"] == "aaaaaaaaaaaaaaaaaaaaaaaa"
                assert body["inbox_id"] == 5
                return True, {
                    "payload": {
                        "contact": {
                            "id": 10,
                            "email": "test@example.com",
                            "contact_inboxes": [],
                        },
                        "contact_inbox": {"source_id": "new-src", "inbox": {"id": 5}},
                    }
                }, ""
            if method == "PUT" and path.endswith("/contacts/10"):
                assert body["custom_attributes"]["ai_mode"] is True
                return True, {
                    "id": 10,
                    "custom_attributes": body["custom_attributes"],
                    "contact_inboxes": [{"source_id": "new-src", "inbox": {"id": 5}}],
                }, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {"payload": []}, ""
            if method == "POST" and path.endswith("/conversations"):
                assert body["source_id"] == "new-src"
                return True, {"id": 88}, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out.get("error") is None
        assert out["conversation_id"] == "88"
        assert out["created"] is True
        assert out["contact_created"] is True

    def test_creates_contact_inbox_when_source_missing(self, chatwoot_env):
        contact = dict(CONTACT)
        contact["contact_inboxes"] = []

        def fake_api(method, path, body=None, query=None):
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [contact]}, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {"payload": []}, ""
            if method == "POST" and path.endswith("/contact_inboxes"):
                return True, {"source_id": "minted-src", "inbox": {"id": 5}}, ""
            if method == "POST" and path.endswith("/conversations"):
                assert body["source_id"] == "minted-src"
                return True, {"id": 91}, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out.get("error") is None
        assert out["conversation_id"] == "91"

    def test_prefers_pending_over_resolved(self, chatwoot_env):
        def fake_api(method, path, body=None, query=None):
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [CONTACT]}, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {
                    "payload": [
                        {"id": 1, "inbox_id": 5, "status": "resolved", "last_activity_at": 9},
                        {"id": 2, "inbox_id": 5, "status": "pending", "last_activity_at": 1},
                    ]
                }, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out["conversation_id"] == "2"

    def test_turns_on_ai_mode_when_not_already_set(self, chatwoot_env):
        contact = dict(CONTACT)
        contact["custom_attributes"] = {"joincrwd_user_id": "aaaaaaaaaaaaaaaaaaaaaaaa"}
        put_bodies = []

        def fake_api(method, path, body=None, query=None):
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [contact]}, ""
            if method == "PUT" and path.endswith("/contacts/10"):
                put_bodies.append(body)
                return True, {"id": 10, "custom_attributes": body["custom_attributes"]}, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {"payload": [{"id": 2, "inbox_id": 5, "status": "pending"}]}, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out.get("error") is None
        assert len(put_bodies) == 1
        assert put_bodies[0]["custom_attributes"] == {
            "joincrwd_user_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
            "ai_mode": True,
        }

    def test_does_not_touch_ai_mode_when_already_true(self, chatwoot_env):
        # CONTACT already carries ai_mode: True; any PUT would hit the
        # catch-all AssertionError below, so this asserts the no-op path.
        def fake_api(method, path, body=None, query=None):
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [CONTACT]}, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {"payload": [{"id": 2, "inbox_id": 5, "status": "pending"}]}, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out.get("error") is None

    def test_fills_missing_name_and_phone_on_existing_contact(self, chatwoot_env):
        contact = dict(CONTACT)
        contact["name"] = ""
        contact["phone_number"] = ""
        put_bodies = []

        def fake_api(method, path, body=None, query=None):
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [contact]}, ""
            if method == "PUT" and path.endswith("/contacts/10"):
                put_bodies.append(body)
                contact.update(body)
                return True, dict(contact), ""
            if path.endswith("/conversations") and method == "GET":
                return True, {"payload": [{"id": 2, "inbox_id": 5, "status": "pending"}]}, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure()
        assert out.get("error") is None
        assert put_bodies == [{"name": "Test User", "phone_number": "+15551234567"}]

    def test_does_not_overwrite_existing_name_or_phone(self, chatwoot_env):
        # CONTACT already has name + phone_number; a PUT here would hit the
        # catch-all AssertionError below, so this asserts the no-op path.
        def fake_api(method, path, body=None, query=None):
            if path.endswith("/inboxes"):
                return True, {"payload": [API_INBOX]}, ""
            if "/contacts/search" in path:
                return True, {"payload": [CONTACT]}, ""
            if path.endswith("/conversations") and method == "GET":
                return True, {"payload": [{"id": 2, "inbox_id": 5, "status": "pending"}]}, ""
            raise AssertionError(f"unexpected {method} {path}")

        with patch.object(cw, "api_request", side_effect=fake_api):
            out = _ensure(name="A Different Name")
        assert out.get("error") is None

    def test_fails_without_eligible_inbox(self, chatwoot_env):
        email_inbox = {"id": 3, "name": "Email", "channel_type": "Channel::Email"}
        with patch.object(
            cw, "api_request", return_value=(True, {"payload": [email_inbox]}, "")
        ):
            out = _ensure()
        assert out.get("error")
        assert "no inbox of type" in out["error"]

    def test_fails_without_account(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "agent-tok")
        monkeypatch.delenv("CHATWOOT_ACCOUNT_ID", raising=False)
        out = _ensure()
        assert "CHATWOOT_ACCOUNT_ID" in out["error"]
