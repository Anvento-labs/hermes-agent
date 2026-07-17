"""Tests for the crwd_db tool module (no live database required)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools import crwd_db_tool as t


class TestAvailability:
    def test_unavailable_without_uri(self, monkeypatch):
        monkeypatch.delenv("CRWD_MONGO_URI", raising=False)
        monkeypatch.delenv("MONGODB_URI", raising=False)
        assert t.check_crwd_db_requirements() is False

    def test_available_with_uri(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://localhost:27017/")
        assert t.check_crwd_db_requirements() is True

    def test_legacy_mongodb_uri_bridges(self, monkeypatch):
        monkeypatch.delenv("CRWD_MONGO_URI", raising=False)
        monkeypatch.setenv("MONGODB_URI", "mongodb://legacy:27017/")
        assert t.check_crwd_db_requirements() is True
        assert t._resolve_mongo_uri() == "mongodb://legacy:27017/"

    def test_handler_errors_without_uri(self, monkeypatch):
        monkeypatch.delenv("CRWD_MONGO_URI", raising=False)
        out = json.loads(t.crwd_db_tool({"action": "list_active_gigs"}))
        assert "error" in out


class TestNormalizeAndScore:
    def test_normalize_strips_noise_words(self):
        assert t._normalize("The Self Obsessed Supplement Gig") == "self obsessed"

    def test_normalize_falls_back_when_all_noise(self):
        # If every token is a noise word, keep the original tokens rather than "".
        assert t._normalize("the a an") == "the a an"

    def test_exact_name_scores_high(self):
        q = t._normalize("self obsessed")
        assert t._score(q, "Self Obsessed - Supplement") >= 0.9

    def test_partial_name_beats_unrelated(self):
        q = t._normalize("self obsessed")
        strong = t._score(q, "Self Obsessed Maxed - Supplement")
        weak = t._score(q, "Review a Gym on Yelp")
        assert strong > weak

    def test_garbage_scores_below_floor(self):
        q = t._normalize("zzzqqq nonexistent xyzzy")
        assert t._score(q, "Self Obsessed - Supplement") < t._MATCH_FLOOR

    def test_description_match_boosts(self):
        q = t._normalize("testosterone")
        assert t._score(q, "Random Gig", "boosts testosterone support") >= 0.5

    def test_empty_query_scores_zero(self):
        assert t._score("", "anything") == 0.0


class TestGuardHelpers:
    def test_has_where_top_level(self):
        assert t._has_where({"$where": "1==1"}) is True

    def test_has_where_nested(self):
        assert t._has_where({"a": {"b": {"$where": "x"}}}) is True

    def test_has_where_in_list(self):
        assert t._has_where({"$or": [{"x": 1}, {"$where": "y"}]}) is True

    def test_has_where_absent(self):
        assert t._has_where({"city": "Austin", "isDeleted": {"$ne": True}}) is False

    def test_redact_secrets_drops_secrets(self):
        doc = {
            "email": "a@b.com", "password": "hash", "emailOTP": "123",
            "emailForgotPasswordVerifyToken": "tok", "resetSecret": "s",
        }
        red = t._redact_secrets(doc)
        assert red == {"email": "a@b.com"}

    def test_redact_secrets_recurses(self):
        doc = {"nested": {"token": "x", "keep": 1}}
        assert t._redact_secrets(doc) == {"nested": {"keep": 1}}

    def test_redact_secrets_drops_notification_tokens(self):
        doc = {"title": "hi", "deviceToken": "d", "webDeviceToken": "w", "chat_token": "c"}
        assert t._redact_secrets(doc) == {"title": "hi"}

    def test_id_values_objectid_and_string(self):
        vals = t._id_values("69a72d9b2109705cc0224a35")
        assert len(vals) == 2 and "69a72d9b2109705cc0224a35" in vals

    def test_id_values_plain_string_only(self):
        assert t._id_values("6a33bb6003b1c0cc31a7baa5x") == ["6a33bb6003b1c0cc31a7baa5x"]

    def test_effective_payout_prefers_top_level(self):
        assert t._effective_payout({"payout": 25, "gig_stores": [{"payout_amount": 5}]}) == 25

    def test_effective_payout_falls_back_to_stores(self):
        gig = {"payout": 0, "gig_stores": [{"payout_amount": 3}, {"payout_amount": 7}]}
        assert t._effective_payout(gig) == 7

    def test_effective_payout_no_stores(self):
        assert t._effective_payout({"payout": 0}) == 0


class TestOid:
    def test_valid_24_hex(self):
        assert t._oid("69a72d9b2109705cc0224a35") is not None

    def test_invalid_returns_none(self):
        assert t._oid("not-an-id") is None
        assert t._oid("") is None


def _fake_db(collections):
    """Return a fake db mapping so _db()[name] yields the given collection mock."""
    db = MagicMock()
    db.__getitem__.side_effect = lambda name: collections[name]
    return db


class TestCustomQueryGuardrails:
    def test_disallowed_collection(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({
            "action": "custom_query", "collection": "orders", "operation": "find",
        }))
        assert "error" in out and "collection" in out["error"]

    def test_bad_operation(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({
            "action": "custom_query", "collection": "crwds", "operation": "aggregate",
        }))
        assert "error" in out

    def test_where_rejected(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({
            "action": "custom_query", "collection": "crwds", "operation": "find",
            "filter": {"$where": "1==1"},
        }))
        assert out["error"] == "$where is not allowed"

    def test_limit_capped_at_20(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        cursor = MagicMock()
        coll.find.return_value = cursor
        cursor.limit.return_value = []
        with patch.object(t, "_db", return_value=_fake_db({"crwds": coll})):
            t.crwd_db_tool({
                "action": "custom_query", "collection": "crwds", "operation": "find",
                "limit": 9999,
            })
        cursor.limit.assert_called_once_with(t._HARD_LIMIT)

    def test_users_projection_redacted(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        cursor = MagicMock()
        coll.find.return_value = cursor
        cursor.limit.return_value = [
            {"email": "a@b.com", "password": "hash", "emailOTP": "1"}
        ]
        with patch.object(t, "_db", return_value=_fake_db({"users": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "custom_query", "collection": "users", "operation": "find",
                "projection": {"email": 1, "password": 1},
            }))
        assert out["items"] == [{"email": "a@b.com"}]


class TestNewUserActions:
    @pytest.mark.parametrize("action", [
        "get_user_products", "get_user_receipts", "get_user_notifications",
    ])
    def test_require_user_id(self, monkeypatch, action):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({"action": action, "user_id": ""}))
        assert "error" in out and "user_id" in out["error"]

    def test_new_collections_in_allowlist(self):
        assert {"user_product_purchases", "receipt_upload_history", "notifications"} <= t._ALLOWED_COLLECTIONS

    def test_get_user_products_shape(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        cursor = MagicMock()
        coll.find.return_value = cursor
        cursor.sort.return_value = cursor
        cursor.limit.return_value = [{"product_name": "X", "product_url": "http://u"}]
        with patch.object(t, "_db", return_value=_fake_db({"user_product_purchases": coll})):
            out = json.loads(t.crwd_db_tool({"action": "get_user_products", "user_id": "abc"}))
        assert out["_type"] == "user_products"
        assert out["items"][0]["product_name"] == "X"

    def test_get_user_products_by_crwd_id_lists_all_catalog_skus(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        gig_oid = t._oid("69e6a4d6cea992cbda22b381")
        mock_crwds = MagicMock()
        mock_crwds.find_one.return_value = {
            "_id": gig_oid,
            "name": "CRWD Cohort- Amazon",
            "gig_stores": [{
                "store_name": "Amazon",
                "products": [
                    {"name": "Gut Intellect", "product_url": "https://amazon.com/a"},
                    {"name": "Shroom Vroom", "product_url": "https://amazon.com/b"},
                ],
            }],
        }
        mock_purchases = MagicMock()
        mock_purchases.find.return_value = []
        with patch.object(t, "_db", return_value=_fake_db({
            "crwds": mock_crwds,
            "user_product_purchases": mock_purchases,
        })):
            out = json.loads(t.crwd_db_tool({
                "action": "get_user_products",
                "user_id": "69a6f191cb29b0b371b3a156",
                "crwd_id": "69e6a4d6cea992cbda22b381",
            }))
        assert out["_type"] == "user_products"
        assert len(out["items"]) == 2
        assert out["items"][0]["name"] == "Gut Intellect"
        assert out["items"][0]["product_url"] == "https://amazon.com/a"

    def test_collect_buy_products_dedupes_and_prefers_purchases(self):
        gig = {"gig_stores": [{"products": [
            {"name": "A", "product_url": "http://a"},
            {"name": "B", "product_url": "http://b"},
            {"name": "A2", "product_url": "http://a"},
        ]}]}
        purchases = [{"product_name": "P", "product_url": "http://p"}]
        items = t._collect_buy_products(gig, purchases)
        assert [i["product_url"] for i in items] == ["http://p", "http://a", "http://b"]

    def test_notifications_custom_query_redacts_tokens(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        cursor = MagicMock()
        coll.find.return_value = cursor
        cursor.limit.return_value = [{"title": "hi", "deviceToken": "d", "chat_token": "c"}]
        with patch.object(t, "_db", return_value=_fake_db({"notifications": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "custom_query", "collection": "notifications", "operation": "find",
            }))
        assert out["items"] == [{"title": "hi"}]


class TestEnrolledGigExclusion:
    def test_get_enrolled_gig_ids_collects_crwd_ids(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        enrolled_oid = t._oid("69e6a4d6cea992cbda22b381")
        coll = MagicMock()
        coll.find.return_value = [{"crwd_id": enrolled_oid}]
        with patch.object(t, "_db", return_value=_fake_db({"added_crwd_members": coll})):
            enrolled = t._get_enrolled_gig_ids("69a6f191cb29b0b371b3a156")
        assert enrolled == {"69e6a4d6cea992cbda22b381"}

    def test_list_active_gigs_with_user_id_excludes_enrolled(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        enrolled_oid = t._oid("69e6a4d6cea992cbda22b381")
        available_oid = t._oid("69b8614f1083b9302fd0a9a7")
        mock_members = MagicMock()
        mock_members.find.return_value = [{"crwd_id": enrolled_oid}]
        mock_crwds = MagicMock()
        cursor = MagicMock()
        mock_crwds.find.return_value = cursor
        mock_crwds.count_documents.return_value = 1
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = [
            {"_id": available_oid, "name": "New Gig", "gig_stores": []},
        ]
        with patch.object(t, "_db", return_value=_fake_db({
            "added_crwd_members": mock_members,
            "crwds": mock_crwds,
        })):
            out = json.loads(t.crwd_db_tool({
                "action": "list_active_gigs",
                "user_id": "69a6f191cb29b0b371b3a156",
            }))
        assert out["_type"] == "gig_list"
        assert out["excluded_enrolled_count"] == 1
        assert len(out["items"]) == 1
        assert out["has_more"] is False
        assert out["next_offset"] is None
        query_used = mock_crwds.find.call_args[0][0]
        assert enrolled_oid in query_used["_id"]["$nin"]
        cursor.skip.assert_called_with(0)

    def test_list_active_gigs_without_user_id_no_exclusion(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        mock_crwds = MagicMock()
        cursor = MagicMock()
        mock_crwds.find.return_value = cursor
        mock_crwds.count_documents.return_value = 0
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = []
        with patch.object(t, "_db", return_value=_fake_db({"crwds": mock_crwds})):
            out = json.loads(t.crwd_db_tool({"action": "list_active_gigs"}))
        assert "excluded_enrolled_count" not in out
        assert out["has_more"] is False
        query_used = mock_crwds.find.call_args[0][0]
        assert "$nin" not in query_used.get("_id", {})


class TestListActiveGigsPagination:
    def _gig_doc(self, hex_id: str, name: str):
        return {"_id": t._oid(hex_id), "name": name, "gig_stores": []}

    def test_pagination_first_page_has_more(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        mock_crwds = MagicMock()
        cursor = MagicMock()
        mock_crwds.find.return_value = cursor
        mock_crwds.count_documents.return_value = 12
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = [self._gig_doc(f"69b8614f1083b9302fd0a9{i:02x}", f"Gig {i}") for i in range(5)]
        with patch.object(t, "_db", return_value=_fake_db({"crwds": mock_crwds})):
            out = json.loads(t.crwd_db_tool({"action": "list_active_gigs", "limit": 5}))
        assert out["total"] == 12
        assert out["offset"] == 0
        assert out["limit"] == 5
        assert len(out["items"]) == 5
        assert out["has_more"] is True
        assert out["next_offset"] == 5
        cursor.skip.assert_called_with(0)

    def test_pagination_second_page_uses_offset(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        mock_crwds = MagicMock()
        cursor = MagicMock()
        mock_crwds.find.return_value = cursor
        mock_crwds.count_documents.return_value = 12
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = [self._gig_doc("69b8614f1083b9302fd0a9a7", "Gig 6")]
        with patch.object(t, "_db", return_value=_fake_db({"crwds": mock_crwds})):
            out = json.loads(t.crwd_db_tool({
                "action": "list_active_gigs",
                "limit": 5,
                "offset": 5,
            }))
        assert out["offset"] == 5
        assert out["has_more"] is True
        assert out["next_offset"] == 6
        cursor.skip.assert_called_with(5)

    def test_last_page_has_more_false(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        mock_crwds = MagicMock()
        cursor = MagicMock()
        mock_crwds.find.return_value = cursor
        mock_crwds.count_documents.return_value = 10
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = [self._gig_doc("69b8614f1083b9302fd0a9a7", "Gig 10")]
        with patch.object(t, "_db", return_value=_fake_db({"crwds": mock_crwds})):
            out = json.loads(t.crwd_db_tool({
                "action": "list_active_gigs",
                "limit": 5,
                "offset": 9,
            }))
        assert out["offset"] == 9
        assert len(out["items"]) == 1
        assert out["has_more"] is False
        assert out["next_offset"] is None


class TestWaitlistedGigs:
    def test_requires_user_id(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({"action": "get_waitlisted_gigs"}))
        assert "error" in out
        assert "user_id" in out["error"]

    def test_filters_is_accepted_false_and_joins_gigs(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        user_id = "69a6f191cb29b0b371b3a156"
        member_oid = t._oid(user_id)
        gig_oid = t._oid("69e6a4d6cea992cbda22b381")
        mock_members = MagicMock()
        mock_members.find.return_value = [
            {
                "member": member_oid,
                "crwd_id": gig_oid,
                "isAccepted": False,
                "status": "Pending",
            },
        ]
        mock_crwds = MagicMock()
        mock_crwds.find.return_value = [
            {"_id": gig_oid, "name": "Waitlisted Gig", "gig_stores": []},
        ]
        with patch.object(t, "_db", return_value=_fake_db({
            "added_crwd_members": mock_members,
            "crwds": mock_crwds,
        })):
            out = json.loads(t.crwd_db_tool({
                "action": "get_waitlisted_gigs",
                "user_id": user_id,
            }))
        assert out["_type"] == "waitlisted_gigs"
        assert len(out["items"]) == 1
        assert out["items"][0]["membership"]["isAccepted"] is False
        assert out["items"][0]["gig"]["name"] == "Waitlisted Gig"
        member_filter = mock_members.find.call_args[0][0]
        assert member_filter["isAccepted"] is False
        assert member_filter["isDeleted"] == {"$ne": True}


class TestRouter:
    def test_unknown_action(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({"action": "frobnicate"}))
        assert "error" in out

    def test_unexpected_exception_generic_error(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        with patch.object(t, "_db", side_effect=Exception("driver boom")):
            out = json.loads(t.crwd_db_tool({"action": "list_active_gigs"}))
        # Raw driver error must not leak to the model.
        assert out == {"error": "query failed"}


class TestUserGigHistory:
    def test_requires_user_id(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({"action": "get_user_gig_history"}))
        assert "error" in out
        assert "user_id" in out["error"]

    def test_returns_membership_rows(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        user_id = "69a6f191cb29b0b371b3a156"
        mock_members = MagicMock()
        member_cursor = MagicMock()
        mock_members.find.return_value = member_cursor
        member_cursor.sort.return_value = member_cursor
        member_cursor.limit.return_value = [
            {
                "_id": t._oid("69e6a4d6cea992cbda22b381"),
                "crwd_id": t._oid("69b8614f1083b9302fd0a9a7"),
                "status": "Completed",
                "isAccepted": True,
                "hasPaid": True,
            },
        ]
        mock_db = _fake_db({"added_crwd_members": mock_members})
        mock_db.list_collection_names.return_value = []
        with patch.object(t, "_db", return_value=mock_db):
            out = json.loads(t.crwd_db_tool({
                "action": "get_user_gig_history",
                "user_id": user_id,
            }))
        assert out["_type"] == "user_gig_history"
        assert out["count"] == 1
        assert out["items"][0]["status"] == "Completed"


class TestGigDetailsFull:
    def test_full_mode_returns_rich_payload(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        gig_oid = t._oid("69b8614f1083b9302fd0a9a7")
        mock_crwds = MagicMock()
        mock_crwds.find_one.return_value = {
            "_id": gig_oid,
            "name": "Summer Gig",
            "gig_stores": [{"store_name": "Target", "products": []}],
            "terms_description": "Terms here",
            "targeting_rules": [],
            "locations": [],
        }
        with patch.object(t, "_db", return_value=_fake_db({"crwds": mock_crwds})):
            out = json.loads(t.crwd_db_tool({
                "action": "get_gig_details",
                "query": "Summer Gig",
                "full": True,
            }))
        assert out["full"] is True
        assert out["items"][0]["name"] == "Summer Gig"
        assert out["items"][0]["terms_description"] == "Terms here"


class TestPrefetchHelpers:
    def test_fetch_active_gigs_requires_user_id(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = t.fetch_active_gigs("")
        assert out["success"] is False
        assert "user_id" in out["error"]


class TestNormalizeProofId:
    def test_receipt_prefix_spacing_and_hyphens_collapse(self):
        key = t._normalize_proof_id("REC# 2-6177-0190-0173-4723-7", "receipt_target")
        assert key == "261770190017347237"
        # Same receipt, three ways a member might send it.
        for variant in (
            "REC#2617701900173-4723-7",
            "2 6177 0190 0173 4723 7",
            "rec # 2-6177-0190-0173-4723-7",
        ):
            assert t._normalize_proof_id(variant, "receipt_target") == key

    def test_amazon_order_normalizes(self):
        assert (
            t._normalize_proof_id("Order # 112-2229469-0480212", "receipt_amazon")
            == "11222294690480212"
        )

    def test_different_receipts_do_not_collide(self):
        a = t._normalize_proof_id("REC# 2-6177-0190-0173-4723-7", "receipt_target")
        b = t._normalize_proof_id("REC# 2-6177-0190-0173-4722-9", "receipt_target")
        assert a != b

    def test_blank_returns_empty(self):
        assert t._normalize_proof_id("", "receipt_target") == ""
        assert t._normalize_proof_id("   ", "receipt_target") == ""
        assert t._normalize_proof_id("no digits here", "receipt_target") == ""

    def test_ugc_url_variants_collapse_to_one_post_id(self):
        key = "tiktok:7311123"
        for variant in (
            "https://www.tiktok.com/@handle/video/7311123?is_from_webapp=1",
            "https://tiktok.com/video/7311123",
            "https://www.tiktok.com/@handle/video/7311123",
            "HTTPS://WWW.TIKTOK.COM/@handle/video/7311123",
        ):
            assert t._normalize_proof_id(variant, "ugc_link") == key

    def test_youtube_url_forms_collapse(self):
        key = "youtube:dQw4w9WgXcQ"
        for variant in (
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5s",
        ):
            assert t._normalize_proof_id(variant, "ugc_link") == key

    def test_post_id_case_is_preserved(self):
        # YouTube ids and Instagram shortcodes are case-sensitive: folding case
        # would key two different posts the same and reject an innocent member.
        a = t._normalize_proof_id("https://youtu.be/dQw4w9WgXcQ", "ugc_link")
        b = t._normalize_proof_id("https://youtu.be/dQw4w9WgXcq", "ugc_link")
        assert a != b
        assert a == "youtube:dQw4w9WgXcQ"

    def test_platform_prefix_prevents_cross_platform_collision(self):
        yt = t._normalize_proof_id("https://youtu.be/C8xY_z1", "ugc_link")
        ig = t._normalize_proof_id("https://www.instagram.com/p/C8xY_z1/", "ugc_link")
        assert yt != ig

    def test_instagram_reel_and_post_forms(self):
        assert (
            t._normalize_proof_id("https://www.instagram.com/reel/C8xY_z1/", "ugc_link")
            == "instagram:C8xY_z1"
        )

    def test_unresolved_short_link_has_no_key(self):
        # vm.tiktok.com carries no post id until followed -- must not be guessed.
        assert t._normalize_proof_id("https://vm.tiktok.com/ZMabc/", "ugc_link") == ""

    def test_amazon_review_url_is_not_a_key(self):
        # Reviews are proved by screenshot: the permalink is behind a sign-in wall,
        # so it can never be read, and an unread proof is never accepted.
        for url in (
            "https://www.amazon.com/gp/customer-reviews/R2ABC123?ie=UTF8",
            "https://amazon.com/gp/customer-reviews/R2ABC123",
        ):
            assert t._normalize_proof_id(url, "") == "", url


def _proof_doc(**over):
    doc = {
        "_id": t._oid("69b8614f1083b9302fd0a9a7"),
        "normalized_proof_id": "261770190017347237",
        "proof_type": "receipt_target",
        "user_id": "user-a",
        "user_email": "a@example.com",
        "crwd_id": "gig-1",
        "gig_name": "Summer Gig",
        "status": "accepted",
        "created_at": "2026-07-01T00:00:00",
    }
    doc.update(over)
    return doc


class TestCheckDuplicateProof:
    def test_no_hit_is_not_duplicate(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = None
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "REC# 2-6177-0190-0173-4723-7",
                "proof_type": "receipt_target",
                "user_id": "user-b",
            }))
        assert out["duplicate"] is False
        assert out["conflict"] is None

    def test_cross_user_duplicate_exposes_conflict_email(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = _proof_doc()
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "REC# 2-6177-0190-0173-4723-7",
                "proof_type": "receipt_target",
                "user_id": "user-b",
            }))
        assert out["duplicate"] is True
        assert out["same_user"] is False
        assert out["conflict"]["user_email"] == "a@example.com"

    def test_same_user_duplicate_is_flagged_as_such(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = _proof_doc()
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "REC# 2-6177-0190-0173-4723-7",
                "proof_type": "receipt_target",
                "user_id": "user-a",
            }))
        assert out["duplicate"] is True
        assert out["same_user"] is True

    def test_only_accepted_records_count_as_duplicates(self, monkeypatch):
        # A previously rejected proof was never credited -- resending it is not fraud.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = None
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "REC# 2-6177-0190-0173-4723-7",
                "proof_type": "receipt_target",
            }))
        assert coll.find_one.call_args[0][0]["status"] == "accepted"

    def test_matches_across_formatting_differences(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = _proof_doc()
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "2 6177 0190 0173 4723 7",
                "proof_type": "receipt_target",
            }))
        queried = coll.find_one.call_args[0][0]["normalized_proof_id"]
        assert queried == "261770190017347237"
        assert out["duplicate"] is True

    def test_unextractable_id_errors_rather_than_guessing(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({
            "action": "check_duplicate_proof",
            "proof_id": "https://vm.tiktok.com/ZMabc/",
            "proof_type": "ugc_link",
        }))
        assert "error" in out and out["error"]

    def test_ugc_link_dedups(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = _proof_doc(
            normalized_proof_id="tiktok:7311123", proof_type="ugc_link"
        )
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "https://www.tiktok.com/@other/video/7311123?is_from_webapp=1",
                "proof_type": "ugc_link",
            }))
        assert coll.find_one.call_args[0][0]["normalized_proof_id"] == "tiktok:7311123"
        assert out["duplicate"] is True


def _store_args(**over):
    args = {
        "action": "store_proof",
        "proof_id": "REC# 2-6177-0190-0173-4723-7",
        "proof_type": "receipt_target",
        "user_id": "user-a",
        "status": "accepted",
        "reason_code": "clean_match",
        "reason": "Product matches gig catalog, dated within window",
        "confidence": "high",
        # An accepted proof must carry the evidence we actually read.
        "source_url": "https://cdn.example/receipt.jpg",
    }
    args.update(over)
    return args


def _proofs_db(coll, users=None):
    users_coll = users or MagicMock()
    if users is None:
        users_coll.find_one.return_value = {"email": "a@example.com"}
    # Default to "no conflicting accepted row": store_proof consults
    # _proof_conflict before inserting, and a bare MagicMock is truthy.
    if not isinstance(coll.find_one.return_value, (dict, type(None))):
        coll.find_one.return_value = None
    return _fake_db({"proof_submissions": coll, "users": users_coll})


class TestStoreProof:
    def setup_method(self):
        t._proof_index_ready = False

    def test_valid_insert(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.insert_one.return_value.inserted_id = t._oid("69b8614f1083b9302fd0a9a7")
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            out = json.loads(t.crwd_db_tool(_store_args()))
        assert out["stored"] is True
        assert out["duplicate"] is False
        assert out["normalized_proof_id"] == "261770190017347237"
        doc = coll.insert_one.call_args[0][0]
        assert doc["proof_id"] == "REC# 2-6177-0190-0173-4723-7"
        assert doc["normalized_proof_id"] == "261770190017347237"
        assert doc["user_email"] == "a@example.com"
        assert doc["created_by"] == "hermes"

    def test_accept_round_trips_its_reason(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            t.crwd_db_tool(_store_args())
        doc = coll.insert_one.call_args[0][0]
        assert doc["status"] == "accepted"
        assert doc["reason_code"] == "clean_match"
        assert "matches gig catalog" in doc["reason"]

    def test_duplicate_key_is_an_idempotent_resend_not_a_duplicate(self, monkeypatch):
        # The unique index means "this exact artifact is already on file for this
        # member+gig" -- a re-send. It must NOT flip the verdict to duplicate.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        from pymongo.errors import DuplicateKeyError

        coll = MagicMock()
        coll.insert_one.side_effect = DuplicateKeyError("dup")
        coll.find_one.return_value = None
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            out = json.loads(t.crwd_db_tool(_store_args()))
        assert out["stored"] is False
        assert out["already_recorded"] is True
        assert out["duplicate"] is False

    def test_accepting_a_purchase_another_member_holds_is_refused(self, monkeypatch):
        # The fraud rule is enforced in store_proof, not by an index.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = _proof_doc()
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            out = json.loads(t.crwd_db_tool(_store_args(user_id="user-b", crwd_id="gig-1")))
        assert out["stored"] is False
        assert out["duplicate"] is True
        assert out["conflict"]["user_email"] == "a@example.com"
        assert coll.insert_one.called is False

    def test_blank_reason_rejected_even_when_accepted(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(reason="")))
        assert "error" in out and out["error"]

    def test_blank_reason_code_rejected(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(reason_code="")))
        assert "error" in out and out["error"]

    def test_invalid_status_rejected(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(status="approved")))
        assert "error" in out and out["error"]

    def test_invalid_proof_type_rejected(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(proof_type="receipt")))
        assert "error" in out and out["error"]

    def test_invalid_confidence_rejected(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(confidence="very high")))
        assert "error" in out and out["error"]

    def test_blank_proof_id_rejected(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(proof_id="")))
        assert "error" in out and out["error"]

    def test_unnormalizable_proof_id_rejected(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(
            proof_id="https://vm.tiktok.com/ZMabc/", proof_type="ugc_link",
        )))
        assert "error" in out and out["error"]
        assert "no_identifier" in out["error"]

    def test_ugc_link_stores_platform_post_id(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            t.crwd_db_tool(_store_args(
                proof_id="https://www.tiktok.com/@h/video/7311123?is_from_webapp=1",
                proof_type="ugc_link",
                proof_link="https://www.tiktok.com/@h/video/7311123",
            ))
        assert coll.insert_one.call_args[0][0]["normalized_proof_id"] == "tiktok:7311123"

    def test_review_screenshot_stores_gig_handle_date_key(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            t.crwd_db_tool(_store_args(
                proof_id="69deb0781ca6038a3a1f6f8a:sarah_k:July 15, 2026",
                proof_type="review_screenshot",
                proof_link="https://cdn.example/shot.png",
            ))
        assert coll.insert_one.call_args[0][0]["normalized_proof_id"] == (
            "69deb0781ca6038a3a1f6f8a:sarah-k:july-15-2026"
        )

    def test_unique_index_is_per_artifact_for_idempotency(self, monkeypatch):
        # Includes proof_type so one purchase can back an order screenshot AND a
        # receipt; it buys idempotency, not the fraud rule.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            t.crwd_db_tool(_store_args())
        call = coll.create_index.call_args_list[0]
        assert call[0][0] == [
            ("normalized_proof_id", 1), ("user_id", 1), ("crwd_id", 1), ("proof_type", 1)
        ]
        assert call[1]["unique"] is True
        assert call[1]["partialFilterExpression"] == {"status": "accepted"}

    def test_index_failure_does_not_block_recording(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.create_index.side_effect = Exception("no perms")
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            out = json.loads(t.crwd_db_tool(_store_args()))
        assert out["stored"] is True


class TestFindProof:
    def test_returns_every_status_not_just_accepted(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        cursor = MagicMock()
        cursor.sort.return_value.limit.return_value = [
            _proof_doc(status="rejected", reason_code="wrong_product"),
            _proof_doc(status="accepted"),
        ]
        coll.find.return_value = cursor
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "find_proof",
                "proof_id": "REC# 2-6177-0190-0173-4723-7",
                "proof_type": "receipt_target",
            }))
        assert out["_type"] == "crwd_proof_lookup"
        assert out["count"] == 2
        assert "status" not in coll.find.call_args[0][0]
        assert {i["status"] for i in out["items"]} == {"rejected", "accepted"}

    def test_user_id_filter_narrows(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find.return_value.sort.return_value.limit.return_value = []
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            t.crwd_db_tool({
                "action": "find_proof",
                "proof_id": "REC# 2-6177-0190-0173-4723-7",
                "proof_type": "receipt_target",
                "user_id": "user-a",
            })
        query = coll.find.call_args[0][0]
        assert query["user_id"] == "user-a"
        assert query["proof_type"] == "receipt_target"

    def test_limit_capped_at_hard_limit(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        cursor = MagicMock()
        cursor.sort.return_value.limit.return_value = []
        coll.find.return_value = cursor
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            t.crwd_db_tool({
                "action": "find_proof",
                "proof_id": "REC# 2-6177-0190-0173-4723-7",
                "proof_type": "receipt_target",
                "limit": 500,
            })
        cursor.sort.return_value.limit.assert_called_with(t._HARD_LIMIT)

    def test_no_hit_returns_empty_items(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find.return_value.sort.return_value.limit.return_value = []
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "find_proof",
                "proof_id": "REC# 2-6177-0190-0173-4723-7",
                "proof_type": "receipt_target",
            }))
        assert out["items"] == []
        assert out["count"] == 0


class TestProofWriteScope:
    """The write path must stay narrow now that the module is no longer read-only."""

    def test_custom_query_cannot_write(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        for operation in ("insert", "insert_one", "update", "delete", "drop"):
            out = json.loads(t.crwd_db_tool({
                "action": "custom_query",
                "collection": "proof_submissions",
                "operation": operation,
                "filter": {},
            }))
            assert "error" in out and out["error"], operation

    def test_store_proof_only_touches_proof_submissions(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        touched = []
        proofs = MagicMock()
        proofs.find_one.return_value = None  # no conflicting accepted row
        users = MagicMock()
        users.find_one.return_value = {"email": "a@example.com"}

        def record(name):
            touched.append(name)
            return {"proof_submissions": proofs, "users": users}[name]

        db = MagicMock()
        db.__getitem__.side_effect = record
        with patch.object(t, "_db", return_value=db):
            t.crwd_db_tool(_store_args())
        # users is read for the audit email; proof_submissions is the only write.
        assert set(touched) <= {"proof_submissions", "users"}
        assert users.insert_one.called is False
        assert users.update_one.called is False
        assert proofs.insert_one.called is True

    def test_no_write_helpers_on_read_actions(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find.return_value.sort.return_value.limit.return_value = []
        with patch.object(t, "_db", return_value=_fake_db({"receipt_upload_history": coll})):
            t.crwd_db_tool({"action": "get_user_receipts", "user_id": "user-a"})
        assert coll.insert_one.called is False
        assert coll.update_one.called is False
        assert coll.delete_one.called is False


class TestStoreRequirements:
    """gig_stores[].requires_* is the real proof spec -- type_of_work_proof is
    unset on 60 of 63 real gigs, so the slim payload must carry the flags."""

    def test_slim_gig_surfaces_requirements(self):
        out = t._slim_gig({
            "_id": t._oid("69b8614f1083b9302fd0a9a7"),
            "name": "Gig",
            "gig_stores": [{
                "store_name": "Amazon",
                "requires_receipt": True,
                "requires_review_link": True,
                "products": [{"name": "Thing", "product_url": "u"}],
            }],
        })
        reqs = out["stores"][0]["requirements"]
        assert reqs["requires_receipt"] is True
        assert reqs["requires_review_link"] is True
        # Absent flags are explicitly False, never missing -- the model must be
        # able to read "not required" without inferring it from a KeyError.
        assert reqs["requires_ugc_post"] is False
        assert set(reqs) == set(t._STORE_REQUIREMENT_FLAGS)

    def test_requirements_are_real_booleans(self):
        out = t._slim_gig({
            "_id": None, "name": "G",
            "gig_stores": [{"store_name": "T", "requires_receipt": "yes", "requires_order_id": None}],
        })
        reqs = out["stores"][0]["requirements"]
        assert reqs["requires_receipt"] is True
        assert reqs["requires_order_id"] is False

    def test_store_with_no_flags_still_has_requirements(self):
        out = t._slim_gig({"_id": None, "name": "G", "gig_stores": [{"store_name": "Target "}]})
        assert out["stores"][0]["requirements"] == {f: False for f in t._STORE_REQUIREMENT_FLAGS}

    def test_full_gig_keeps_slim_stores_and_raw_gig_stores(self):
        gig = {
            "_id": None, "name": "G",
            "gig_stores": [{"store_name": "Amazon", "requires_receipt": True, "sort_order": 0}],
        }
        out = t._full_gig(gig)
        assert out["stores"][0]["requirements"]["requires_receipt"] is True
        assert out["gig_stores"][0]["sort_order"] == 0


class TestReviewScreenshotGigHandleDateKeys:
    """A review screenshot keys on '{crwd_id}:{handle}:{review_date_as_shown}'.
    The tool slugifies only — it does not parse dates or verify handles."""

    GIG = "69deb0781ca6038a3a1f6f8a"
    OTHER_GIG = "aaaaaaaaaaaaaaaaaaaaaaaa"

    def test_gig_handle_date_slugifies(self):
        key = t._normalize_proof_id(f"{self.GIG}:sarah_k:July 15, 2026", "review_screenshot")
        assert key == f"{self.GIG}:sarah-k:july-15-2026"

    def test_same_text_is_stable_across_separators(self):
        a = t._normalize_proof_id(f"{self.GIG}:sarah_k:July 15, 2026", "review_screenshot")
        b = t._normalize_proof_id(f"{self.GIG}|sarah_k|July 15, 2026", "review_screenshot")
        c = t._normalize_proof_id(f"{self.GIG} / sarah_k / July 15, 2026", "review_screenshot")
        assert a == b == c == f"{self.GIG}:sarah-k:july-15-2026"

    def test_each_part_changes_the_key(self):
        # Notably the handle: two honest members reviewing one gig on one day must
        # not be rejected as duplicates of each other.
        base = t._normalize_proof_id(f"{self.GIG}:sarah_k:July 15, 2026", "review_screenshot")
        for other in (
            f"{self.GIG}:mike_r:July 15, 2026",         # different member
            f"{self.GIG}:sarah_k:July 16, 2026",        # different day
            f"{self.OTHER_GIG}:sarah_k:July 15, 2026",  # different gig
        ):
            assert base != t._normalize_proof_id(other, "review_screenshot") != ""

    def test_does_not_refuse_unparsed_date_text(self):
        # Date window / legibility is skill-side; the tool must not parse dates.
        # Relative dates ("2 days ago") are what phone apps actually render.
        key = t._normalize_proof_id(f"{self.GIG}:sarah_k:2 days ago", "review_screenshot")
        assert key == f"{self.GIG}:sarah-k:2-days-ago"

    def test_missing_any_part_refuses(self):
        for raw in (
            "target:A-95279869:sarah_k",        # no gig id
            "July 15, 2026",                    # bare date
            f"{self.GIG}:July 15, 2026",        # no handle
            f"{self.GIG}:sarah_k",              # no date
            f"{self.GIG}:sarah_k:",             # empty date
            f"{self.GIG}::July 15, 2026",       # empty handle
            f"{self.GIG}:sarah_k:   ",          # whitespace date
        ):
            assert t._normalize_proof_id(raw, "review_screenshot") == "", raw

    def test_object_id_and_handle_case_are_folded(self):
        a = t._normalize_proof_id(f"{self.GIG.upper()}:Sarah_K:July 15, 2026", "review_screenshot")
        b = t._normalize_proof_id(f"{self.GIG}:sarah_k:july 15, 2026", "review_screenshot")
        assert a == b == f"{self.GIG}:sarah-k:july-15-2026"


class TestReviewLinksAreNotAProofType:
    """Reviews are proved by screenshot only. Target's 'review link' is the product
    page; Amazon's permalink is behind a sign-in wall and cannot be read."""

    def test_amazon_review_link_is_rejected_as_a_proof_type(self, monkeypatch):
        assert "amazon_review_link" not in t._PROOF_TYPES
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(
            proof_id="R1M6OC1KJ7ZMUQ", proof_type="amazon_review_link",
        )))
        assert "error" in out and out["error"]


class TestProductPageUrlsAreNeverKeys:
    """No review url is ever a key. Target's /p/hj/-/A-95279869 is the product page
    every reviewer shares; Amazon's permalink needs a login and can't be read."""

    TARGET_URL = "https://www.target.com/p/hj/-/A-95279869"
    AMAZON_REVIEW_URL = "https://amazon.com/gp/customer-reviews/R1M6OC1KJ7ZMUQ?ref=pf_ov"

    def test_review_urls_never_normalize(self):
        for url in (self.TARGET_URL, self.AMAZON_REVIEW_URL):
            for proof_type in ("review_screenshot", "ugc_link", ""):
                assert t._normalize_proof_id(url, proof_type) == "", (url, proof_type)

    def test_target_url_cannot_be_stored(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(
            proof_id=self.TARGET_URL, proof_type="review_screenshot",
        )))
        assert "error" in out and out["error"]

    def test_two_members_same_target_url_never_collide_as_duplicates(self, monkeypatch):
        # Both fail to key at all, so neither can be called a duplicate of the other.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({
            "action": "check_duplicate_proof",
            "proof_id": self.TARGET_URL,
            "proof_type": "review_screenshot",
            "user_id": "user-b",
        }))
        assert "error" in out and out["error"]

    def test_unrecognized_share_urls_do_not_key(self):
        # Real values seen in gig_product_reviews.review_link.
        for url in (
            "https://share.icloud.com/photos/abc",
            "https://drive.google.com/file/d/xyz",
            "https://mycarpe.com/products/x",
        ):
            assert t._normalize_proof_id(url, "") == "", url


class TestProofIdNamesAPurchaseNotASubmission:
    """A proof id identifies a *purchase*. One purchase legitimately backs several
    artifacts -- real gig_store_orders rows carry two receipt files for one order,
    and the same order_id recurs across rows for one member. Keying on the id alone
    hard-blocked an honest member's second artifact on the 41 gigs that require both
    a receipt and an order id.
    """

    def test_same_member_same_gig_second_artifact_is_not_a_duplicate(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = None  # the $nor filter excludes their own row
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "Order # 112-2229469-0480212",
                "proof_type": "receipt_amazon",
                "user_id": "user-a",
                "crwd_id": "gig-1",
            }))
        assert out["duplicate"] is False
        # The member's own accepted row for this gig must be excluded from the query.
        assert coll.find_one.call_args[0][0]["$nor"] == [{"user_id": "user-a", "crwd_id": "gig-1"}]

    def test_another_member_using_the_purchase_is_still_a_duplicate(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = _proof_doc()  # user-a's accepted row
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "Order # 112-2229469-0480212",
                "proof_type": "receipt_amazon",
                "user_id": "user-b",
                "crwd_id": "gig-1",
            }))
        assert out["duplicate"] is True
        assert out["same_user"] is False
        assert out["conflict"]["user_email"] == "a@example.com"

    def test_same_member_reusing_a_purchase_on_another_gig_is_a_duplicate(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = _proof_doc(crwd_id="gig-1")
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "Order # 112-2229469-0480212",
                "proof_type": "receipt_amazon",
                "user_id": "user-a",
                "crwd_id": "gig-2",
            }))
        assert out["duplicate"] is True
        assert out["same_user"] is True

    def test_without_crwd_id_the_check_stays_conservative(self, monkeypatch):
        # No gig scope -> cannot exclude the member's own row, so any accepted row
        # reports as a conflict. The caller should pass crwd_id.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = _proof_doc()
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "check_duplicate_proof",
                "proof_id": "Order # 112-2229469-0480212",
                "proof_type": "receipt_amazon",
                "user_id": "user-a",
            }))
        assert "$nor" not in coll.find_one.call_args[0][0]
        assert out["duplicate"] is True


class TestReasonCodeIsClosed:
    """A risk assessment counts reason codes, so the vocabulary must be fixed --
    an open field lets 'wrong_item' drift in beside 'wrong_product'."""

    def test_invalid_reason_code_rejected(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(reason_code="wrong_item")))
        assert "error" in out and out["error"]

    def test_every_documented_code_is_accepted(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            for code in t._PROOF_REASON_CODES:
                out = json.loads(t.crwd_db_tool(_store_args(
                    reason_code=code,
                    status="accepted" if code == "clean_match" else "rejected",
                )))
                assert out.get("stored") is True, code

    def test_incomplete_submission_is_a_valid_code(self, monkeypatch):
        # Two-purchase gigs: one receipt in hand is incomplete, not clean.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            out = json.loads(t.crwd_db_tool(_store_args(
                status="needs_human", reason_code="incomplete_submission",
                reason="first of two receipts; second payment method outstanding",
            )))
        assert out["stored"] is True

    def test_schema_enum_matches_the_validator(self):
        schema_codes = set(
            t.CRWD_DB_SCHEMA["parameters"]["properties"]["reason_code"]["enum"]
        )
        assert schema_codes == t._PROOF_REASON_CODES

    def test_driver_skill_documents_exactly_these_codes(self):
        import pathlib
        skill = pathlib.Path("skills/crwd/crwd-proof-validator/SKILL.md").read_text()
        for code in t._PROOF_REASON_CODES:
            assert f"`{code}`" in skill, f"{code} missing from SKILL.md reason table"


class TestOrderScreenshotIsItsOwnArtifact:
    """An order confirmation and the receipt for that order share an order number.
    They are two artifacts of one purchase -- typing them apart is what lets a
    member record both on the 41 gigs that want a receipt and an order id."""

    def test_order_screenshot_is_a_valid_proof_type(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = None
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            out = json.loads(t.crwd_db_tool(_store_args(
                proof_type="order_screenshot", proof_id="Order # 112-2229469-0480212",
            )))
        assert out["stored"] is True

    def test_order_screenshot_normalizes_like_a_receipt(self):
        assert (
            t._normalize_proof_id("Order # 112-2229469-0480212", "order_screenshot")
            == t._normalize_proof_id("Order # 112-2229469-0480212", "receipt_amazon")
        )

    def test_proof_type_enum_matches_the_validator(self):
        schema_types = set(
            t.CRWD_DB_SCHEMA["parameters"]["properties"]["proof_type"]["enum"]
        )
        assert schema_types == t._PROOF_TYPES

    def test_driver_skill_documents_every_proof_type(self):
        import pathlib
        skill = pathlib.Path("skills/crwd/crwd-proof-validator/SKILL.md").read_text()
        for proof_type in t._PROOF_TYPES:
            assert f"`{proof_type}`" in skill, f"{proof_type} missing from SKILL.md"


def _gig_with(**flags):
    store = {"store_name": flags.pop("store_name", "Amazon"), "products": []}
    store.update(flags)
    return {"_id": t._oid("69b8614f1083b9302fd0a9a7"), "name": "Gig", "gig_stores": [store]}


def _completion_db(gig, accepted_types):
    crwds = MagicMock()
    crwds.find_one.return_value = gig
    proofs = MagicMock()
    proofs.find.return_value = [{"proof_type": pt} for pt in accepted_types]
    users = MagicMock()
    users.find_one.return_value = {"email": "a@example.com"}
    return _fake_db({"crwds": crwds, "proof_submissions": proofs, "users": users})


class TestGigProofCompletion:
    def test_field_level_flags_never_gate_completion(self, monkeypatch):
        # requires_order_id never appears without requires_receipt (41 gigs vs 0) and
        # the app stores order_id on the receipt row -- it is a field, not an artifact.
        # Gating on it would leave every such gig permanently incomplete.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        gig = _gig_with(requires_receipt=True, requires_order_id=True,
                        requires_store_address=True)
        with patch.object(t, "_db", return_value=_completion_db(gig, ["receipt_amazon"])):
            out = json.loads(t.crwd_db_tool({
                "action": "check_gig_proof_completion", "user_id": "u", "crwd_id": "g",
            }))
        assert out["complete"] is True
        assert out["outstanding"] == []
        assert "requires_order_id" in out["field_level"]

    def test_outstanding_artifact_blocks_completion(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        gig = _gig_with(requires_receipt=True, requires_review_receipt=True)
        with patch.object(t, "_db", return_value=_completion_db(gig, ["receipt_amazon"])):
            out = json.loads(t.crwd_db_tool({
                "action": "check_gig_proof_completion", "user_id": "u", "crwd_id": "g",
            }))
        assert out["complete"] is False
        assert out["outstanding"] == ["requires_review_receipt"]
        assert out["satisfied"] == ["requires_receipt"]

    def test_review_screenshot_satisfies_review_link_everywhere(self, monkeypatch):
        # requires_review_link is a legacy flag name: a screenshot is the only
        # thing that ever satisfies it, at every store. No link is owed anywhere.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        for store in ("Target ", "Amazon", "Walmart"):
            gig = _gig_with(requires_review_link=True, store_name=store)
            with patch.object(t, "_db", return_value=_completion_db(gig, ["review_screenshot"])):
                out = json.loads(t.crwd_db_tool({
                    "action": "check_gig_proof_completion", "user_id": "u", "crwd_id": "g",
                }))
            assert out["complete"] is True, f"{store}: screenshot must complete the gig"
        assert t._artifacts_for("requires_review_link", "Amazon") == {"review_screenshot"}

    def test_gig_with_no_artifact_requirements_is_not_asserted_complete(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        with patch.object(t, "_db", return_value=_completion_db(_gig_with(), [])):
            out = json.loads(t.crwd_db_tool({
                "action": "check_gig_proof_completion", "user_id": "u", "crwd_id": "g",
            }))
        assert out["complete"] is False
        assert out["determinable"] is False

    def test_requires_user_id_and_crwd_id(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        for args in ({"user_id": "", "crwd_id": "g"}, {"user_id": "u", "crwd_id": ""}):
            out = json.loads(t.crwd_db_tool({"action": "check_gig_proof_completion", **args}))
            assert "error" in out and out["error"]


class TestIsGigCompletedFlag:
    """True only on the proof that leaves nothing outstanding; False on every proof
    submitted before it. Computed by the tool, never taken from the caller."""

    def test_false_while_something_is_outstanding(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        gig = _gig_with(requires_receipt=True, requires_review_receipt=True)
        db = _completion_db(gig, [])
        proofs = db["proof_submissions"]
        proofs.find_one.return_value = None
        with patch.object(t, "_db", return_value=db):
            out = json.loads(t.crwd_db_tool(_store_args(
                proof_id="Order # 112-2229469-0480212",
                proof_type="receipt_amazon", crwd_id="g", user_id="u")))
        assert out["is_gig_completed"] is False
        assert proofs.insert_one.call_args[0][0]["is_gig_completed"] is False

    def test_true_on_the_proof_that_completes_the_gig(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        gig = _gig_with(requires_receipt=True, requires_review_receipt=True)
        db = _completion_db(gig, ["receipt_amazon"])  # receipt already accepted
        proofs = db["proof_submissions"]
        proofs.find_one.return_value = None
        with patch.object(t, "_db", return_value=db):
            out = json.loads(t.crwd_db_tool(_store_args(
                proof_id="69b8614f1083b9302fd0a9a7:sarah_k:July 15, 2026",
                proof_type="review_screenshot",
                crwd_id="g", user_id="u")))
        assert out["is_gig_completed"] is True
        assert proofs.insert_one.call_args[0][0]["is_gig_completed"] is True

    def test_a_rejected_proof_never_completes_a_gig(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        gig = _gig_with(requires_receipt=True)
        db = _completion_db(gig, [])
        db["proof_submissions"].find_one.return_value = None
        with patch.object(t, "_db", return_value=db):
            out = json.loads(t.crwd_db_tool(_store_args(
                status="rejected", reason_code="wrong_product",
                reason="not on the catalog", crwd_id="g", user_id="u")))
        assert out["is_gig_completed"] is False

    def test_caller_cannot_force_the_flag(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        gig = _gig_with(requires_receipt=True, requires_review_receipt=True)
        db = _completion_db(gig, [])
        db["proof_submissions"].find_one.return_value = None
        with patch.object(t, "_db", return_value=db):
            out = json.loads(t.crwd_db_tool(_store_args(
                proof_id="Order # 112-2229469-0480212",
                proof_type="receipt_amazon", crwd_id="g", user_id="u",
                is_gig_completed=True)))
        assert out["is_gig_completed"] is False


class TestGigCompleteLabelExists:
    def test_gig_complete_is_a_predefined_label(self):
        from plugins.platforms.chatwoot.labels import APPLIED_LABEL_TITLES

        assert "gig-complete" in APPLIED_LABEL_TITLES


class TestReviewLinkFlagTakesAScreenshotAtEveryStore:
    """requires_review_link is satisfied by a review_screenshot and nothing else.
    No store is exempt: Target has no per-review url, and Amazon's permalink can't
    be opened. Never demand a link from anyone."""

    def _complete_with_screenshot(self, store_name):
        gig = _gig_with(requires_review_link=True, store_name=store_name)
        with patch.object(t, "_db", return_value=_completion_db(gig, ["review_screenshot"])):
            return json.loads(t.crwd_db_tool({
                "action": "check_gig_proof_completion", "user_id": "u", "crwd_id": "g",
            }))

    def test_unknown_stores_accept_a_screenshot(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        # Real store names from the gig data; none has ever produced a review link.
        for store in ("Walmart", "Whole Foods", "SPROUTS FARMERS MARKET", "Raley's",
                      "Apple Store", "MR. D.I.Y. (M) SDN BHD"):
            out = self._complete_with_screenshot(store)
            assert out["complete"] is True, f"{store} should accept a screenshot"

    def test_amazon_accepts_only_a_screenshot(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = self._complete_with_screenshot("Amazon")
        assert out["complete"] is True
        assert t._artifacts_for("requires_review_link", "Amazon") == {"review_screenshot"}

    def test_target_accepts_a_screenshot_despite_the_trailing_space(self, monkeypatch):
        # The data holds both 'Target' and 'Target ' -- normalization must catch both.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        for store in ("Target", "Target ", "TARGET"):
            assert self._complete_with_screenshot(store)["complete"] is True, store

    def test_artifacts_for_is_the_single_source(self):
        for store in ("Walmart", "Amazon", "Target", ""):
            assert t._artifacts_for("requires_review_link", store) == {"review_screenshot"}, store


class TestMultiStoreGigsAreALatentRisk:
    """No gig has more than one store today (verified against staging), so unioning
    requirements across stores is safe. If multi-store gigs ever ship, a member who
    buys at ONE store would owe the union -- revisit then."""

    def test_union_across_stores_is_documented_behaviour(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        gig = {
            "_id": t._oid("69b8614f1083b9302fd0a9a7"), "name": "Two stores",
            "gig_stores": [
                {"store_name": "Target", "requires_receipt": True},
                {"store_name": "Amazon", "requires_ugc_post": True},
            ],
        }
        with patch.object(t, "_db", return_value=_completion_db(gig, ["receipt_target"])):
            out = json.loads(t.crwd_db_tool({
                "action": "check_gig_proof_completion", "user_id": "u", "crwd_id": "g",
            }))
        # Current behaviour: the union is owed. Pinned so a future multi-store gig
        # surfaces here rather than silently stranding a member.
        assert out["outstanding"] == ["requires_ugc_post"]


class TestOrderNumberShape:
    """Without a shape check, _normalize_proof_id turns a typed "12345" into a
    valid proof id. Staging holds Amazon rows with order_ids of exactly "12345",
    "2234" and "45435" -- the manual-entry abuse itself. Strictness is matched to
    evidence: exact length only where we have hundreds of samples."""

    def test_real_amazon_order_passes(self):
        assert t._normalize_proof_id("Order # 112-2229469-0480212", "receipt_amazon") \
            == "11222294690480212"

    def test_typed_junk_refused_for_amazon(self):
        for junk in ("12345", "2234", "45435", "123512", "1231235"):
            assert t._normalize_proof_id(junk, "receipt_amazon") == "", junk

    def test_amazon_typo_refused_rather_than_keyed(self):
        # One digit short / long: a real member typo. Refusing routes it to a
        # human instead of storing a key that matches nothing.
        assert t._normalize_proof_id("114-8752493-907022", "receipt_amazon") == ""
        assert t._normalize_proof_id("113-0952386-82474669", "receipt_amazon") == ""

    def test_two_orders_pasted_into_one_field_refused(self):
        # Real value from gig_store_orders: a two-purchase gig where the member
        # pasted both numbers. Concatenating them would key on neither.
        both = "# 113-5521368-3152237 # 113-2950954-4561840"
        assert t._normalize_proof_id(both, "receipt_amazon") == ""

    def test_real_target_rec_number_passes(self):
        # 18 digits across the only four real samples we have.
        assert t._normalize_proof_id("REC# 2-6177-0190-0173-4723-7", "receipt_target") \
            == "261770190017347237"
        assert t._normalize_proof_id("2-6172-2275-0172-6193-1", "receipt_target") \
            == "261722275017261931"

    def test_target_uses_a_floor_not_an_exact_length(self):
        # Evidence is four receipts and zero gig_store_orders rows -- too thin to
        # reject an 18-vs-17 digit real receipt. Junk still refuses.
        assert t._normalize_proof_id("12345", "receipt_target") == ""
        assert t._normalize_proof_id("2-6177-0190-0173-472", "receipt_target") != ""

    def test_unknown_merchants_stay_lenient(self):
        # Real Sprouts receipt: 6 digits. Real Walmart order: 15 digits. An
        # unfamiliar format must never be called fraud.
        assert t._normalize_proof_id("315261", "receipt_other") == "315261"
        assert t._normalize_proof_id("200014602152926", "receipt_other") == "200014602152926"

    def test_absurdly_short_refused_everywhere(self):
        assert t._normalize_proof_id("2234", "receipt_other") == ""


class TestAcceptedProofNeedsEvidence:
    """'Never accept a proof you have not read' was prose. This is the tool guard,
    and it is what closes order-number guessing: a typed number with no image can
    no longer be accepted or complete a gig."""

    def test_accept_without_evidence_refused(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool(_store_args(source_url="", proof_link="")))
        assert "error" in out and out["error"]
        assert "source_url" in out["error"]

    def test_proof_link_also_satisfies_it(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = None
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            out = json.loads(t.crwd_db_tool(_store_args(
                source_url="", proof_link="https://amazon.com/gp/customer-reviews/RABC")))
        assert out["stored"] is True

    def test_rejected_and_needs_human_are_exempt(self, monkeypatch):
        # We must always be able to record a proof we could not read.
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = None
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            for status, code in (("rejected", "unreadable"), ("needs_human", "no_identifier")):
                out = json.loads(t.crwd_db_tool(_store_args(
                    status=status, reason_code=code, reason="could not read it",
                    source_url="", proof_link="")))
                assert out["stored"] is True, status


class TestProofInfoAndProductFields:
    def test_proof_info_lands_under_metadata(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = None
        info = {
            "merchant_name": "TARGET", "store_location": "Dallas, TX 75231",
            "purchase_date": "2026-07-01", "total_amount": 86.65, "tax_amount": 6.6,
            "payment_method": "VISA ..1234",
            "line_items": [{"product_name": "SMOOTH LGND DEODORNT", "quantity": 1,
                            "price": 15.99, "amount": 15.99}],
        }
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            t.crwd_db_tool(_store_args(
                proof_info=info, product_name="Smooth Legend Deodorant",
                store_name="Target "))
        doc = coll.insert_one.call_args[0][0]
        assert doc["metadata"]["proof_info"] == info
        assert doc["product_name"] == "Smooth Legend Deodorant"
        # Normalized on write: the data holds both 'Target' and 'Target '.
        assert doc["store_name"] == "target"
        assert "extracted" not in doc

    def test_ugc_proof_info_shape_round_trips(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = None
        info = {"platform": "tiktok", "handle": "@alice", "posted_at": "2026-07-02",
                "likes": 120, "comments": 8, "views": 3400, "caption": "love these"}
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            t.crwd_db_tool(_store_args(
                proof_id="https://www.tiktok.com/@alice/video/7311123",
                proof_type="ugc_link", proof_link="https://www.tiktok.com/@alice/video/7311123",
                proof_info=info))
        assert coll.insert_one.call_args[0][0]["metadata"]["proof_info"]["likes"] == 120

    def test_proof_starts_unscored(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.find_one.return_value = None
        with patch.object(t, "_db", return_value=_proofs_db(coll)):
            t.crwd_db_tool(_store_args())
        assert coll.insert_one.call_args[0][0]["risk_scored"] is False


class TestMarkProofRiskScored:
    """The risk skill runs every turn against a delta-only score with no history.
    Without this guard a second pass silently doubles a member's risk."""

    def test_marks_the_record(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.update_one.return_value = MagicMock(matched_count=1)
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "mark_proof_risk_scored",
                "proof_record_id": "69b8614f1083b9302fd0a9a7",
            }))
        assert out["marked"] is True
        assert out["already_marked"] is False
        # The narrowest possible write: one boolean, nothing else touched.
        update = coll.update_one.call_args[0][1]["$set"]
        assert update["risk_scored"] is True
        assert set(update) == {"risk_scored", "updated_at"}
        # "Already marked" must be decided by the FILTER, not by modified_count:
        # the $set bumps updated_at, so the doc always changes and modified_count
        # is never 0. Real Mongo caught this; a mock cannot.
        assert coll.update_one.call_args[0][0]["risk_scored"] == {"$ne": True}

    def test_second_pass_reports_already_marked(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.update_one.return_value = MagicMock(matched_count=0)
        coll.count_documents.return_value = 1  # the record exists, just already marked
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "mark_proof_risk_scored",
                "proof_record_id": "69b8614f1083b9302fd0a9a7",
            }))
        assert out["already_marked"] is True

    def test_bad_id_refused(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        for bad in ("", "not-an-id"):
            out = json.loads(t.crwd_db_tool({
                "action": "mark_proof_risk_scored", "proof_record_id": bad}))
            assert "error" in out and out["error"]

    def test_unknown_record_refused(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll = MagicMock()
        coll.update_one.return_value = MagicMock(matched_count=0)
        coll.count_documents.return_value = 0  # no such record at all
        with patch.object(t, "_db", return_value=_fake_db({"proof_submissions": coll})):
            out = json.loads(t.crwd_db_tool({
                "action": "mark_proof_risk_scored",
                "proof_record_id": "69b8614f1083b9302fd0a9a7"}))
        assert "error" in out and out["error"]


class TestGetUserProofs:
    """The member-centric read. find_proof is keyed on a proof id, so it answers
    "who else touched this purchase" -- not "what have I submitted?", which is what
    a coach is actually asked. Without this the agent asked a member for an order
    number it had already stored five times."""

    def _db_with(self, rows):
        coll = MagicMock()
        coll.find.return_value.sort.return_value.limit.return_value = rows
        return coll, _fake_db({"proof_submissions": coll})

    def test_returns_everything_for_a_member(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll, db = self._db_with([_proof_doc(), _proof_doc(status="rejected")])
        with patch.object(t, "_db", return_value=db):
            out = json.loads(t.crwd_db_tool({
                "action": "get_user_proofs", "user_id": "user-a"}))
        assert out["_type"] == "crwd_user_proofs"
        assert out["count"] == 2
        # No proof_id needed -- that was the whole bug.
        assert coll.find.call_args[0][0] == {"user_id": "user-a"}

    def test_narrows_to_one_gig(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll, db = self._db_with([_proof_doc()])
        with patch.object(t, "_db", return_value=db):
            t.crwd_db_tool({
                "action": "get_user_proofs", "user_id": "user-a", "crwd_id": "gig-1"})
        assert coll.find.call_args[0][0] == {"user_id": "user-a", "crwd_id": "gig-1"}

    def test_gig_id_is_accepted_as_an_alias(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll, db = self._db_with([])
        with patch.object(t, "_db", return_value=db):
            t.crwd_db_tool({
                "action": "get_user_proofs", "user_id": "user-a", "gig_id": "gig-1"})
        assert coll.find.call_args[0][0]["crwd_id"] == "gig-1"

    def test_status_filter(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll, db = self._db_with([_proof_doc()])
        with patch.object(t, "_db", return_value=db):
            t.crwd_db_tool({
                "action": "get_user_proofs", "user_id": "user-a", "status": "accepted"})
        assert coll.find.call_args[0][0]["status"] == "accepted"

    def test_newest_first_and_capped(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll, db = self._db_with([])
        with patch.object(t, "_db", return_value=db):
            t.crwd_db_tool({
                "action": "get_user_proofs", "user_id": "user-a", "limit": 500})
        coll.find.return_value.sort.assert_called_with("created_at", -1)
        coll.find.return_value.sort.return_value.limit.assert_called_with(t._HARD_LIMIT)

    def test_no_proofs_is_an_honest_empty_not_an_error(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        coll, db = self._db_with([])
        with patch.object(t, "_db", return_value=db):
            out = json.loads(t.crwd_db_tool({
                "action": "get_user_proofs", "user_id": "user-a"}))
        assert out["items"] == [] and out["count"] == 0 and out["error"] is None

    def test_requires_user_id(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({"action": "get_user_proofs", "user_id": ""}))
        assert "error" in out and out["error"]

    def test_invalid_status_refused(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        out = json.loads(t.crwd_db_tool({
            "action": "get_user_proofs", "user_id": "user-a", "status": "approved"}))
        assert "error" in out and out["error"]

    def test_action_is_registered_in_the_schema(self):
        assert "get_user_proofs" in t.CRWD_DB_SCHEMA["parameters"]["properties"]["action"]["enum"]


class TestArchivedGigsAreInvisible:
    """The app hides archived gigs, and real archived rows still carry
    status "Active" with a future end_date -- so isArchived is load-bearing.
    Without it the coach told a member they had 3 active gigs while the app
    showed 1, including a gig they were never enrolled in."""

    def test_open_gig_filter_excludes_archived(self):
        f = t._open_gig_filter()
        assert f["isArchived"] == {"$ne": True}

    def test_status_skips_membership_whose_gig_is_archived(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        live_id = t._oid("69b8614f1083b9302fd0a9a7")
        members = MagicMock()
        members.find.return_value = [
            {"member": "user-a", "crwd_id": live_id, "isAccepted": True},
            {"member": "user-a", "crwd_id": t._oid("6a54f6d3a64282067a333a6b"),
             "isAccepted": True},
        ]
        crwds = MagicMock()
        # The join itself excludes archived rows -- only the live gig comes back.
        crwds.find.return_value = [
            {"_id": live_id, "name": "Live Gig", "gig_type": "web_based"},
        ]
        empty = MagicMock()
        # Progress collections are queried as find().sort().limit() -> iterable.
        empty.find.return_value.sort.return_value.limit.return_value = []
        db = _fake_db({
            "added_crwd_members": members, "crwds": crwds,
            "user_product_purchases": empty, "gig_store_orders": empty,
            "gig_product_reviews": empty, "order_receipt_reviews": empty,
        })
        db.list_collection_names = MagicMock(return_value=[])
        with patch.object(t, "_db", return_value=db):
            out = json.loads(t.crwd_db_tool({
                "action": "get_user_gig_status", "user_id": "user-a"}))
        assert out["count"] == 1
        # And the join was asked to exclude archived gigs.
        assert crwds.find.call_args[0][0]["isArchived"] == {"$ne": True}

    def test_get_user_gigs_drops_archived_instead_of_emitting_gig_none(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        members = MagicMock()
        members.find.return_value.limit.return_value = [
            {"member": "user-a", "crwd_id": t._oid("6a54f6d3a64282067a333a6b")},
        ]
        crwds = MagicMock()
        crwds.find.return_value = []  # its gig is archived -> excluded by the join
        db = _fake_db({"added_crwd_members": members, "crwds": crwds})
        with patch.object(t, "_db", return_value=db):
            out = json.loads(t.crwd_db_tool({
                "action": "get_user_gigs", "user_id": "user-a"}))
        assert out["items"] == []
