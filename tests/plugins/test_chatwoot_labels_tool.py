"""Tests for the Chatwoot labels tool (no live Chatwoot required)."""

import json
from unittest.mock import patch

import pytest

from plugins.platforms.chatwoot import labels_tool as t


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
        assert t.check_chatwoot_labels_requirements() is False

    def test_unavailable_without_token(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.delenv("CHATWOOT_AGENT_TOKEN", raising=False)
        monkeypatch.delenv("CHATWOOT_TOKEN", raising=False)
        assert t.check_chatwoot_labels_requirements() is False

    def test_available_with_creds(self, chatwoot_env):
        assert t.check_chatwoot_labels_requirements() is True

    def test_falls_back_to_plain_token(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.delenv("CHATWOOT_AGENT_TOKEN", raising=False)
        monkeypatch.setenv("CHATWOOT_TOKEN", "plain-tok")
        assert t.check_chatwoot_labels_requirements() is True
        assert t._agent_token() == "plain-tok"


class TestMergeLabels:
    def test_merge_preserves_existing_order(self):
        assert t._merge_labels(["a"], ["b", "c"]) == ["a", "b", "c"]

    def test_merge_dedupes(self):
        assert t._merge_labels(["payment-payout"], ["payment-payout", "troubleshooting"]) == [
            "payment-payout",
            "troubleshooting",
        ]

    def test_merge_normalizes_case(self):
        assert t._merge_labels(["Gig-Discovery"], ["gig-discovery"]) == ["gig-discovery"]


class TestApplyAddRemove:
    def test_add_appends(self):
        assert t._apply_add_remove(["a"], ["b", "c"], []) == ["a", "b", "c"]

    def test_remove_drops(self):
        assert t._apply_add_remove(["a", "b", "c"], [], ["b"]) == ["a", "c"]

    def test_add_wins_over_remove(self):
        assert t._apply_add_remove(["old"], ["keep"], ["keep"]) == ["old", "keep"]

    def test_preserves_unmentioned(self):
        assert t._apply_add_remove(
            ["risk-low", "human-tag", "payment-issue"],
            ["app-help"],
            ["payment-issue"],
        ) == ["risk-low", "human-tag", "app-help"]


class TestResolveConversation:
    def _with_session(self, platform, chat_id):
        return patch(
            "gateway.session_context.get_session_env",
            side_effect=lambda name, default="": {
                "HERMES_SESSION_PLATFORM": platform,
                "HERMES_SESSION_CHAT_ID": chat_id,
            }.get(name, default),
        )

    def test_parses_account_and_conversation(self, chatwoot_env):
        with self._with_session("chatwoot", "7:42"):
            assert t._resolve_conversation() == ("7", "42")

    def test_bare_id_uses_account_env(self, chatwoot_env):
        with self._with_session("chatwoot", "42"):
            assert t._resolve_conversation() == ("1", "42")

    def test_override_params(self, chatwoot_env):
        assert t._resolve_conversation("9", "99") == ("9", "99")

    def test_wrong_platform_returns_none(self, chatwoot_env):
        with self._with_session("telegram", "7:42"):
            assert t._resolve_conversation() == (None, None)


class TestCreateLabelsIfNotExists:
    def test_creates_only_missing_applied(self, chatwoot_env):
        existing_payload = {
            "payload": [{"title": "app-help", "id": 1}],
        }

        def fake_api(method, path, body=None):
            if method == "GET" and path.endswith("/labels") and "conversations" not in path:
                return True, existing_payload, ""
            if method == "POST" and path.endswith("/labels"):
                return True, {"payload": body}, ""
            return False, None, "unexpected"

        with patch.object(t, "_api_request", side_effect=fake_api):
            out = t._create_labels_if_not_exists("1")

        from plugins.platforms.chatwoot.labels import (
            APPLIED_LABEL_TITLES,
            UNAPPLIED_LABEL_TITLES,
        )

        assert "app-help" not in out["created"]
        expected = sorted(APPLIED_LABEL_TITLES - {"app-help"})
        assert sorted(out["created"]) == expected
        # Unapplied titles must never be bootstrapped.
        assert not (set(out["created"]) & UNAPPLIED_LABEL_TITLES)
        assert "app-help" in (out.get("existing") or [])

    def test_does_not_create_unapplied(self, chatwoot_env):
        from plugins.platforms.chatwoot.labels import UNAPPLIED_LABEL_TITLES

        def fake_api(method, path, body=None):
            if method == "GET" and path.endswith("/labels") and "conversations" not in path:
                return True, {"payload": []}, ""
            if method == "POST" and path.endswith("/labels"):
                return True, {"payload": body}, ""
            return False, None, "unexpected"

        with patch.object(t, "_api_request", side_effect=fake_api):
            out = t._create_labels_if_not_exists("1")

        assert not (set(out["created"]) & UNAPPLIED_LABEL_TITLES)

    def test_get_failure(self, chatwoot_env):
        with patch.object(t, "_api_request", return_value=(False, None, "HTTP 401")):
            out = t._create_labels_if_not_exists("1")
        assert out["success"] is False
        assert out["error"] == "HTTP 401"


class TestAssignLabels:
    def test_add_preserves_existing(self, chatwoot_env):
        calls = []

        def fake_api(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET" and "conversations" in path:
                return True, {"payload": ["gig-discovery", "risk-low"]}, ""
            if method == "POST" and "conversations" in path:
                return True, {"payload": body["labels"]}, ""
            if method == "GET":
                return True, {"payload": [{"title": x} for x in t.APPLIED_LABEL_TITLES]}, ""
            return False, None, "unexpected"

        with patch.object(t, "_api_request", side_effect=fake_api):
            out = t._assign_labels("1", "42", add=["payment-issue"], remove=[])

        assert out["success"] is True
        assert out["labels"] == ["gig-discovery", "risk-low", "payment-issue"]
        post_calls = [c for c in calls if c[0] == "POST" and "conversations" in c[1]]
        assert len(post_calls) == 1
        assert post_calls[-1][2] == {
            "labels": ["gig-discovery", "risk-low", "payment-issue"],
        }

    def test_remove_leaves_others(self, chatwoot_env):
        def fake_api(method, path, body=None):
            if method == "GET" and "conversations" in path:
                return True, {"payload": ["payment-issue", "risk-low", "human-tag"]}, ""
            if method == "POST" and "conversations" in path:
                return True, {"payload": body["labels"]}, ""
            if method == "GET":
                return True, {"payload": [{"title": "payment-issue"}]}, ""
            return False, None, "unexpected"

        with patch.object(t, "_api_request", side_effect=fake_api):
            out = t._assign_labels("1", "42", add=[], remove=["payment-issue"])

        assert out["success"] is True
        assert out["labels"] == ["risk-low", "human-tag"]

    def test_add_and_remove_one_post(self, chatwoot_env):
        calls = []

        def fake_api(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET" and "conversations" in path:
                return True, {"payload": ["app-help", "risk-medium"]}, ""
            if method == "POST" and "conversations" in path:
                return True, {"payload": body["labels"]}, ""
            if method == "GET":
                return True, {"payload": [{"title": "payment-issue"}]}, ""
            return False, None, "unexpected"

        with patch.object(t, "_api_request", side_effect=fake_api):
            out = t._assign_labels(
                "1", "42", add=["payment-issue"], remove=["app-help"]
            )

        assert out["success"] is True
        assert out["labels"] == ["risk-medium", "payment-issue"]
        post_calls = [c for c in calls if c[0] == "POST" and "conversations" in c[1]]
        assert len(post_calls) == 1

    def test_empty_add_and_remove_error(self, chatwoot_env):
        out = t._assign_labels("1", "42", add=[], remove=[])
        assert out["success"] is False

    def test_post_422_returns_error(self, chatwoot_env):
        def fake_api(method, path, body=None):
            if method == "GET" and "conversations" in path:
                return True, {"payload": []}, ""
            if method == "POST" and "conversations" in path:
                return False, {"message": "Invalid labels: fake-label"}, "HTTP 422"
            if method == "GET":
                return True, {"payload": [{"title": "payment-issue"}]}, ""
            return False, None, "unexpected"

        with patch.object(t, "_api_request", side_effect=fake_api):
            out = t._assign_labels("1", "42", add=["fake-label"], remove=[])

        assert out["success"] is False
        assert "422" in out["error"]


class TestHandler:
    def test_noop_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("CHATWOOT_BASE_URL", raising=False)
        out = json.loads(t.chatwoot_labels_tool({"action": "assign_labels", "add": ["a"]}))
        assert out["success"] is False
        assert out["error"] is None

    def test_invalid_action(self, chatwoot_env):
        out = json.loads(t.chatwoot_labels_tool({"action": "bogus"}))
        assert out["success"] is False
        assert "action must be" in out["error"]

    def test_get_all_labels(self, chatwoot_env):
        with patch.object(
            t,
            "_get_all_labels",
            return_value={"success": True, "labels": ["a"], "error": None},
        ):
            out = json.loads(
                t.chatwoot_labels_tool({"action": "get_all_labels", "account_id": "1"})
            )
        assert out["success"] is True
        assert out["labels"] == ["a"]

    def test_get_conversation_labels(self, chatwoot_env):
        with patch.object(t, "_resolve_conversation", return_value=("1", "42")), patch.object(
            t,
            "_get_conversation_labels",
            return_value={"success": True, "labels": ["payment-issue"], "error": None},
        ):
            out = json.loads(
                t.chatwoot_labels_tool({"action": "get_conversation_labels"})
            )
        assert out["success"] is True
        assert out["labels"] == ["payment-issue"]

    def test_assign_no_conversation(self, chatwoot_env):
        with patch.object(t, "_resolve_conversation", return_value=(None, None)):
            out = json.loads(
                t.chatwoot_labels_tool(
                    {"action": "assign_labels", "add": ["payment-issue"]}
                )
            )
        assert out["success"] is False
        assert "No current Chatwoot conversation" in out["reason"]

    def test_assign_success(self, chatwoot_env):
        with patch.object(t, "_resolve_conversation", return_value=("1", "42")), patch.object(
            t,
            "_assign_labels",
            return_value={"success": True, "labels": ["payment-issue"], "error": None},
        ) as assign:
            out = json.loads(
                t.chatwoot_labels_tool(
                    {
                        "action": "assign_labels",
                        "add": ["payment-issue"],
                        "remove": ["app-help"],
                    }
                )
            )
        assert out["success"] is True
        assign.assert_called_once_with("1", "42", ["payment-issue"], ["app-help"])

    def test_assign_requires_add_or_remove(self, chatwoot_env):
        with patch.object(t, "_resolve_conversation", return_value=("1", "42")):
            out = json.loads(t.chatwoot_labels_tool({"action": "assign_labels"}))
        assert out["success"] is False
        assert "add and/or remove" in out["error"]

    def test_assign_rejects_non_list_add(self, chatwoot_env):
        with patch.object(t, "_resolve_conversation", return_value=("1", "42")):
            out = json.loads(
                t.chatwoot_labels_tool({"action": "assign_labels", "add": "payment-issue"})
            )
        assert out["success"] is False
        assert "add" in out["error"]
