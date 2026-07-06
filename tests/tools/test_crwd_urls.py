"""Tests for CRWD gig page URL and name_link helpers."""

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
