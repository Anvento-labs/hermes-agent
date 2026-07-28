"""Gig stage / next_step computation and get_user_gig_status."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from tools.crwd_urls import attach_gig_url
from tools.registry import tool_error

from tools.crwd_db import connection as _conn
from tools.crwd_db.connection import (
    _COLL_CRWDS,
    _COLL_GIG_PRODUCT_REVIEWS,
    _COLL_GIG_STORE_ORDERS,
    _COLL_MEMBERS,
    _COLL_ORDER_RECEIPT_REVIEWS,
    _COLL_PURCHASES,
    _GIG_FIELDS,
    _HARD_LIMIT,
    _MATCH_FLOOR,
    _MAX_TIME_MS,
    _MEMBER_FIELDS,
    _PURCHASE_FIELDS,
    _id_values,
    _oid,
)

from tools.crwd_db.gigs import _normalize, _score
from tools.crwd_db.membership import (
    _end_date_sort_key,
    _gig_type_key,
    _joined_member_filter,
    _member_or_filter,
    _sort_members_by_gig_end_date,
    _waitlisted_member_filter,
)
from tools.crwd_db.serialize import _serialize_doc

def _first_buy_link(gig: Dict[str, Any], purchases: List[Dict[str, Any]]) -> Optional[str]:
    products = _collect_buy_products(gig, purchases)
    if not products:
        return None
    return products[0].get("product_url")


def _collect_buy_products(
    gig: Dict[str, Any],
    purchases: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Return all buyable products (name + url), purchases first, then gig catalog.

    Dedupes by ``product_url``. Used so product-link answers can list every
    SKU instead of only the first ``buy_link``.
    """
    out: List[Dict[str, str]] = []
    seen: set[str] = set()

    def _add(name: Any, url: Any) -> None:
        link = str(url or "").strip()
        if not link or link in seen:
            return
        seen.add(link)
        title = str(name or "").strip() or "Buy here"
        out.append({"name": title, "product_url": link})

    for row in purchases or []:
        _add(row.get("product_name") or row.get("name"), row.get("product_url"))
    for store in gig.get("gig_stores") or []:
        for product in store.get("products") or []:
            _add(product.get("name"), product.get("product_url"))
    return out


def compute_gig_stage(
    membership: Dict[str, Any],
    gig: Dict[str, Any],
    *,
    purchases: List[Dict[str, Any]],
    store_orders: List[Dict[str, Any]],
    product_reviews: List[Dict[str, Any]],
    order_receipt_reviews: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Derive machine-readable stage + coach-facing next_step for one membership."""
    gig_name = str(gig.get("name") or "this gig").strip()
    gig_type = _gig_type_key(gig)
    products = _collect_buy_products(gig, purchases)
    buy_link = products[0]["product_url"] if products else None

    is_accepted = membership.get("isAccepted")
    has_paid = membership.get("hasPaid")
    rejection = membership.get("rejectionReason") or membership.get("rejectionNotes")

    progress: Dict[str, Any] = {
        # NOT a purchase confirmation. A user_product_purchases row is written when
        # the member is *approved to join* (every row in the data is
        # source: "join_approved", and purchasedAt is just createdAt on most of
        # them) -- it records which product they may buy and the buy link, not that
        # they bought anything. Naming it purchase_confirmed made the coach tell a
        # member "the system registered that you ordered the product" when the
        # system had registered no such thing.
        "product_assigned": bool(purchases),
        "receipt_submitted": False,
        "receipt_approved": False,
        "review_submitted": False,
        "review_approved": False,
    }

    if rejection:
        return {
            "stage": "rejected",
            "next_step": (
                f"Your enrollment for {gig_name} was not approved. "
                "I'll loop in a human who can help."
            ),
            "progress": progress,
            "buy_link": buy_link,
            "handoff_recommended": True,
        }

    if is_accepted is False:
        return {
            "stage": "request_pending_approval",
            "next_step": (
                f"Your application for {gig_name} is pending approval — we'll "
                "notify you once you're accepted into the gig."
            ),
            "progress": progress,
            "buy_link": buy_link,
            "handoff_recommended": False,
        }

    if not purchases:
        link_hint = f" Use your buy link: {buy_link}." if buy_link else ""
        return {
            "stage": "need_purchase",
            "next_step": (
                f"You're in {gig_name} — next, buy the product using "
                f"the gig's link in the app.{link_hint}"
            ),
            "progress": progress,
            "buy_link": buy_link,
            "handoff_recommended": False,
        }

    progress["product_assigned"] = True

    if gig_type == "irl":
        if not store_orders:
            return {
                "stage": "need_receipt",
                "next_step": (
                    f"For {gig_name}, visit the store, buy the product, then send "
                    "your receipt right here in the chat."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": False,
            }
        latest_order = store_orders[0]
        progress["receipt_submitted"] = bool(
            latest_order.get("receipt_file") or latest_order.get("receipt_files")
        )
        if latest_order.get("rejectionReason"):
            return {
                "stage": "receipt_rejected",
                "next_step": (
                    f"Your receipt for {gig_name} needs a human review — "
                    "I'll connect you with support."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": True,
            }
        if progress["receipt_submitted"] and not latest_order.get("isApproved"):
            progress["receipt_submitted"] = True
            return {
                "stage": "receipt_review",
                "next_step": (
                    f"Your receipt for {gig_name} is being reviewed — "
                    "we'll notify you when it's approved."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": False,
            }
        if latest_order.get("isApproved"):
            progress["receipt_approved"] = True

        if not product_reviews:
            return {
                "stage": "need_review",
                "next_step": (
                    f"Receipt approved for {gig_name}! Next: post your review, then "
                    "send it here in the chat."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": False,
            }
        latest_review = product_reviews[0]
        progress["review_submitted"] = bool(
            latest_review.get("review_link") or latest_review.get("ugc_post_link")
        )
        if latest_review.get("rejectionReason"):
            return {
                "stage": "review_rejected",
                "next_step": (
                    f"Your review submission for {gig_name} needs support — "
                    "I'll loop in a human."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": True,
            }
        if progress["review_submitted"] and not latest_review.get("isApproved"):
            return {
                "stage": "review_review",
                "next_step": (
                    f"Your review for {gig_name} is under review — "
                    "we'll notify you when it's approved."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": False,
            }
        if latest_review.get("isApproved"):
            progress["review_approved"] = True

    else:
        order_rows = [r for r in order_receipt_reviews if r.get("type") == "order_receipt"]
        review_rows = [r for r in order_receipt_reviews if r.get("type") == "review"]

        if not order_rows:
            return {
                "stage": "need_receipt",
                "next_step": (
                    f"For {gig_name}, order the product, then send your order "
                    "receipt screenshot right here in the chat."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": False,
            }
        latest_order = order_rows[0]
        progress["receipt_submitted"] = bool(latest_order.get("order_receipt_file"))
        if not latest_order.get("isOrderApproved") and progress["receipt_submitted"]:
            return {
                "stage": "receipt_review",
                "next_step": (
                    f"Your order receipt for {gig_name} is being reviewed — "
                    "we'll notify you when it's approved."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": False,
            }
        if latest_order.get("isOrderApproved"):
            progress["receipt_approved"] = True

        if not review_rows:
            return {
                "stage": "need_review",
                "next_step": (
                    f"Order approved for {gig_name}! Leave your review, then send "
                    "the review screenshot here in the chat."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": False,
            }
        latest_review = review_rows[0]
        progress["review_submitted"] = bool(
            latest_review.get("review") or latest_review.get("review_file")
        )
        if progress["review_submitted"] and str(latest_review.get("status") or "").lower() not in (
            "approved", "complete", "completed",
        ):
            if not latest_review.get("isOrderApproved"):
                return {
                    "stage": "review_review",
                    "next_step": (
                        f"Your review for {gig_name} is under review — "
                        "we'll notify you when it's approved."
                    ),
                    "progress": progress,
                    "buy_link": buy_link,
                    "handoff_recommended": False,
                }
        if latest_review.get("isOrderApproved") or str(
            latest_review.get("status") or ""
        ).lower() in ("approved", "complete", "completed"):
            progress["review_approved"] = True

    if has_paid:
        return {
            "stage": "paid",
            "next_step": (
                f"Payout for {gig_name} has been issued. If you don't see it yet, "
                "check your Dot payout link or ask me to loop in support."
            ),
            "progress": progress,
            "buy_link": buy_link,
            "handoff_recommended": False,
        }

    return {
        "stage": "awaiting_payout",
        "next_step": (
            f"All proof for {gig_name} is approved — payout typically lands in "
            "1–2 business days via Dot."
        ),
        "progress": progress,
        "buy_link": buy_link,
        "handoff_recommended": False,
    }


def _progress_for_crwd(
    user_id: str,
    crwd_id: Any,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch purchase + proof rows for one gig."""
    id_values = _id_values(user_id)
    crwd_values = [crwd_id]
    if isinstance(crwd_id, str):
        oid = _oid(crwd_id)
        if oid is not None:
            crwd_values = [oid, crwd_id]

    db = _conn._db()
    purchases = list(
        db[_COLL_PURCHASES]
        .find(
            {
                "user_id": {"$in": id_values},
                "crwd_id": {"$in": crwd_values},
                "isDeleted": {"$ne": True},
            },
            _PURCHASE_FIELDS,
            max_time_ms=_MAX_TIME_MS,
        )
        .sort("purchasedAt", -1)
        .limit(5)
    )
    store_orders = list(
        db[_COLL_GIG_STORE_ORDERS]
        .find(
            {"user_id": {"$in": id_values}, "crwd_id": {"$in": crwd_values}},
            {
                "receipt_file": 1, "receipt_files": 1, "isApproved": 1,
                "rejectionReason": 1, "reviewedAt": 1,
            },
            max_time_ms=_MAX_TIME_MS,
        )
        .sort("reviewedAt", -1)
        .limit(5)
    )
    product_reviews = list(
        db[_COLL_GIG_PRODUCT_REVIEWS]
        .find(
            {"user_id": {"$in": id_values}, "crwd_id": {"$in": crwd_values}},
            {
                "review_link": 1, "ugc_post_link": 1, "isApproved": 1,
                "rejectionReason": 1, "reviewedAt": 1,
            },
            max_time_ms=_MAX_TIME_MS,
        )
        .sort("reviewedAt", -1)
        .limit(5)
    )
    order_receipt_reviews = list(
        db[_COLL_ORDER_RECEIPT_REVIEWS]
        .find(
            {
                "order_generated_by": {"$in": id_values},
                "crwd_id": {"$in": crwd_values},
            },
            {
                "type": 1, "order_receipt_file": 1, "review": 1, "review_file": 1,
                "isOrderApproved": 1, "status": 1,
            },
            max_time_ms=_MAX_TIME_MS,
        )
        .limit(10)
    )
    return {
        "purchases": purchases,
        "store_orders": store_orders,
        "product_reviews": product_reviews,
        "order_receipt_reviews": order_receipt_reviews,
    }


def _filter_membership_by_gig_ref(
    members: List[Dict[str, Any]],
    gigs_by_id: Dict[str, Dict[str, Any]],
    *,
    crwd_id: str = "",
    gig_name: str = "",
) -> List[Dict[str, Any]]:
    """Narrow memberships to one gig when crwd_id or fuzzy gig_name is provided."""
    crwd_id = (crwd_id or "").strip()
    gig_name = (gig_name or "").strip()
    if crwd_id:
        return [m for m in members if str(m.get("crwd_id")) == crwd_id]
    if not gig_name:
        return members
    query_norm = _normalize(gig_name)
    matched = []
    for m in members:
        gid = str(m.get("crwd_id"))
        gig = gigs_by_id.get(gid) or {}
        name = gig.get("name") or ""
        if _score(query_norm, name) >= _MATCH_FLOOR:
            matched.append(m)
    return matched or members


def build_user_gig_status(
    user_id: str,
    *,
    crwd_id: str = "",
    gig_name: str = "",
    include_waitlisted: bool = False,
    limit: int = _HARD_LIMIT,
) -> Dict[str, Any]:
    """Build gig status payload (dict) for one member — used by tool + prefetch hook."""
    user_id = (user_id or "").strip()
    if not user_id:
        return {"_type": "user_gig_status", "items": [], "error": "user_id is required"}

    row_limit = max(1, min(int(limit or _HARD_LIMIT), _HARD_LIMIT))
    db = _conn._db()

    member_filter = _joined_member_filter(user_id)
    members = list(
        db[_COLL_MEMBERS]
        .find(member_filter, _MEMBER_FIELDS, max_time_ms=_MAX_TIME_MS)
    )

    waitlisted: List[Dict[str, Any]] = []
    if include_waitlisted:
        waitlisted = list(
            db[_COLL_MEMBERS]
            .find(_waitlisted_member_filter(user_id), _MEMBER_FIELDS, max_time_ms=_MAX_TIME_MS)
        )
        members = members + waitlisted

    crwd_ids = [m["crwd_id"] for m in members if m.get("crwd_id") is not None]
    gigs_by_id: Dict[str, Dict[str, Any]] = {}
    if crwd_ids:
        for gig in db[_COLL_CRWDS].find(
            # Same archived exclusion as the joins above: the status loop below
            # skips memberships whose gig is absent, which is exactly the app's
            # Active-tab behaviour.
            {"_id": {"$in": crwd_ids}, "isArchived": {"$ne": True}},
            _GIG_FIELDS, max_time_ms=_MAX_TIME_MS,
        ):
            gigs_by_id[str(gig["_id"])] = gig

    members = _filter_membership_by_gig_ref(
        members, gigs_by_id, crwd_id=crwd_id, gig_name=gig_name
    )
    members = _sort_members_by_gig_end_date(members, gigs_by_id)[:row_limit]

    items = []
    for m in members:
        gid = m.get("crwd_id")
        gig = gigs_by_id.get(str(gid)) if gid is not None else None
        if not gig:
            continue
        prog = _progress_for_crwd(user_id, gid)
        stage_info = compute_gig_stage(
            m, gig,
            purchases=prog["purchases"],
            store_orders=prog["store_orders"],
            product_reviews=prog["product_reviews"],
            order_receipt_reviews=prog["order_receipt_reviews"],
        )
        products = _collect_buy_products(gig, prog["purchases"])
        items.append(attach_gig_url({
            "gig_id": str(gid),
            "gig_name": gig.get("name"),
            "gig_type": _gig_type_key(gig),
            "end_date": _serialize_doc(gig.get("end_date")),
            "membership": {
                "isAccepted": m.get("isAccepted"),
                "isApproved": m.get("isApproved"),
                "hasPaid": m.get("hasPaid"),
                "status": m.get("status"),
            },
            "progress": stage_info["progress"],
            "stage": stage_info["stage"],
            "next_step": stage_info["next_step"],
            "buy_link": stage_info.get("buy_link"),
            "products": products,
            "handoff_recommended": stage_info.get("handoff_recommended", False),
        }, inline_name=True))

    return {
        "_type": "user_gig_status",
        "items": items,
        "active_gigs": items,
        "count": len(items),
        "error": None,
    }


def _get_user_gig_status(
    user_id: str,
    crwd_id: str = "",
    gig_name: str = "",
    include_waitlisted: bool = False,
    limit: int = _HARD_LIMIT,
) -> str:
    payload = build_user_gig_status(
        user_id,
        crwd_id=crwd_id,
        gig_name=gig_name,
        include_waitlisted=include_waitlisted,
        limit=limit,
    )
    if payload.get("error"):
        return tool_error(str(payload["error"]))
    return json.dumps(payload, ensure_ascii=False)
