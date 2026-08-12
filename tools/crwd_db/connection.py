"""Process-scoped MongoDB connection for CRWD tools.

One lazy ``MongoClient`` per process (pymongo owns the pool). Shared by all
action modules and Chatwoot enrichment — never open/close per request.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import threading
from typing import Any, Callable, TypeVar

from tools.lazy_deps import FeatureUnavailable, ensure

logger = logging.getLogger(__name__)

T = TypeVar("T")

# --- Constants ---

DB_DEFAULT = "crwd_staging"
COLL_CRWDS = "crwds"
COLL_USERS = "users"
COLL_MEMBERS = "added_crwd_members"
COLL_PURCHASES = "user_product_purchases"
COLL_RECEIPTS = "receipt_upload_history"
COLL_NOTIFS = "notifications"
COLL_GIG_STORE_ORDERS = "gig_store_orders"
COLL_GIG_PRODUCT_REVIEWS = "gig_product_reviews"
COLL_ORDER_RECEIPT_REVIEWS = "order_receipt_reviews"
COLL_GIG_PARTICIPATIONS = "gig_participations"
# Agent-owned. The only collection this package ever writes to.
COLL_PROOFS = "proof_submissions"
OBJECT_ID_IN_TEXT_RE = re.compile(r"\b[0-9a-fA-F]{24}\b")
# custom_query is find/count only, so listing a collection here grants read access
# and nothing more. Writes never consult this set.
ALLOWED_COLLECTIONS = {
    COLL_CRWDS, COLL_USERS, COLL_MEMBERS,
    COLL_PURCHASES, COLL_RECEIPTS, COLL_NOTIFS,
    COLL_PROOFS,
}
HARD_LIMIT = 20
MAX_TIME_MS = 5000
GIG_TOPN_CAP = 10
MATCH_FLOOR = 0.3

OBJECTID_RE = re.compile(r"^[a-fA-F0-9]{24}$")

# Fields that must never be returned from ``users``, regardless of projection.
USER_SECRET_RE = re.compile(r"password|token|otp|secret", re.IGNORECASE)

# Explicit projections -- never return whole documents.
USER_FIELDS = {
    "full_name": 1, "first_name": 1, "last_name": 1, "email": 1, "phone": 1,
    "bio": 1, "status": 1, "city": 1, "state": 1, "country": 1,
    "postal_code": 1, "dob": 1, "gender": 1,
    "isBlocked": 1, "isDeleted": 1,
}
GIG_FIELDS = {
    "name": 1, "description": 1, "gig_type": 1, "payout": 1, "price": 1,
    "gig_stores": 1, "start_date": 1, "end_date": 1, "type_of_work_proof": 1,
    "status": 1, "address": 1, "city": 1, "state": 1, "postal_code": 1,
    "image": 1, "isDeleted": 1, "isArchived": 1,
}
MEMBER_FIELDS = {
    "member": 1, "user_id": 1, "worker_id": 1, "crwd_id": 1, "status": 1,
    "isAccepted": 1, "isApproved": 1, "isCompleted": 1, "hasPaid": 1,
    "isDeleted": 1, "createdAt": 1, "updatedAt": 1,
}
# What product a member is approved to buy for a gig (name + buy link).
PURCHASE_FIELDS = {
    "product_name": 1, "product_url": 1, "store_name": 1, "crwd_id": 1,
    "crwd_name": 1, "gig_type": 1, "source": 1, "purchasedAt": 1, "createdAt": 1,
}
# Receipt/proof validation status (current pipeline). Omits the S3 key.
RECEIPT_FIELDS = {
    "status": 1, "fail_reason": 1, "receipt_type": 1, "order_number": 1,
    "campaign_id": 1, "extracted_data": 1, "fraud_band_after": 1,
    "created_at": 1, "updated_at": 1,
}
# Account notifications. Never project the device/chat token fields.
NOTIF_FIELDS = {
    "title": 1, "description": 1, "notificationType": 1, "isSeen": 1,
    "date": 1, "status": 1, "createdAt": 1,
}

_uri_bridge_warned = False


def _lazy_singleton(factory: Callable[[], T]) -> Callable[[], T]:
    """Thread-safe lazy singleton (local copy — avoid tools→plugins coupling)."""
    lock = threading.Lock()
    box: list = []

    @functools.wraps(factory)
    def accessor() -> T:
        if box:
            return box[0]
        with lock:
            if box:
                return box[0]
            instance = factory()
            box.append(instance)
            return instance

    def reset() -> None:
        with lock:
            box.clear()

    accessor.reset = reset  # type: ignore[attr-defined]
    return accessor


def _bridge_legacy_mongo_uri() -> None:
    """Copy deprecated MONGODB_URI into CRWD_MONGO_URI when the latter is unset."""
    global _uri_bridge_warned
    if os.getenv("CRWD_MONGO_URI"):
        return
    legacy = (os.getenv("MONGODB_URI") or "").strip()
    if not legacy:
        return
    os.environ["CRWD_MONGO_URI"] = legacy
    if not _uri_bridge_warned:
        logger.warning(
            "MONGODB_URI is deprecated for CRWD access; set CRWD_MONGO_URI instead"
        )
        _uri_bridge_warned = True


def _resolve_mongo_uri() -> str:
    _bridge_legacy_mongo_uri()
    return (os.getenv("CRWD_MONGO_URI") or "").strip()


def _resolve_db_name() -> str:
    env_name = (os.getenv("CRWD_MONGO_DB") or "").strip()
    if env_name:
        return env_name
    try:
        from hermes_cli.config import cfg_get, load_config

        cfg = load_config()
        db_name = str(cfg_get(cfg, "mongodb", "default_database", default="") or "").strip()
        if db_name:
            return db_name
    except Exception:
        pass
    return DB_DEFAULT


def check_crwd_db_requirements() -> bool:
    """Tool is only available when CRWD_MONGO_URI (or legacy MONGODB_URI) is set."""
    return bool(_resolve_mongo_uri())


def _build_client():
    try:
        ensure("tool.mongodb", prompt=False)
    except FeatureUnavailable as exc:
        raise RuntimeError(str(exc)) from exc
    from pymongo import MongoClient

    uri = _resolve_mongo_uri()
    if not uri:
        raise RuntimeError("CRWD_MONGO_URI is not set")
    return MongoClient(uri, serverSelectionTimeoutMS=5000)


_get_client = _lazy_singleton(_build_client)


def reset_client() -> None:
    """Drop the cached MongoClient (tests / teardown)."""
    _get_client.reset()  # type: ignore[attr-defined]


def _db():
    return _get_client()[_resolve_db_name()]


def _oid(value: Any):
    """Return an ObjectId for a 24-hex string, else None."""
    from bson import ObjectId

    if isinstance(value, str) and OBJECTID_RE.match(value):
        return ObjectId(value)
    return None


def _now():
    import datetime

    return datetime.datetime.now()


def _id_values(user_id: str) -> list:
    """Match values for a user id stored as either ObjectId or string."""
    oid = _oid(user_id)
    return [oid, user_id] if oid is not None else [user_id]


# Back-compat aliases used inside the package (underscore prefix kept for callers).
_DB_DEFAULT = DB_DEFAULT
_COLL_CRWDS = COLL_CRWDS
_COLL_USERS = COLL_USERS
_COLL_MEMBERS = COLL_MEMBERS
_COLL_PURCHASES = COLL_PURCHASES
_COLL_RECEIPTS = COLL_RECEIPTS
_COLL_NOTIFS = COLL_NOTIFS
_COLL_GIG_STORE_ORDERS = COLL_GIG_STORE_ORDERS
_COLL_GIG_PRODUCT_REVIEWS = COLL_GIG_PRODUCT_REVIEWS
_COLL_ORDER_RECEIPT_REVIEWS = COLL_ORDER_RECEIPT_REVIEWS
_COLL_GIG_PARTICIPATIONS = COLL_GIG_PARTICIPATIONS
_COLL_PROOFS = COLL_PROOFS
_OBJECT_ID_IN_TEXT_RE = OBJECT_ID_IN_TEXT_RE
_ALLOWED_COLLECTIONS = ALLOWED_COLLECTIONS
_HARD_LIMIT = HARD_LIMIT
_MAX_TIME_MS = MAX_TIME_MS
_GIG_TOPN_CAP = GIG_TOPN_CAP
_MATCH_FLOOR = MATCH_FLOOR
_OBJECTID_RE = OBJECTID_RE
_USER_SECRET_RE = USER_SECRET_RE
_USER_FIELDS = USER_FIELDS
_GIG_FIELDS = GIG_FIELDS
_MEMBER_FIELDS = MEMBER_FIELDS
_PURCHASE_FIELDS = PURCHASE_FIELDS
_RECEIPT_FIELDS = RECEIPT_FIELDS
_NOTIF_FIELDS = NOTIF_FIELDS
