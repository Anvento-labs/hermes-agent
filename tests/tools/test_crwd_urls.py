"""Tests for tools.crwd_urls — gig deep-link helpers."""

from __future__ import annotations

import tools.crwd_urls as cu


GIG_ID = "6a3411008972fa2d14ce8fe0"
BASE = "https://live-staging.joincrwd.com"
URL = f"{BASE}/my-gigs/{GIG_ID}"
LINKED = f"[Summer Skincare Bundle]({URL})"


class TestNormalizeGigId:
    def test_plain_hex(self):
        assert cu.normalize_gig_id(GIG_ID) == GIG_ID

    def test_oid_dict(self):
        assert cu.normalize_gig_id({"$oid": GIG_ID}) == GIG_ID

    def test_invalid(self):
        assert cu.normalize_gig_id("not-an-id") == ""
        assert cu.normalize_gig_id(None) == ""
        assert cu.normalize_gig_id({}) == ""


class TestGigPageUrl:
    def test_builds_my_gigs_path(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", BASE)
        assert cu.gig_page_url(GIG_ID) == URL

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", BASE + "/")
        assert cu.gig_page_url(GIG_ID) == URL

    def test_missing_base(self, monkeypatch):
        monkeypatch.delenv("CRWD_APP_BASE_URL", raising=False)
        assert cu.gig_page_url(GIG_ID) is None

    def test_never_explore(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", BASE)
        url = cu.gig_page_url(GIG_ID)
        assert url is not None
        assert "/explore/" not in url
        assert "/my-gigs/" in url


class TestAttachGigUrl:
    def test_inline_name_becomes_markdown(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", BASE)
        item = cu.attach_gig_url({"_id": GIG_ID, "name": "Summer Skincare Bundle"})
        assert item["gig_url"] == URL
        assert item["name_plain"] == "Summer Skincare Bundle"
        assert item["name"] == LINKED

    def test_inline_gig_name(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", BASE)
        item = cu.attach_gig_url({
            "gig_id": GIG_ID,
            "gig_name": "Pul Tool",
            "next_step": "Buy the product for Pul Tool at Walmart.",
        })
        assert item["gig_name_plain"] == "Pul Tool"
        assert item["gig_name"] == f"[Pul Tool]({URL})"
        assert item["next_step"] == (
            f"Buy the product for [Pul Tool]({URL}) at Walmart."
        )

    def test_missing_base_leaves_names(self, monkeypatch):
        monkeypatch.delenv("CRWD_APP_BASE_URL", raising=False)
        item = cu.attach_gig_url({"_id": GIG_ID, "name": "Plain Title"})
        assert item["name"] == "Plain Title"
        assert "gig_url" not in item
        assert "name_plain" not in item

    def test_does_not_double_wrap_markdown(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", BASE)
        item = cu.attach_gig_url({"_id": GIG_ID, "name": LINKED})
        assert item["name"] == LINKED
        assert item["name_plain"] == "Summer Skincare Bundle"

    def test_upgrades_bare_url_when_plain_present(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", BASE)
        item = cu.attach_gig_url({
            "_id": GIG_ID,
            "name": URL,
            "name_plain": "Summer Skincare Bundle",
        })
        assert item["name"] == LINKED

    def test_inline_name_false_sets_url_only(self, monkeypatch):
        monkeypatch.setenv("CRWD_APP_BASE_URL", BASE)
        item = cu.attach_gig_url(
            {"_id": GIG_ID, "name": "Keep Plain"},
            inline_name=False,
        )
        assert item["name"] == "Keep Plain"
        assert item["gig_url"] == URL
        assert "name_plain" not in item
