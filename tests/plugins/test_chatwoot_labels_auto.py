"""Tests for Chatwoot labeling context hook (no auto-classifier)."""

from unittest.mock import patch

from plugins.platforms.chatwoot import labels_auto as auto
from plugins.platforms.chatwoot.labels import (
    APPLIED_LABEL_TITLES,
    UNAPPLIED_LABEL_TITLES,
)


class TestTaxonomySets:
    def test_applied_and_unapplied_disjoint(self):
        assert APPLIED_LABEL_TITLES.isdisjoint(UNAPPLIED_LABEL_TITLES)

    def test_owned_titles_are_applied(self):
        for title in (
            "payment-issue",
            "app-help",
            "new-user",
            "proof-acceptance",
            "proof-rejection",
            "gig-complete",
            "handoff-escalation",
            "scam",
        ):
            assert title in APPLIED_LABEL_TITLES
            assert title not in UNAPPLIED_LABEL_TITLES

    def test_unapplied_excludes_scam(self):
        assert "scam" not in UNAPPLIED_LABEL_TITLES
        for title in (
            "mid-gig-support",
            "proof-submission",
            "gig-discovery",
            "general-inquiry",
            "off-topic",
        ):
            assert title in UNAPPLIED_LABEL_TITLES


class TestConversationStatus:
    def test_reads_webhook_status(self):
        with patch(
            "plugins.platforms.chatwoot.coach_context.webhook_conversation_status",
            return_value="pending",
        ):
            assert auto._conversation_status() == "pending"

    def test_blank_is_none(self):
        with patch(
            "plugins.platforms.chatwoot.coach_context.webhook_conversation_status",
            return_value="  ",
        ):
            assert auto._conversation_status() is None

    def test_lookup_failure_is_none(self):
        with patch(
            "plugins.platforms.chatwoot.coach_context.webhook_conversation_status",
            side_effect=RuntimeError("boom"),
        ):
            assert auto._conversation_status() is None


class TestLabelContextHook:
    def test_skips_non_chatwoot(self):
        assert auto.chatwoot_label_context_hook(platform="telegram") is None

    def test_skips_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(auto, "check_chatwoot_labels_requirements", lambda: False)
        assert auto.chatwoot_label_context_hook(platform="chatwoot") is None

    def test_injects_status_and_skill_pointer(self, monkeypatch):
        monkeypatch.setattr(auto, "check_chatwoot_labels_requirements", lambda: True)
        monkeypatch.setattr(auto, "_conversation_status", lambda: "open")
        out = auto.chatwoot_label_context_hook(platform="chatwoot")
        assert out is not None
        assert "status: open" in out["context"]
        assert "chatwoot-conversation-labels" in out["context"]
        assert "never replace" in out["context"]

    def test_unknown_status(self, monkeypatch):
        monkeypatch.setattr(auto, "check_chatwoot_labels_requirements", lambda: True)
        monkeypatch.setattr(auto, "_conversation_status", lambda: None)
        out = auto.chatwoot_label_context_hook(platform="chatwoot")
        assert out is not None
        assert "status: unknown" in out["context"]
