"""Unit tests for Chatwoot campaign-code token detection."""

from plugins.platforms.chatwoot.campaign_code import looks_like_campaign_code


def test_opaque_tokens_match():
    for token in ("FRGP", "AAAB", "ROGUETT", "RoGUEtt", "aaapr"):
        assert looks_like_campaign_code(token) is True


def test_chat_and_yes_no_are_not_codes():
    for token in ("yes", "ok", "help", "what's available?", "Rogue gig", "hi"):
        assert looks_like_campaign_code(token) is False
