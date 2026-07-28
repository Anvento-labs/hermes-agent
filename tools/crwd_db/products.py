"""Products, app receipts, and notifications."""

from __future__ import annotations

import json
from typing import Any, Dict

from tools.registry import tool_error

from tools.crwd_db import connection as _conn
from tools.crwd_db.connection import (
    _COLL_CRWDS,
    _COLL_NOTIFS,
    _COLL_PURCHASES,
    _COLL_RECEIPTS,
    _GIG_FIELDS,
    _HARD_LIMIT,
    _MAX_TIME_MS,
    _NOTIF_FIELDS,
    _PURCHASE_FIELDS,
    _RECEIPT_FIELDS,
    _id_values,
    _oid,
)
from tools.crwd_db.serialize import _serialize_docs
from tools.crwd_db.stage import _collect_buy_products

def _get_user_products(user_id: str, limit: int = 10, crwd_id: str = "") -> str:
    """Products a member is approved to buy for a gig (name + buy link).

    When ``crwd_id`` is set, prefer that gig's full ``gig_stores.products``
    catalog (and any matching purchase rows) so multi-SKU gigs list every
    product — not only the latest purchase or a single ``buy_link``.
    """
    user_id = (user_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for get_user_products")
    row_limit = max(1, min(int(limit or 10), _HARD_LIMIT))
    crwd_id = (crwd_id or "").strip()

    if crwd_id:
        # Multi-SKU gigs often exceed the generic default of 10.
        row_limit = max(1, min(int(limit or _HARD_LIMIT), _HARD_LIMIT))
        oid = _oid(crwd_id)
        gig = None
        if oid is not None:
            gig = _conn._db()[_COLL_CRWDS].find_one(
                {"_id": oid, "isDeleted": {"$ne": True}},
                _GIG_FIELDS,
                max_time_ms=_MAX_TIME_MS,
            )
        purchases = []
        purchase_filter: Dict[str, Any] = {
            "user_id": {"$in": _id_values(user_id)},
            "isDeleted": {"$ne": True},
        }
        if oid is not None:
            purchase_filter["crwd_id"] = {"$in": [oid, crwd_id]}
        else:
            purchase_filter["crwd_id"] = crwd_id
        purchases = list(
            _conn._db()[_COLL_PURCHASES].find(
                purchase_filter, _PURCHASE_FIELDS, max_time_ms=_MAX_TIME_MS
            )
        )
        items = _collect_buy_products(gig or {}, purchases)[:row_limit]
        return json.dumps(
            {"_type": "user_products", "items": items, "crwd_id": crwd_id, "error": None},
            ensure_ascii=False,
        )

    cursor = (
        _conn._db()[_COLL_PURCHASES]
        .find(
            {"user_id": {"$in": _id_values(user_id)}, "isDeleted": {"$ne": True}},
            _PURCHASE_FIELDS, max_time_ms=_MAX_TIME_MS,
        )
        .sort("purchasedAt", -1)
        .limit(row_limit)
    )
    items = _serialize_docs(list(cursor))
    return json.dumps(
        {"_type": "user_products", "items": items, "error": None}, ensure_ascii=False
    )


def _get_user_receipts(user_id: str, limit: int = 10) -> str:
    """Receipt/proof upload validation status (pass/fail + reason)."""
    user_id = (user_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for get_user_receipts")
    row_limit = max(1, min(int(limit or 10), _HARD_LIMIT))
    cursor = (
        _conn._db()[_COLL_RECEIPTS]
        .find(
            {"user_id": {"$in": _id_values(user_id)}},
            _RECEIPT_FIELDS, max_time_ms=_MAX_TIME_MS,
        )
        .sort("created_at", -1)
        .limit(row_limit)
    )
    items = _serialize_docs(list(cursor))
    return json.dumps(
        {"_type": "user_receipts", "items": items, "error": None}, ensure_ascii=False
    )


def _get_user_notifications(user_id: str, limit: int = 10) -> str:
    """Recent account notifications for a member (secret fields excluded)."""
    user_id = (user_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for get_user_notifications")
    row_limit = max(1, min(int(limit or 10), _HARD_LIMIT))
    cursor = (
        _conn._db()[_COLL_NOTIFS]
        .find(
            {"to": {"$in": _id_values(user_id)}, "isDeleted": {"$ne": True}},
            _NOTIF_FIELDS, max_time_ms=_MAX_TIME_MS,
        )
        .sort("createdAt", -1)
        .limit(row_limit)
    )
    items = _serialize_docs(list(cursor))
    return json.dumps(
        {"_type": "user_notifications", "items": items, "error": None},
        ensure_ascii=False,
    )
