"""Tests for crwd_db gig status / next-step state machine."""

from __future__ import annotations

import datetime as dt
import json
from unittest.mock import MagicMock, patch

import pytest

from tools import crwd_db_tool as t


def _gig(**kwargs):
    base = {"name": "Test Gig", "gig_type": "web_based", "gig_stores": []}
    base.update(kwargs)
    return base


def _membership(**kwargs):
    base = {
        "isAccepted": True,
        "isApproved": True,
        "hasPaid": False,
        "status": "Active",
    }
    base.update(kwargs)
    return base


class TestComputeGigStage:
    def test_request_pending_approval(self):
        out = t.compute_gig_stage(
            _membership(isAccepted=False),
            _gig(),
            purchases=[], store_orders=[], product_reviews=[], order_receipt_reviews=[],
        )
        assert out["stage"] == "request_pending_approval"
        assert "pending approval" in out["next_step"].lower()
        assert "waitlist" not in out["next_step"].lower()

    def test_rejected_handoff(self):
        out = t.compute_gig_stage(
            _membership(rejectionReason="duplicate"),
            _gig(),
            purchases=[], store_orders=[], product_reviews=[], order_receipt_reviews=[],
        )
        assert out["stage"] == "rejected"
        assert out["handoff_recommended"] is True

    def test_is_approved_on_membership_ignored(self):
        out = t.compute_gig_stage(
            _membership(isAccepted=True, isApproved=False),
            _gig(),
            purchases=[], store_orders=[], product_reviews=[], order_receipt_reviews=[],
        )
        assert out["stage"] == "need_purchase"

    def test_need_purchase_includes_buy_link(self):
        gig = _gig(gig_stores=[{"products": [{"product_url": "https://buy.example/p"}]}])
        out = t.compute_gig_stage(
            _membership(),
            gig,
            purchases=[], store_orders=[], product_reviews=[], order_receipt_reviews=[],
        )
        assert out["stage"] == "need_purchase"
        assert "https://buy.example/p" in out["next_step"]

    def test_irl_need_receipt(self):
        out = t.compute_gig_stage(
            _membership(),
            _gig(gig_type="irl"),
            purchases=[{"product_url": "http://u"}],
            store_orders=[],
            product_reviews=[],
            order_receipt_reviews=[],
        )
        assert out["stage"] == "need_receipt"

    def test_irl_receipt_review(self):
        out = t.compute_gig_stage(
            _membership(),
            _gig(gig_type="irl"),
            purchases=[{}],
            store_orders=[{"receipt_file": "r.jpg", "isApproved": False}],
            product_reviews=[],
            order_receipt_reviews=[],
        )
        assert out["stage"] == "receipt_review"

    def test_irl_need_review_after_receipt_approved(self):
        out = t.compute_gig_stage(
            _membership(),
            _gig(gig_type="irl"),
            purchases=[{}],
            store_orders=[{"receipt_file": "r.jpg", "isApproved": True}],
            product_reviews=[],
            order_receipt_reviews=[],
        )
        assert out["stage"] == "need_review"

    def test_web_need_receipt(self):
        out = t.compute_gig_stage(
            _membership(),
            _gig(gig_type="web_based"),
            purchases=[{}],
            store_orders=[],
            product_reviews=[],
            order_receipt_reviews=[],
        )
        assert out["stage"] == "need_receipt"

    def test_web_need_review_after_order_approved(self):
        out = t.compute_gig_stage(
            _membership(),
            _gig(gig_type="web_based"),
            purchases=[{}],
            store_orders=[],
            product_reviews=[],
            order_receipt_reviews=[
                {"type": "order_receipt", "order_receipt_file": "o.png", "isOrderApproved": True},
            ],
        )
        assert out["stage"] == "need_review"

    def test_awaiting_payout(self):
        out = t.compute_gig_stage(
            _membership(),
            _gig(gig_type="web_based"),
            purchases=[{}],
            store_orders=[],
            product_reviews=[],
            order_receipt_reviews=[
                {"type": "order_receipt", "order_receipt_file": "o.png", "isOrderApproved": True},
                {"type": "review", "review": "great", "isOrderApproved": True, "status": "approved"},
            ],
        )
        assert out["stage"] == "awaiting_payout"

    def test_paid(self):
        out = t.compute_gig_stage(
            _membership(hasPaid=True),
            _gig(gig_type="web_based"),
            purchases=[{}],
            store_orders=[],
            product_reviews=[],
            order_receipt_reviews=[
                {"type": "order_receipt", "isOrderApproved": True},
                {"type": "review", "review": "great", "status": "approved"},
            ],
        )
        assert out["stage"] == "paid"


class TestJoinedMemberFilter:
    def test_gates_on_is_accepted(self):
        filt = t._joined_member_filter("69a6f191cb29b0b371b3a156")
        assert "$and" in filt
        or_clause = next(
            c for c in filt["$and"]
            if "$or" in c and any("isAccepted" in str(item) for item in c["$or"])
        )
        assert {"isAccepted": True} in or_clause["$or"]
        assert not any("isApproved" in str(c) for c in or_clause["$or"])


class TestBuildUserGigStatus:
    def test_requires_user_id(self):
        out = t.build_user_gig_status("")
        assert out["error"] == "user_id is required"
        assert out["items"] == []

    def test_end_to_end_with_mocks(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        user_id = "69a6f191cb29b0b371b3a156"
        member_oid = t._oid(user_id)
        gig_oid = t._oid("69e6a4d6cea992cbda22b381")

        mock_members = MagicMock()
        mock_members.find.return_value = [
            {
                "member": member_oid,
                "crwd_id": gig_oid,
                "isAccepted": True,
                "isApproved": True,
                "hasPaid": False,
                "status": "Active",
            },
        ]

        mock_crwds = MagicMock()
        mock_crwds.find.return_value = [
            {"_id": gig_oid, "name": "Pul Tool", "gig_type": "web_based", "gig_stores": []},
        ]

        mock_purchases = MagicMock()
        pc = MagicMock()
        mock_purchases.find.return_value = pc
        pc.sort.return_value = pc
        pc.limit.return_value = []

        mock_store = MagicMock()
        sc = MagicMock()
        mock_store.find.return_value = sc
        sc.sort.return_value = sc
        sc.limit.return_value = []

        mock_reviews = MagicMock()
        rc = MagicMock()
        mock_reviews.find.return_value = rc
        rc.sort.return_value = rc
        rc.limit.return_value = []

        mock_orr = MagicMock()
        oc = MagicMock()
        mock_orr.find.return_value = oc
        oc.limit.return_value = []

        with patch.object(
            t,
            "_db",
            return_value={
                "added_crwd_members": mock_members,
                "crwds": mock_crwds,
                "user_product_purchases": mock_purchases,
                "gig_store_orders": mock_store,
                "gig_product_reviews": mock_reviews,
                "order_receipt_reviews": mock_orr,
            },
        ):
            out = t.build_user_gig_status(user_id)

        assert out["_type"] == "user_gig_status"
        assert len(out["items"]) == 1
        assert out["items"][0]["gig_name"] == "Pul Tool"
        assert out["items"][0]["stage"] == "need_purchase"

    def test_sorts_by_soonest_end_date(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        user_id = "69a6f191cb29b0b371b3a156"
        member_oid = t._oid(user_id)
        gig_near = t._oid("69e6a4d6cea992cbda22b3a1")
        gig_mid = t._oid("69e6a4d6cea992cbda22b3a2")
        gig_far = t._oid("69e6a4d6cea992cbda22b3a3")

        mock_members = MagicMock()
        mock_members.find.return_value = [
            {"member": member_oid, "crwd_id": gig_far, "isAccepted": True, "status": "Active"},
            {"member": member_oid, "crwd_id": gig_near, "isAccepted": True, "status": "Active"},
            {"member": member_oid, "crwd_id": gig_mid, "isAccepted": True, "status": "Active"},
        ]
        mock_crwds = MagicMock()
        mock_crwds.find.return_value = [
            {"_id": gig_near, "name": "Soon", "gig_type": "web_based", "gig_stores": [],
             "end_date": dt.datetime(2026, 7, 1)},
            {"_id": gig_mid, "name": "Mid", "gig_type": "web_based", "gig_stores": [],
             "end_date": dt.datetime(2026, 9, 1)},
            {"_id": gig_far, "name": "Far", "gig_type": "web_based", "gig_stores": [],
             "end_date": dt.datetime(2026, 12, 1)},
        ]
        empty_progress = {
            "purchases": [], "store_orders": [], "product_reviews": [],
            "order_receipt_reviews": [],
        }

        with patch.object(
            t,
            "_db",
            return_value={"added_crwd_members": mock_members, "crwds": mock_crwds},
        ), patch.object(t, "_progress_for_crwd", return_value=empty_progress):
            out = t.build_user_gig_status(user_id)

        assert [row["gig_name"] for row in out["items"]] == ["Soon", "Mid", "Far"]

    def test_default_limit_allows_more_than_five(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        user_id = "69a6f191cb29b0b371b3a156"
        member_oid = t._oid(user_id)
        members = []
        gigs = []
        for i in range(8):
            gid = t._oid(f"69e6a4d6cea992cbda22b3a{i:x}")
            members.append({
                "member": member_oid,
                "crwd_id": gid,
                "isAccepted": True,
                "status": "Active",
            })
            gigs.append({
                "_id": gid,
                "name": f"Gig {i}",
                "gig_type": "web_based",
                "gig_stores": [],
                "end_date": dt.datetime(2026, 7, i + 1),
            })

        mock_members = MagicMock()
        mock_members.find.return_value = members
        mock_crwds = MagicMock()
        mock_crwds.find.return_value = gigs
        empty_progress = {
            "purchases": [], "store_orders": [], "product_reviews": [],
            "order_receipt_reviews": [],
        }

        with patch.object(
            t,
            "_db",
            return_value={"added_crwd_members": mock_members, "crwds": mock_crwds},
        ), patch.object(t, "_progress_for_crwd", return_value=empty_progress):
            out = t.build_user_gig_status(user_id)

        assert len(out["items"]) == 8


class TestEndDateSorting:
    def test_sort_members_by_gig_end_date(self):
        oid_near = t._oid("69e6a4d6cea992cbda22b3a1")
        oid_mid = t._oid("69e6a4d6cea992cbda22b3a2")
        oid_far = t._oid("69e6a4d6cea992cbda22b3a3")
        members = [
            {"crwd_id": oid_far},
            {"crwd_id": oid_near},
            {"crwd_id": oid_mid},
        ]
        gigs_by_id = {
            str(oid_near): {"end_date": dt.datetime(2026, 7, 1)},
            str(oid_mid): {"end_date": dt.datetime(2026, 9, 1)},
            str(oid_far): {"end_date": dt.datetime(2026, 12, 1)},
        }
        sorted_members = t._sort_members_by_gig_end_date(members, gigs_by_id)
        assert [str(m["crwd_id"]) for m in sorted_members] == [
            str(oid_near), str(oid_mid), str(oid_far),
        ]

    def test_missing_end_date_sorts_last(self):
        oid_dated = t._oid("69e6a4d6cea992cbda22b3a1")
        oid_missing = t._oid("69e6a4d6cea992cbda22b3a2")
        members = [{"crwd_id": oid_missing}, {"crwd_id": oid_dated}]
        gigs_by_id = {
            str(oid_dated): {"end_date": dt.datetime(2026, 7, 1)},
            str(oid_missing): {},
        }
        sorted_members = t._sort_members_by_gig_end_date(members, gigs_by_id)
        assert [str(m["crwd_id"]) for m in sorted_members] == [
            str(oid_dated), str(oid_missing),
        ]


class TestGetUserGigStatusAction:
    def test_router_action(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        with patch.object(
            t,
            "build_user_gig_status",
            return_value={"_type": "user_gig_status", "items": [], "error": None},
        ):
            out = json.loads(t.crwd_db_tool({
                "action": "get_user_gig_status",
                "user_id": "abc",
            }))
        assert out["_type"] == "user_gig_status"

    def test_get_user_gigs_uses_joined_filter(self, monkeypatch):
        monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://x/")
        mock_members = MagicMock()
        mock_members.find.return_value = []
        mock_crwds = MagicMock()
        mock_crwds.find.return_value = []
        with patch.object(
            t,
            "_db",
            return_value={"added_crwd_members": mock_members, "crwds": mock_crwds},
        ):
            t.crwd_db_tool({"action": "get_user_gigs", "user_id": "abc"})
        filt = mock_members.find.call_args[0][0]
        assert "$and" in filt


class TestNextStepNeverSendsProofToTheApp:
    """Proof is submitted in the coach chat, never in the CRWD app. These strings
    are emitted by the tool, so no skill can override them -- a member sent to the
    app hunts for a Submit Proof button that does not exist."""

    def _stage(self, **over):
        membership = {"isAccepted": True, "isApproved": True}
        membership.update(over.pop("membership", {}))
        gig = {"name": "Self Obsessed", "gig_type": "web_based"}
        gig.update(over.pop("gig", {}))
        return t.compute_gig_stage(
            membership, gig,
            purchases=over.get("purchases", []),
            store_orders=over.get("store_orders", []),
            product_reviews=over.get("product_reviews", []),
            order_receipt_reviews=over.get("order_receipt_reviews", []),
        )

    def test_web_need_receipt_points_at_the_chat(self):
        out = self._stage(purchases=[{"product_name": "Gummy"}])
        assert out["stage"] == "need_receipt"
        assert "in the app" not in out["next_step"]
        assert "chat" in out["next_step"]

    def test_irl_need_receipt_points_at_the_chat(self):
        out = self._stage(gig={"gig_type": "irl"}, purchases=[{"product_name": "X"}])
        assert "in the app" not in out["next_step"]
        assert "chat" in out["next_step"]

    def test_buying_still_points_at_the_app_buy_link(self):
        # Buying DOES happen via the app's buy link -- only proof moved to chat.
        out = self._stage(purchases=[])
        assert out["stage"] == "need_purchase"
        assert "in the app" in out["next_step"]


class TestProductAssignedIsNotAPurchase:
    """user_product_purchases rows are written at join-approval (every row is
    source: "join_approved"), so they record which product a member MAY buy -- not
    that they bought it. Calling the flag purchase_confirmed made the coach tell a
    member "the system registered that you ordered the product"."""

    def test_flag_is_named_for_what_it_means(self):
        out = t.compute_gig_stage(
            {"isAccepted": True, "isApproved": True},
            {"name": "Self Obsessed", "gig_type": "web_based"},
            purchases=[{"product_name": "Gummy", "source": "join_approved"}],
            store_orders=[], product_reviews=[], order_receipt_reviews=[],
        )
        assert out["progress"]["product_assigned"] is True
        # The old name is a claim the data cannot support.
        assert "purchase_confirmed" not in out["progress"]

    def test_join_approved_alone_never_claims_a_purchase(self):
        # A member approved 10 seconds ago who has bought nothing still gets a
        # user_product_purchases row. Nothing in the payload may say otherwise.
        out = t.compute_gig_stage(
            {"isAccepted": True, "isApproved": True},
            {"name": "Self Obsessed", "gig_type": "web_based"},
            purchases=[{"product_name": "Gummy", "source": "join_approved"}],
            store_orders=[], product_reviews=[], order_receipt_reviews=[],
        )
        blob = json.dumps(out).lower()
        assert "purchase is confirmed" not in blob
        assert "purchase_confirmed" not in blob

    def test_tool_description_warns_against_the_misread(self):
        desc = t.CRWD_DB_SCHEMA["description"]
        assert "product_assigned" in desc
        assert "NOT evidence" in desc
