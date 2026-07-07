"""Tests for Chatwoot gig intent classification."""

from __future__ import annotations

import pytest

from plugins.platforms.chatwoot import gig_intent as gi


class TestClassifyGigScope:
    @pytest.mark.parametrize("msg", [
        "what are my next steps?",
        "my gigs",
        "what's my status?",
        "show my joined gigs",
        "gigs i am in",
    ])
    def test_explicit_enrolled(self, msg):
        assert gi.classify_gig_scope(msg) == "enrolled"

    @pytest.mark.parametrize("msg", [
        "what active gigs can I join?",
        "show me open gigs",
        "any available gigs?",
        "what can i join",
        "how do i apply",
        "browse gigs near me",
    ])
    def test_explicit_available(self, msg):
        assert gi.classify_gig_scope(msg) == "available"

    @pytest.mark.parametrize("msg", [
        "what are target store gigs",
        "show me gigs",
        "what gigs",
        "amazon gigs",
        "target store gigs",
    ])
    def test_ambiguous(self, msg):
        assert gi.classify_gig_scope(msg) == "ambiguous"

    def test_non_gig_returns_none(self):
        assert gi.classify_gig_scope("who are you?") is None
        assert gi.classify_gig_scope("hello there") is None

    def test_history_overrides_to_available(self):
        history = [
            {"role": "user", "content": "what can i join"},
            {"role": "assistant", "content": "Here are some open gigs..."},
        ]
        assert gi.classify_gig_scope("target store ones", history) == "available"

    def test_history_overrides_to_enrolled(self):
        history = [
            {"role": "user", "content": "my gigs"},
            {"role": "assistant", "content": "You have 2 active gigs..."},
        ]
        assert gi.classify_gig_scope("target store ones", history) == "enrolled"


class TestExtractGigQueryHint:
    def test_what_are_store_gigs(self):
        assert gi.extract_gig_query_hint("what are target store gigs") == "target store"

    def test_tell_me_about_gig(self):
        assert gi.extract_gig_query_hint("tell me about Summer Skincare gig") == "Summer Skincare"

    def test_target_store_phrase(self):
        assert gi.extract_gig_query_hint("target store gigs") == "target store"


class TestStitchUserText:
    def test_includes_recent_user_turns(self):
        history = [
            {"role": "user", "content": "what can i join"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "earlier question"},
        ]
        text = gi.stitch_user_text("target store", history)
        assert "target store" in text
        assert "what can i join" in text
