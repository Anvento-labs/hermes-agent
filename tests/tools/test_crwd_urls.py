"""Tests for CRWD gig page URL and inline name helpers."""

from __future__ import annotations

from tools.crwd_urls import (
    attach_gig_url,
    build_gig_page_url,
    crwd_app_base_url,
    format_gig_name_link,
    normalize_gig_id,
)


class TestCrwdUrls:
    def test_build_gig_page_url_without_base(self, monkeypatch):
        monkeypatch.delenv("CRWD_APP_BASE_URL", raising=False)
        assert build_gig_page_url("674abc1234567890abcdef12") is None

    def test_build_gig_page_url_with_base(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com/")
        url = build_gig_page_url("674abc1234567890abcdef12")
        assert url == "https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12"

    def test_normalize_gig_id_from_oid_dict(self):
        assert normalize_gig_id({"$oid": "674abc1234567890abcdef12"}) == "674abc1234567890abcdef12"

    def test_format_gig_name_link(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        link = format_gig_name_link("CRWD Cohort - Target", "674abc1234567890abcdef12")
        assert link == (
            "[CRWD Cohort - Target]"
            "(https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12)"
        )

    def test_format_gig_name_link_without_base(self, monkeypatch):
        monkeypatch.delenv("CRWD_APP_BASE_URL", raising=False)
        assert format_gig_name_link("Pul Tool", "674abc1234567890abcdef12") is None

    def test_attach_gig_url_with_name_field(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        item = attach_gig_url({"_id": "674abc1234567890abcdef12", "name": "Pul Tool"})
        assert item["gig_url"] == "https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12"
        assert item["name_link"] == (
            "[Pul Tool](https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12)"
        )
        assert item["name"] == "Pul Tool"

    def test_attach_gig_url_with_gig_name_field(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        item = attach_gig_url({
            "gig_id": "674abc1234567890abcdef12",
            "gig_name": "CRWD Cohort - Target",
        })
        assert item["name_link"] == (
            "[CRWD Cohort - Target]"
            "(https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12)"
        )
        assert item["gig_name"] == "CRWD Cohort - Target"

    def test_attach_gig_url_skips_name_link_without_name(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        item = attach_gig_url({"_id": "674abc1234567890abcdef12"})
        assert "gig_url" in item
        assert "name_link" not in item

    def test_attach_gig_url_without_base(self, monkeypatch):
        monkeypatch.delenv("CRWD_APP_BASE_URL", raising=False)
        item = attach_gig_url({"_id": "674abc1234567890abcdef12", "name": "Pul Tool"})
        assert "gig_url" not in item
        assert "name_link" not in item

    def test_crwd_app_base_url_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com///")
        assert crwd_app_base_url() == "https://app.crwd.example.com"


class TestAttachGigUrlInlineName:
    def test_inline_name_replaces_name_and_sets_plain(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        item = attach_gig_url(
            {"_id": "674abc1234567890abcdef12", "name": "Pul Tool"},
            inline_name=True,
        )
        assert item["name_plain"] == "Pul Tool"
        assert item["name"] == (
            "[Pul Tool](https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12)"
        )
        assert "name_link" not in item

    def test_inline_name_replaces_gig_name_and_sets_plain(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        item = attach_gig_url(
            {
                "gig_id": "674abc1234567890abcdef12",
                "gig_name": "CRWD Cohort - Target",
            },
            inline_name=True,
        )
        assert item["gig_name_plain"] == "CRWD Cohort - Target"
        assert item["gig_name"] == (
            "[CRWD Cohort - Target]"
            "(https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12)"
        )
        assert "name_link" not in item

    def test_inline_name_rewrites_next_step(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        link = "[Pul Tool](https://app.crwd.example.com/my-gigs/674abc1234567890abcdef12)"
        item = attach_gig_url(
            {
                "gig_id": "674abc1234567890abcdef12",
                "gig_name": "Pul Tool",
                "next_step": "You're in Pul Tool — next, buy the product.",
            },
            inline_name=True,
        )
        assert item["next_step"] == f"You're in {link} — next, buy the product."

    def test_inline_name_without_base_leaves_name_plain(self, monkeypatch):
        monkeypatch.delenv("CRWD_APP_BASE_URL", raising=False)
        item = attach_gig_url(
            {"_id": "674abc1234567890abcdef12", "name": "Pul Tool"},
            inline_name=True,
        )
        assert item["name"] == "Pul Tool"
        assert "name_plain" not in item
        assert "gig_url" not in item

    def test_build_gig_list_markdown(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", "https://app.crwd.example.com")
        item = attach_gig_url(
            {"_id": "674abc1234567890abcdef12", "name": "Pul Tool", "effective_payout": 10},
            inline_name=True,
        )
        from tools.crwd_urls import build_gig_list_markdown

        md = build_gig_list_markdown([item])
        assert md.startswith("- [Pul Tool](https://app.crwd.example.com/my-gigs/")
        assert "— $10" in md
