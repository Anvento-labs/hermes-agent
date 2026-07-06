"""Tests for scoped Chatwoot gig title linking."""

from __future__ import annotations

import json

import pytest

from plugins.platforms.chatwoot import gig_links as gl


@pytest.fixture(autouse=True)
def _reset_registry():
    gl.reset_turn_registry()
    yield
    gl.reset_turn_registry()


class TestScopedGigLinks:
    def test_bullet_title_linked_body_store_name_stays_plain(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        gl.record_name_link(
            "Try Pul Tool Free",
            "[Try Pul Tool Free](https://app.crwd.example.com/my-gigs/pul1)",
        )
        text = "- Try Pul Tool Free — $10 | Walmart in-store | Ends Aug 16"
        out = gl.apply_scoped_gig_links(text, gl._registry())
        assert "[Try Pul Tool Free](https://app.crwd.example.com/my-gigs/pul1)" in out
        assert "Walmart in-store" in out
        assert out.count("](https://app.crwd.example.com/my-gigs/") == 1

    def test_comma_list_becomes_linked_bullets(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        names = [
            ("Self Obsessed - Supplement", "s1"),
            ("Heart of Steel - Supplement", "s2"),
            ("The Night Before - Supplement", "s3"),
        ]
        for name, gid in names:
            gl.record_name_link(name, f"[{name}](https://app.crwd.example.com/my-gigs/{gid})")
        line = "Self Obsessed, Heart of Steel, The Night Before"
        out = gl.apply_scoped_gig_links(line, gl._registry())
        assert out.count("](https://app.crwd.example.com/my-gigs/") == 3
        assert out.startswith("- [")

    def test_user_reply_shape(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        registry_entries = {
            "MyFutureSelf - UGC Videos": "m1",
            "Raley's Supermarket UGC": "r1",
            "Self Obsessed - Supplement": "s1",
            "Heart of Steel - Supplement": "s2",
            "The Night Before - Supplement": "s3",
            "Crown of Glory - Supplement": "c1",
            "Boss Mode - Supplement": "b1",
            "She's Obsessed - Supplement": "sh1",
            "Gut Intellect - Supplement": "g1",
            "Shroom Vroom - Supplement": "sr1",
            "Primal Power - Supplement": "p1",
            "Self Obsessed Maxed - Supplement": "sm1",
        }
        for name, gid in registry_entries.items():
            gl.record_name_link(name, f"[{name}](https://app.crwd.example.com/my-gigs/{gid})")

        text = """Top payers:
- MyFutureSelf - UGC Videos — $50, post TikTok + Instagram Reels about the app
- Raley's Supermarket UGC — $50, buy Celzo drinks at Raley's + post a video (in-store, US only)

Amazon supplement gigs — $10 each (order, try, leave a review):
Self Obsessed, Heart of Steel, The Night Before, Crown of Glory, Boss Mode, She's Obsessed, Gut Intellect, Shroom Vroom, Primal Power, Self Obsessed Maxed"""

        out = gl.apply_scoped_gig_links(text, gl._registry())
        assert "[MyFutureSelf - UGC Videos](https://app.crwd.example.com/my-gigs/m1)" in out
        assert "[Raley's Supermarket UGC](https://app.crwd.example.com/my-gigs/r1)" in out
        assert out.count("](https://app.crwd.example.com/my-gigs/") >= 10
        assert "at Raley's + post" in out

    def test_ingest_gig_list_markdown(self):
        md = "- [Self Obsessed - Supplement](https://app.crwd.example.com/my-gigs/s1) — $10"
        gl._ingest_gig_list_markdown(md)
        assert gl._resolve_title_to_link("Self Obsessed", gl._registry()) is not None

    def test_header_paren_commas_not_treated_as_gig_list(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        gl.record_name_link(
            "Self Obsessed - Supplement",
            "[Self Obsessed - Supplement](https://app.crwd.example.com/my-gigs/s1)",
        )
        text = (
            "Amazon supplement gigs — $10 each (order, try, leave a review):\n"
            "Self Obsessed, Heart of Steel"
        )
        out = gl.apply_scoped_gig_links(text, gl._registry())
        assert "(order, try, leave a review):" in out
        assert "- order\n- try" not in out

    def test_normalized_db_title_links_paraphrase(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        db_name = "The Self Obsessed Supplement Gig"
        gl.record_name_link(
            db_name,
            f"[{db_name}](https://app.crwd.example.com/my-gigs/s1)",
        )
        assert gl._resolve_title_to_link("Self Obsessed", gl._registry()) is not None
