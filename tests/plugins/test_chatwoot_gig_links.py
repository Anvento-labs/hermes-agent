"""Tests for Chatwoot name_link application in replies."""

from __future__ import annotations

import json

import pytest

from plugins.platforms.chatwoot import gig_links as gl


@pytest.fixture(autouse=True)
def _reset_registry():
    gl.reset_turn_registry()
    yield
    gl.reset_turn_registry()


class TestNameAliases:
    def test_dash_collapsed_alias(self):
        aliases = gl._name_aliases("Self Obsessed - Supplement")
        assert "Self Obsessed Supplement" in aliases


class TestApplyNameLinksInText:
    def test_plain_title_becomes_name_link(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        link = "[Review a Gym on Yelp](https://app.crwd.example.com/my-gigs/abc123)"
        out = gl.apply_name_links_in_text(
            "- Review a Gym on Yelp — $5, write an honest Yelp review",
            {"Review a Gym on Yelp": link},
        )
        assert out.startswith(f"- {link} — $5")

    def test_dash_alias_matches_paraphrased_title(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        link = "[Self Obsessed - Supplement](https://app.crwd.example.com/my-gigs/id1)"
        out = gl.apply_name_links_in_text(
            "- Self Obsessed Supplement — $10, buy on Amazon",
            {"Self Obsessed - Supplement": link, "Self Obsessed Supplement": link},
        )
        assert link in out
        assert "Self Obsessed Supplement —" in out or link in out

    def test_product_markdown_link_unchanged(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        link = "[Self Obsessed](https://app.crwd.example.com/my-gigs/id1)"
        original = "[Self Obsessed](https://amazon.com/dp/123)"
        assert gl.apply_name_links_in_text(original, {"Self Obsessed": link}) == original

    def test_mixed_plain_title_and_product_link(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        link = "[Self Obsessed - Supplement](https://app.crwd.example.com/my-gigs/id1)"
        original = "Self Obsessed Supplement — [buy on Amazon](https://amazon.com/dp/123)"
        out = gl.apply_name_links_in_text(
            original,
            {"Self Obsessed - Supplement": link, "Self Obsessed Supplement": link},
        )
        assert link in out
        assert "[buy on Amazon](https://amazon.com/dp/123)" in out

    def test_user_example_bullet_list(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        links = {
            "Review a Gym on Yelp": "[Review a Gym on Yelp](https://app.crwd.example.com/my-gigs/a1)",
            "Self Obsessed Supplement": "[Self Obsessed - Supplement](https://app.crwd.example.com/my-gigs/a2)",
            "Heart of Steel Supplement": "[Heart of Steel - Supplement](https://app.crwd.example.com/my-gigs/a3)",
            "The Night Before Supplement": "[The Night Before - Supplement](https://app.crwd.example.com/my-gigs/a4)",
            "Crown of Glory Supplement": "[Crown of Glory - Supplement](https://app.crwd.example.com/my-gigs/a5)",
        }
        text = (
            "- Review a Gym on Yelp — $5, write an honest Yelp review\n"
            "- Self Obsessed Supplement — $10, buy on Amazon, try it, leave a review\n"
            "- Heart of Steel Supplement — $10, same deal\n"
            "- The Night Before Supplement — $10\n"
            "- Crown of Glory Supplement — $10"
        )
        out = gl.apply_name_links_in_text(text, links)
        assert out.count("](https://app.crwd.example.com/my-gigs/") == 5


class TestRegistryAndHooks:
    def test_record_links_from_payload(self):
        payload = {
            "_type": "gig_list",
            "items": [{
                "_id": "674abc1234567890abcdef12",
                "name": "Self Obsessed - Supplement",
                "name_link": "[Self Obsessed - Supplement](https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12)",
            }],
        }
        gl.record_links_from_payload(payload)
        reg = gl._registry()
        assert "Self Obsessed - Supplement" in reg
        assert "Self Obsessed Supplement" in reg

    def test_record_tool_links_hook(self):
        result = json.dumps({
            "_type": "gig_list",
            "items": [{
                "name": "Pul Tool",
                "name_link": "[Pul Tool](https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12)",
            }],
        })
        gl.record_tool_links_hook(
            platform="chatwoot",
            tool_name="crwd_db",
            result=result,
        )
        assert "Pul Tool" in gl._registry()

    def test_apply_hook_passes_without_base_url(self, monkeypatch):
        monkeypatch.delenv("CRWD_APP_BASE_URL", raising=False)
        gl.record_name_link("Pul Tool", "[Pul Tool](https://app.crwd.example.com/my-gigs/x)")
        assert gl.apply_name_links_hook(
            platform="chatwoot",
            response_text="Try Pul Tool today.",
        ) is None

    def test_apply_hook_links_on_chatwoot(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        link = "[Pul Tool](https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12)"
        gl.record_name_link("Pul Tool", link)
        out = gl.apply_name_links_hook(
            platform="chatwoot",
            response_text="- Pul Tool — $10",
        )
        assert out is not None
        assert link in out
