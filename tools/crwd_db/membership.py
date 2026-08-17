"""Membership / enrollment actions and filters."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from tools.registry import tool_error

from tools.crwd_db import connection as _conn
from tools.crwd_db.connection import (
    _COLL_CRWDS,
    _COLL_GIG_PARTICIPATIONS,
    _COLL_MEMBERS,
    _GIG_FIELDS,
    _HARD_LIMIT,
    _MAX_TIME_MS,
    _MEMBER_FIELDS,
    _id_values,
    _oid,
)

from tools.crwd_db.gigs import _slim_gig
from tools.crwd_db.serialize import _serialize_doc, _serialize_docs

logger = logging.getLogger(__name__)

def _get_waitlisted_gigs(user_id: str, limit: int = 10) -> str:
    """Gigs the member applied for but has not been accepted into yet."""
    user_id = (user_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for get_waitlisted_gigs")
    row_limit = max(1, min(int(limit or 10), _HARD_LIMIT))

    oid = _oid(user_id)
    id_values = [oid, user_id] if oid is not None else [user_id]
    member_filter = {
        "$or": [
            {"member": {"$in": id_values}},
            {"user_id": {"$in": id_values}},
            {"worker_id": {"$in": id_values}},
        ],
        "isDeleted": {"$ne": True},
        "isAccepted": False,
    }
    members = list(
        _conn._db()[_COLL_MEMBERS]
        .find(member_filter, _MEMBER_FIELDS, max_time_ms=_MAX_TIME_MS)
    )
    crwd_ids = [m["crwd_id"] for m in members if m.get("crwd_id") is not None]
    gigs_by_id = {}
    if crwd_ids:
        for gig in _conn._db()[_COLL_CRWDS].find(
            # Archived gigs are invisible in the app; a membership pointing at one
            # is not an active gig and must not be presented as one.
            {"_id": {"$in": crwd_ids}, "isArchived": {"$ne": True}},
            _GIG_FIELDS, max_time_ms=_MAX_TIME_MS,
        ):
            gigs_by_id[str(gig["_id"])] = _slim_gig(gig)

    members = _sort_members_by_gig_end_date(members, gigs_by_id)[:row_limit]
    items = []
    for m in members:
        items.append({
            "membership": _serialize_doc(m),
            "gig": gigs_by_id.get(str(m.get("crwd_id"))),
        })
    return json.dumps(
        {"_type": "waitlisted_gigs", "items": items, "error": None}, ensure_ascii=False
    )


def _get_user_gigs(user_id: str, limit: int = 10) -> str:
    user_id = (user_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for get_user_gigs")
    row_limit = max(1, min(int(limit or 10), _HARD_LIMIT))

    members = list(
        _conn._db()[_COLL_MEMBERS]
        .find(_joined_member_filter(user_id), _MEMBER_FIELDS, max_time_ms=_MAX_TIME_MS)
    )
    crwd_ids = [m["crwd_id"] for m in members if m.get("crwd_id") is not None]
    gigs_by_id = {}
    if crwd_ids:
        for gig in _conn._db()[_COLL_CRWDS].find(
            # Archived gigs are invisible in the app; a membership pointing at one
            # is not an active gig and must not be presented as one.
            {"_id": {"$in": crwd_ids}, "isArchived": {"$ne": True}},
            _GIG_FIELDS, max_time_ms=_MAX_TIME_MS,
        ):
            gigs_by_id[str(gig["_id"])] = _slim_gig(gig)

    members = _sort_members_by_gig_end_date(members, gigs_by_id)[:row_limit]
    items = []
    for m in members:
        gig = gigs_by_id.get(str(m.get("crwd_id")))
        if not gig:
            # Gig archived (excluded from the join above) or gone -- the app's
            # Active tab doesn't show it, so neither do we.
            continue
        items.append({
            "membership": _serialize_doc(m),
            "gig": gig,
        })
    return json.dumps(
        {"_type": "user_gigs", "items": items, "error": None}, ensure_ascii=False
    )


def _get_user_gig_history(user_id: str, limit: int = 50) -> str:
    """Past membership rows for a member (includes deleted/rejected rows)."""
    user_id = (user_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for get_user_gig_history")
    row_limit = max(1, min(int(limit or 50), _HARD_LIMIT))

    db = _conn._db()
    rows = list(
        db[_COLL_MEMBERS]
        .find(_member_or_filter(user_id), _MEMBER_FIELDS, max_time_ms=_MAX_TIME_MS)
        .sort("createdAt", -1)
        .limit(row_limit)
    )
    items = []
    for row in rows:
        serialized = _serialize_doc(row)
        items.append({
            "_id": serialized.get("_id"),
            "crwd_id": serialized.get("crwd_id"),
            "status": serialized.get("status"),
            "isApproved": serialized.get("isApproved"),
            "isAccepted": serialized.get("isAccepted"),
            "isDeleted": serialized.get("isDeleted"),
            "hasPaid": serialized.get("hasPaid"),
            "rejectionReason": serialized.get("rejectionReason"),
            "rejectionNotes": serialized.get("rejectionNotes"),
            "date": serialized.get("date"),
            "time": serialized.get("time"),
            "createdAt": serialized.get("createdAt"),
            "updatedAt": serialized.get("updatedAt"),
        })

    if not items:
        try:
            if _COLL_GIG_PARTICIPATIONS in db.list_collection_names():
                fallback = list(
                    db[_COLL_GIG_PARTICIPATIONS]
                    .find(
                        {"user_id": {"$in": _id_values(user_id)}},
                        max_time_ms=_MAX_TIME_MS,
                    )
                    .sort("createdAt", -1)
                    .limit(row_limit)
                )
                if fallback:
                    items = _serialize_docs(fallback)
        except Exception:
            logger.debug("gig_participations fallback unavailable", exc_info=True)

    return json.dumps(
        {"_type": "user_gig_history", "items": items, "count": len(items), "error": None},
        ensure_ascii=False,
    )

def _member_or_filter(user_id: str) -> Dict[str, Any]:
    """Filter fragment matching a user id on member/user_id/worker_id fields."""
    id_values = _id_values(user_id)
    return {
        "$or": [
            {"member": {"$in": id_values}},
            {"user_id": {"$in": id_values}},
            {"worker_id": {"$in": id_values}},
        ],
    }


def _joined_member_filter(user_id: str) -> Dict[str, Any]:
    """In-progress memberships — admin accepted the member into the gig (``isAccepted``)."""
    return {
        "$and": [
            _member_or_filter(user_id),
            {"isDeleted": {"$ne": True}},
            {
                "$or": [
                    {"isAccepted": True},
                    # Legacy status synonyms for enrolled/joined membership. CRWD's own
                    # backend has historically written "Approved" here for enrollment
                    # (not the payout-approval concept) -- isAccepted, OR'd in above,
                    # remains the authoritative enrollment signal regardless of status.
                    {"status": {"$in": ["Active", "Accepted", "Approved", "Joined"]}},
                ],
            },
        ],
    }


def _waitlisted_member_filter(user_id: str) -> Dict[str, Any]:
    return {
        **_member_or_filter(user_id),
        "isDeleted": {"$ne": True},
        "isAccepted": False,
    }


def _gig_type_key(gig: Dict[str, Any]) -> str:
    gt = str(gig.get("gig_type") or "").strip().lower()
    if gt in ("irl", "in_store", "live"):
        return "irl"
    if gt in ("web_based", "web", "online", "amazon"):
        return "web"
    return gt or "unknown"


def _end_date_sort_key(gig: Optional[Dict[str, Any]]) -> tuple[int, float]:
    """Ascending sort key for gig end_date; gigs without a date sort last."""
    missing = (1, float("inf"))
    if not gig:
        return missing
    end = gig.get("end_date")
    if end is None:
        return missing
    if hasattr(end, "timestamp"):
        return (0, end.timestamp())
    if isinstance(end, dict):
        raw = end.get("$date")
        if isinstance(raw, (int, float)):
            ts = raw / 1000.0 if raw > 1e12 else raw
            return (0, ts)
        if isinstance(raw, str):
            try:
                import datetime as dt

                parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return (0, parsed.timestamp())
            except ValueError:
                return missing
    return missing


def _sort_members_by_gig_end_date(
    members: List[Dict[str, Any]],
    gigs_by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return sorted(
        members,
        key=lambda m: _end_date_sort_key(gigs_by_id.get(str(m.get("crwd_id")))),
    )
