"""Gig catalog lookups: list open gigs, fuzzy details, slim/full shaping."""

from __future__ import annotations

import difflib
import json
import re
from typing import Any, Dict, List, Optional

from tools.crwd_urls import attach_gig_url
from tools.registry import tool_error

from tools.crwd_db import connection as _conn
from tools.crwd_db.connection import (
    _COLL_CRWDS,
    _COLL_MEMBERS,
    _GIG_FIELDS,
    _GIG_TOPN_CAP,
    _HARD_LIMIT,
    _MATCH_FLOOR,
    _MAX_TIME_MS,
    _OBJECT_ID_IN_TEXT_RE,
    _id_values,
    _now,
    _oid,
)

from tools.crwd_db.serialize import _serialize_doc

# Noise words stripped before fuzzy scoring gig names.
_NOISE_WORDS = {
    "the", "a", "an", "gig", "campaign", "crwd", "and", "for", "with",
    "supplement", "supplements", "review", "reviews",
}

# What proof a store demands. These flags -- not ``type_of_work_proof``, which is
# null on almost every gig -- are the real proof spec, so surface them on the slim
# payload rather than only inside _full_gig's raw gig_stores dump.
_STORE_REQUIREMENT_FLAGS = (
    "requires_receipt", "requires_order_id", "requires_review_rating",
    "requires_review_receipt", "requires_review_link", "requires_tracking_id",
    "requires_store_address", "requires_ugc_post",
)

def _open_gig_filter() -> Dict[str, Any]:
    """Filter for currently-open gigs: not deleted, not archived, Active, end_date in future.

    ``isArchived`` matters as much as ``status``: the app hides archived gigs, and
    real archived rows still carry status "Active" with a future end_date. Without
    this the coach listed an archived gig a member was never enrolled in as one of
    "your active gigs" -- while the app showed one gig and the coach said three.

    Gigs with no end_date at all are open-ended: the app's Explore page shows
    them, so treat a missing/null end_date as "not expired" rather than
    filtering the gig out.
    """
    return {
        "isDeleted": {"$ne": True},
        "isArchived": {"$ne": True},
        "status": {"$regex": r"^active$", "$options": "i"},
        "$or": [
            {"end_date": {"$gte": _now()}},
            {"end_date": None},  # matches both null and missing field
        ],
    }


def _effective_payout(gig: Dict[str, Any]) -> Any:
    """Top-level payout when set, else the max per-store payout_amount."""
    payout = gig.get("payout")
    try:
        if payout and float(payout) > 0:
            return payout
    except (TypeError, ValueError):
        pass
    amounts = []
    for store in gig.get("gig_stores") or []:
        amt = store.get("payout_amount")
        if isinstance(amt, (int, float)):
            amounts.append(amt)
    return max(amounts) if amounts else payout


def _effective_product_funds(gig: Dict[str, Any]) -> Any:
    """Money given to buy the product, or None when the gig doesn't work that way.

    Same top-level-then-per-store shape as ``_effective_payout``. ``None`` is the
    default and today's only case: the member pays for the product themselves and
    keeps it, and the payout is a fee rather than a refund.
    """
    funds = gig.get("product_funds")
    try:
        if funds and float(funds) > 0:
            return funds
    except (TypeError, ValueError):
        pass
    amounts = []
    for store in gig.get("gig_stores") or []:
        amt = store.get("product_funds")
        if isinstance(amt, (int, float)):
            amounts.append(amt)
    return max(amounts) if amounts else None


# What proof a store demands. These flags -- not ``type_of_work_proof``, which is
# null on almost every gig -- are the real proof spec, so surface them on the slim
# payload rather than only inside _full_gig's raw gig_stores dump.
_STORE_REQUIREMENT_FLAGS = (
    "requires_receipt", "requires_order_id", "requires_review_rating",
    "requires_review_receipt", "requires_review_link", "requires_tracking_id",
    "requires_store_address", "requires_ugc_post",
)


def _store_requirements(store: Dict[str, Any]) -> Dict[str, bool]:
    return {flag: bool(store.get(flag)) for flag in _STORE_REQUIREMENT_FLAGS}


def _slim_gig(gig: Dict[str, Any]) -> Dict[str, Any]:
    """Clean, coach-friendly gig summary (product names + links included)."""
    stores = []
    for store in gig.get("gig_stores") or []:
        stores.append({
            "store_name": store.get("store_name"),
            "payout_amount": store.get("payout_amount"),
            "product_funds": store.get("product_funds"),
            "requirements": _store_requirements(store),
            "products": [
                {"name": p.get("name"), "product_url": p.get("product_url")}
                for p in (store.get("products") or [])
            ],
        })
    gig_id = gig.get("_id")
    out = {
        "_id": str(gig_id) if gig_id is not None else None,
        "name": gig.get("name"),
        "description": gig.get("description"),
        "gig_type": gig.get("gig_type"),
        "status": gig.get("status"),
        # Archived gigs are hidden from every listing; they only reach the model
        # via a direct get_gig_details lookup, and this flag is how it can say
        # "that gig is no longer active" instead of presenting it as live.
        "is_archived": bool(gig.get("isArchived")),
        "start_date": gig.get("start_date"),
        "end_date": gig.get("end_date"),
        "effective_payout": _effective_payout(gig),
        "effective_product_funds": _effective_product_funds(gig),
        "type_of_work_proof": gig.get("type_of_work_proof"),
        "image": gig.get("image"),
        "stores": stores,
    }
    if gig.get("gig_type") == "irl":
        out["location"] = {
            "address": gig.get("address"), "city": gig.get("city"),
            "state": gig.get("state"), "postal_code": gig.get("postal_code"),
        }
    return attach_gig_url(_serialize_doc(out), inline_name=True)


def _full_gig(gig: Dict[str, Any]) -> Dict[str, Any]:
    """Coach-facing gig payload with full store/terms/targeting detail."""
    out = _slim_gig(gig)
    out["terms_description"] = gig.get("terms_description")
    out["gig_stores"] = _serialize_doc(gig.get("gig_stores") or [])
    out["targeting_rules"] = _serialize_doc(gig.get("targeting_rules") or [])
    out["locations"] = _serialize_doc(gig.get("locations") or [])
    return out


def _find_gig_by_ref(gig_ref: str) -> Optional[Dict[str, Any]]:
    """Resolve one gig by _id or name; prefers open gigs, falls back to any non-deleted."""
    ref = (gig_ref or "").strip()
    if not ref:
        return None

    coll = _conn._db()[_COLL_CRWDS]
    oid_match = _OBJECT_ID_IN_TEXT_RE.search(ref)
    if oid_match:
        oid = _oid(oid_match.group(0))
        if oid is not None:
            doc = coll.find_one({"_id": oid, "isDeleted": {"$ne": True}}, _GIG_FIELDS, max_time_ms=_MAX_TIME_MS)
            if doc:
                return doc

    active_filter = _open_gig_filter()
    exact = coll.find_one(
        {**active_filter, "name": {"$regex": f"^{re.escape(ref)}$", "$options": "i"}},
        _GIG_FIELDS,
        max_time_ms=_MAX_TIME_MS,
    )
    if exact:
        return exact

    fuzzy = coll.find_one(
        {**active_filter, "name": {"$regex": re.escape(ref), "$options": "i"}},
        _GIG_FIELDS,
        max_time_ms=_MAX_TIME_MS,
    )
    if fuzzy:
        return fuzzy

    words = [w for w in re.split(r"\W+", ref) if len(w) >= 3]
    if len(words) >= 2:
        pattern = ".*".join(re.escape(word) for word in words)
        token_match = coll.find_one(
            {**active_filter, "name": {"$regex": pattern, "$options": "i"}},
            _GIG_FIELDS,
            max_time_ms=_MAX_TIME_MS,
        )
        if token_match:
            return token_match

    return coll.find_one(
        {"isDeleted": {"$ne": True}, "name": {"$regex": re.escape(ref), "$options": "i"}},
        _GIG_FIELDS,
        max_time_ms=_MAX_TIME_MS,
    )


def _get_enrolled_gig_ids(user_id: str) -> set[str]:
    """Gig _ids the user has any non-deleted membership row for."""
    user_id = (user_id or "").strip()
    if not user_id:
        return set()
    id_values = _id_values(user_id)
    member_filter = {
        "$or": [
            {"member": {"$in": id_values}},
            {"user_id": {"$in": id_values}},
            {"worker_id": {"$in": id_values}},
        ],
        "isDeleted": {"$ne": True},
    }
    cursor = _conn._db()[_COLL_MEMBERS].find(
        member_filter, {"crwd_id": 1}, max_time_ms=_MAX_TIME_MS
    )
    enrolled: set[str] = set()
    for row in cursor:
        crwd_id = row.get("crwd_id")
        if crwd_id is not None:
            enrolled.add(str(crwd_id))
    return enrolled


def _spots_full_gig_oids() -> list:
    """ObjectIds of open gigs with no remaining spots (matches CRWD Explore).

    A gig is full when ``number_of_people > 0`` and the count of non-deleted
    ``added_crwd_members`` rows with ``isAccepted: true`` and
    ``status != "Inactive"`` is >= capacity. Pending / waitlisted rows
    (``isAccepted: false``) and Inactive memberships do not consume spots.
    Missing or zero ``number_of_people`` means uncapped — never returned here.
    """
    pipeline: List[Dict[str, Any]] = [
        {
            "$match": {
                **_open_gig_filter(),
                # Keep rows that declare a capacity; coerce later so string "40" works.
                "number_of_people": {"$exists": True, "$nin": [None, 0, "0", ""]},
            },
        },
        {
            "$lookup": {
                "from": _COLL_MEMBERS,
                "let": {"gig_id": "$_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$crwd_id", "$$gig_id"]},
                            "isAccepted": True,
                            "isDeleted": {"$ne": True},
                            # Inactive accepted rows no longer occupy a joinable slot.
                            "status": {"$ne": "Inactive"},
                        },
                    },
                    {"$count": "n"},
                ],
                "as": "_accepted",
            },
        },
        {
            "$addFields": {
                "_accepted_count": {
                    "$ifNull": [{"$arrayElemAt": ["$_accepted.n", 0]}, 0],
                },
                # Coerce string capacities (e.g. "40") so $gte compares numbers.
                "_capacity": {
                    "$convert": {
                        "input": "$number_of_people",
                        "to": "int",
                        "onError": 0,
                        "onNull": 0,
                    },
                },
            },
        },
        {
            "$match": {
                "$expr": {
                    "$and": [
                        {"$gt": ["$_capacity", 0]},
                        {"$gte": ["$_accepted_count", "$_capacity"]},
                    ],
                },
            },
        },
        {"$project": {"_id": 1}},
    ]
    return [
        doc["_id"]
        for doc in _conn._db()[_COLL_CRWDS].aggregate(pipeline, maxTimeMS=_MAX_TIME_MS)
        if doc.get("_id") is not None
    ]


def _list_active_gigs(limit: int = 5, user_id: str = "", offset: int = 0) -> str:
    row_limit = max(1, min(int(limit or 5), _HARD_LIMIT))
    row_offset = max(0, int(offset or 0))
    query: Dict[str, Any] = dict(_open_gig_filter())
    user_id = (user_id or "").strip()
    excluded_count = 0
    excluded_oids: list = list(_spots_full_gig_oids())
    if user_id:
        enrolled_ids = _get_enrolled_gig_ids(user_id)
        excluded_count = len(enrolled_ids)
        enrolled_oids = [oid for gid in enrolled_ids if (oid := _oid(gid)) is not None]
        excluded_oids.extend(enrolled_oids)
    if excluded_oids:
        # Dedupe while preserving ObjectId identity for $nin.
        seen: set[str] = set()
        unique_oids = []
        for oid in excluded_oids:
            key = str(oid)
            if key not in seen:
                seen.add(key)
                unique_oids.append(oid)
        query["_id"] = {"$nin": unique_oids}
    coll = _conn._db()[_COLL_CRWDS]
    total = coll.count_documents(query, maxTimeMS=_MAX_TIME_MS)
    cursor = (
        coll.find(query, _GIG_FIELDS, max_time_ms=_MAX_TIME_MS)
        .sort("end_date", 1)
        .skip(row_offset)
        .limit(row_limit)
    )
    items = [_slim_gig(g) for g in cursor]
    next_offset = row_offset + len(items)
    has_more = next_offset < total
    payload: Dict[str, Any] = {
        "_type": "gig_list",
        "items": items,
        "error": None,
        "offset": row_offset,
        "limit": row_limit,
        "total": total,
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
    }
    if user_id:
        payload["excluded_enrolled_count"] = excluded_count
    return json.dumps(payload, ensure_ascii=False)


def _normalize(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    kept = [w for w in words if w not in _NOISE_WORDS]
    return " ".join(kept or words)


def _score(query_norm: str, name: str, description: str = "") -> float:
    """Fuzzy score in [0, 1] of query against a gig name (+ description)."""
    if not query_norm:
        return 0.0
    name_norm = _normalize(name)
    ratio = difflib.SequenceMatcher(None, query_norm, name_norm).ratio()
    substring = 1.0 if name_norm and query_norm in name_norm else 0.0
    score = 0.6 * ratio + 0.4 * substring
    if description:
        desc_norm = _normalize(description)
        if desc_norm and query_norm in desc_norm:
            score = max(score, 0.5)
    return round(min(score, 1.0), 4)


def _get_gig_details(query: str, top_n: int = 3, full: bool = False) -> str:
    query = (query or "").strip()
    top_n = max(1, min(int(top_n or 3), _GIG_TOPN_CAP))
    if not query:
        return tool_error("query is required for get_gig_details")

    if full or top_n == 1:
        doc = _find_gig_by_ref(query)
        if doc:
            item = _full_gig(doc)
            item["score"] = 1.0
            return json.dumps(
                {"_type": "gig_match_candidates", "query": query, "items": [item], "full": True},
                ensure_ascii=False,
            )
        if full:
            return json.dumps(
                {
                    "_type": "gig_match_candidates",
                    "query": query,
                    "items": [],
                    "error": f"Gig not found: {query}",
                },
                ensure_ascii=False,
            )

    # Exact _id short-circuit.
    oid = _oid(query)
    if oid is not None:
        gig = _conn._db()[_COLL_CRWDS].find_one({"_id": oid}, _GIG_FIELDS, max_time_ms=_MAX_TIME_MS)
        if gig:
            item = _full_gig(gig) if top_n == 1 else _slim_gig(gig)
            item["score"] = 1.0
            return json.dumps(
                {"_type": "gig_match_candidates", "query": query, "items": [item]},
                ensure_ascii=False,
            )

    query_norm = _normalize(query)
    cursor = _conn._db()[_COLL_CRWDS].find(
        _open_gig_filter(),
        {"name": 1, "description": 1, "status": 1, "end_date": 1},
        max_time_ms=_MAX_TIME_MS,
    )
    scored = []
    for gig in cursor:
        s = _score(query_norm, gig.get("name", ""), gig.get("description", ""))
        if s >= _MATCH_FLOOR:
            scored.append((s, gig))
    scored.sort(key=lambda t: t[0], reverse=True)

    items = []
    for s, gig in scored[:top_n]:
        if top_n == 1 and s >= 0.9:
            items.append({**_full_gig(gig), "score": s})
        else:
            items.append(attach_gig_url({
                "score": s,
                "_id": str(gig.get("_id")),
                "name": gig.get("name"),
                "status": gig.get("status"),
                "end_date": _serialize_doc(gig.get("end_date")),
            }, inline_name=True))
    return json.dumps(
        {"_type": "gig_match_candidates", "query": query, "items": items},
        ensure_ascii=False,
    )
