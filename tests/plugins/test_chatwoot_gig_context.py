"""Tests for Chatwoot gig context prefetch hook."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from plugins.platforms.chatwoot import coach_context as cc
from plugins.platforms.chatwoot import gig_context as gc


@pytest.fixture(autouse=True)
def _reset():
    cc._reset_cache()
    cc.reset_cross_user_request()
    yield
    cc._reset_cache()
    cc.reset_cross_user_request()


class TestIntentDetection:
    @pytest.mark.parametrize("msg", [
        "what are my next steps?",
        "what's my status?",
        "my gigs",
        "how is Pul Tool going?",
        "what are target store gigs",
        "show me gigs",
    ])
    def test_gig_intent_matches(self, msg):
        assert gc.should_prefetch_gig_context(msg) is True

    @pytest.mark.parametrize("msg", [
        "what active gigs can I join?",
        "show me open gigs",
        "any available gigs?",
        "What gigs are available right now?",
    ])
    def test_available_scope_does_not_prefetch_enrolled(self, msg):
        assert gc.should_prefetch_gig_context(msg) is False

    def test_generic_identity_does_not_match(self):
        assert gc.should_prefetch_gig_context("who are you?") is False

    def test_part_of_classifies_as_enrolled(self):
        from plugins.platforms.chatwoot import gig_intent as gi

        assert gi.classify_gig_scope("what gigs am i part of?") == "enrolled"


class TestGigContextHook:
    def test_skips_non_chatwoot(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        assert gc.gig_context_hook(
            platform="telegram",
            sender_id="55",
            user_message="what are my next steps?",
        ) is None

    def test_skips_without_mongo_uri(self, monkeypatch):
        monkeypatch.delenv("CRWD_MONGO_URI", raising=False)
        assert gc.gig_context_hook(
            platform="chatwoot",
            sender_id="55",
            user_message="what are my next steps?",
        ) is None

    def test_skips_non_gig_message(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        assert gc.gig_context_hook(
            platform="chatwoot",
            sender_id="55",
            user_message="who are you?",
        ) is None

    def test_injects_context_on_intent(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        payload = {
            "_type": "user_gig_status",
            "items": [{
                "gig_id": "g1",
                "gig_name": "Pul Tool",
                "gig_type": "web",
                "stage": "receipt_review",
                "next_step": "Receipt under review.",
                "buy_link": None,
                "handoff_recommended": False,
            }],
        }
        with patch.object(gc, "resolve_member_crwd_id", return_value="user1"), patch(
            "tools.crwd_db_tool.build_user_gig_status",
            return_value=payload,
        ):
            out = gc.gig_context_hook(
                platform="chatwoot",
                sender_id="55",
                user_message="what are my next steps?",
            )
        assert out is not None
        assert "[CRWD gig context]" in out["context"]
        assert "Pul Tool" in out["context"]
        assert "receipt_review" in out["context"]

    def test_skips_cross_user_turn(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        cc._cross_user_request.set(True)
        assert gc.gig_context_hook(
            platform="chatwoot",
            sender_id="55",
            user_message="what are my next steps?",
        ) is None

    def test_ambiguous_injects_guidance(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        payload = {
            "_type": "user_gig_status",
            "items": [{
                "gig_id": "g1",
                "gig_name": "Target Store Promo",
                "gig_type": "irl",
                "stage": "purchase",
                "next_step": "Buy the product at Target.",
                "buy_link": None,
                "handoff_recommended": False,
            }],
        }
        with patch.object(gc, "resolve_member_crwd_id", return_value="user1"), patch(
            "tools.crwd_db_tool.build_user_gig_status",
            return_value=payload,
        ):
            out = gc.gig_context_hook(
                platform="chatwoot",
                sender_id="55",
                user_message="what are target store gigs",
            )
        assert out is not None
        assert "[CRWD gig context]" in out["context"]
        assert "[Gig intent guidance]" in out["context"]
        assert "target store" in out["context"].lower()

    def test_available_message_injects_open_gigs(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        payload = {
            "_type": "gig_list",
            "items": [{
                "_id": "g2",
                "name": "Tide Pods Gig",
                "effective_payout": 18,
            }],
            "error": None,
            "has_more": False,
            "total": 1,
        }
        with patch.object(gc, "resolve_member_crwd_id", return_value="user1"), patch(
            "tools.crwd_db_tool.fetch_active_gigs",
            return_value=payload,
        ):
            out = gc.gig_context_hook(
                platform="chatwoot",
                sender_id="55",
                user_message="What gigs are available right now?",
            )
        assert out is not None
        assert "[CRWD available gigs context]" in out["context"]
        assert "[CRWD gig context]" not in out["context"]
        assert "Tide Pods Gig" in out["context"]
        assert "get_user_gigs" in out["context"]

    def test_available_active_gigs_message_injects_open_gigs(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        payload = {"_type": "gig_list", "items": [], "error": None, "has_more": False}
        with patch.object(gc, "resolve_member_crwd_id", return_value="user1"), patch(
            "tools.crwd_db_tool.fetch_active_gigs",
            return_value=payload,
        ):
            out = gc.gig_context_hook(
                platform="chatwoot",
                sender_id="55",
                user_message="what active gigs can I join?",
            )
        assert out is not None
        assert "[CRWD available gigs context]" in out["context"]
        assert "[CRWD gig context]" not in out["context"]

    def test_ambiguous_without_enrolled_still_injects_guidance(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        payload = {"_type": "user_gig_status", "items": []}
        with patch.object(gc, "resolve_member_crwd_id", return_value="user1"), patch(
            "tools.crwd_db_tool.build_user_gig_status",
            return_value=payload,
        ):
            out = gc.gig_context_hook(
                platform="chatwoot",
                sender_id="55",
                user_message="what are target store gigs",
            )
        assert out is not None
        assert "[Gig intent guidance]" in out["context"]
