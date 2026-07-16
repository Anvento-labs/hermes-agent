"""CRWD database tool -- lookups plus proof-submission storage for the CRWD Coach agent.

Registers a single LLM-callable tool ``crwd_db`` (gated on ``CRWD_MONGO_URI``)
that reads CRWD's MongoDB data through a handful of purpose-built actions plus
one guarded custom-query escape hatch:

- ``list_active_gigs`` -- open gigs sorted by soonest end_date; pass ``user_id`` to
  exclude gigs the member already has a membership for
- ``get_gig_details``  -- fuzzy-match gigs by name / free text, ranked candidates
- ``get_user``         -- look up one user by email, phone, or _id
- ``get_user_gigs``    -- campaigns a user is an active member of
- ``get_user_gig_history`` -- past membership rows for a member
- ``get_user_gig_status`` -- per-gig stage + personalized next_step from progress data
- ``custom_query``     -- guarded find/count on the known collections

Plus the proof-submission actions used by the ``crwd-proof-validator`` and
``crwd-risk-analyser`` skills:

- ``store_proof``               -- record one validated proof submission
- ``check_duplicate_proof``     -- is this proof id already claimed?
- ``find_proof``                -- full submission history for a proof id
- ``check_gig_proof_completion``-- which required artifacts are still outstanding?
- ``mark_proof_risk_scored``    -- flag a proof so risk never scores it twice

Connection string comes from ``CRWD_MONGO_URI`` (in ``~/.hermes/.env``); the
database name from ``CRWD_MONGO_DB`` (default ``crwd_staging``).

Write scope is deliberately narrow. Exactly two code paths write, both only to
``proof_submissions`` -- a collection owned by this agent: ``store_proof``
inserts, and ``mark_proof_risk_scored`` sets a single boolean on one record.
Every pre-existing CRWD collection remains read-only, and ``custom_query`` stays
find/count. The collection name is hardcoded at both write sites and never taken
from tool arguments.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from tools.crwd_urls import attach_gig_url
from tools.lazy_deps import FeatureUnavailable, ensure
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

# --- Constants ---

_DB_DEFAULT = "crwd_staging"
_COLL_CRWDS = "crwds"
_COLL_USERS = "users"
_COLL_MEMBERS = "added_crwd_members"
_COLL_PURCHASES = "user_product_purchases"
_COLL_RECEIPTS = "receipt_upload_history"
_COLL_NOTIFS = "notifications"
_COLL_GIG_STORE_ORDERS = "gig_store_orders"
_COLL_GIG_PRODUCT_REVIEWS = "gig_product_reviews"
_COLL_ORDER_RECEIPT_REVIEWS = "order_receipt_reviews"
_COLL_GIG_PARTICIPATIONS = "gig_participations"
# Agent-owned. The only collection this module ever writes to.
_COLL_PROOFS = "proof_submissions"
_OBJECT_ID_IN_TEXT_RE = re.compile(r"\b[0-9a-fA-F]{24}\b")
# custom_query is find/count only, so listing a collection here grants read access
# and nothing more. Writes never consult this set.
_ALLOWED_COLLECTIONS = {
    _COLL_CRWDS, _COLL_USERS, _COLL_MEMBERS,
    _COLL_PURCHASES, _COLL_RECEIPTS, _COLL_NOTIFS,
    _COLL_PROOFS,
}
_HARD_LIMIT = 20
_MAX_TIME_MS = 5000
_GIG_TOPN_CAP = 10
_MATCH_FLOOR = 0.3

_OBJECTID_RE = re.compile(r"^[a-fA-F0-9]{24}$")

# --- Proof submissions ---

_PROOF_TYPES = {
    "receipt_target", "receipt_amazon", "receipt_other",
    "order_screenshot", "review_screenshot", "amazon_review_link", "ugc_link",
}
_PROOF_STATUSES = {"accepted", "rejected", "needs_human"}
_PROOF_CONFIDENCE = {"low", "medium", "high"}
# Closed on purpose: a risk assessment counts these, and an open field would let
# "wrong_item" drift in beside "wrong_product" and silently undercount.
_PROOF_REASON_CODES = {
    "clean_match", "duplicate_proof", "gig_not_active_for_user", "wrong_proof_type",
    "incomplete_submission", "date_outside_gig_window", "no_identifier",
    "invalid_order_number", "wrong_product", "wrong_quantity", "unreadable",
    "suspected_edited", "link_unreachable", "link_not_owned", "content_mismatch",
}
_RECEIPT_TYPES = {
    "receipt_target", "receipt_amazon", "receipt_other", "order_screenshot",
}

# Which requirement flags demand a proof artifact of their own, and what satisfies
# each. Derived from the data, not assumed.
_REQUIREMENT_ARTIFACTS = {
    "requires_receipt": {
        "receipt_target", "receipt_amazon", "receipt_other", "order_screenshot",
    },
    "requires_review_receipt": {"review_screenshot"},
    "requires_review_link": {"amazon_review_link"},
    "requires_ugc_post": {"ugc_link"},
}
# Stores KNOWN to give each review its own permalink. Only for these is a review
# link genuinely obtainable, so only here may a screenshot fail to satisfy
# requires_review_link -- accepting one would quietly drop a deliverable the gig
# asked for.
_STORES_WITH_REVIEW_URLS = {"amazon"}
# Stores KNOWN to have no per-review URL: their "review link" is a product page,
# identical for every reviewer (e.g. target.com/p/hj/-/A-95279869).
_STORES_WITHOUT_REVIEW_URLS = {"target"}
# Everything else is UNKNOWN, and unknown resolves in the member's favour: a
# screenshot satisfies requires_review_link. Demanding a permalink from a store
# that may not issue one would strand an honest member on a proof they cannot
# produce -- the same failure as the Target product-page trap. The skill is told to
# check the web when it needs to know for sure.


def _norm_store(name: str) -> str:
    """Trim + case-fold: the data holds both 'Target' and 'Target ' (trailing space)."""
    return (name or "").strip().lower()


# Order-number shapes, with strictness matched to how much evidence we have.
#
# Without any check, _normalize_proof_id turns a typed "12345" into a valid proof
# id -- staging holds Amazon rows with order_ids of exactly "12345", "2234" and
# "45435", which is the manual-entry abuse itself.
#
# EXACT lengths, only where the evidence is strong. Amazon is 3-7-7 = 17 digits:
# 166 of ~170 real order_ids in gig_store_orders match, and the handful that don't
# are typos or two numbers pasted into one field.
_ORDER_NUMBER_DIGITS = {
    "receipt_amazon": {17},
    "order_screenshot": {17},
}
# FLOORS, where the evidence is thin. Target's REC# reads as 18 digits across the
# only four real samples we have (and gig_store_orders holds no Target rows at
# all), so a floor rather than an exact length: enough to refuse typed junk,
# loose enough that an 18-digit assumption drawn from four receipts cannot reject
# a real one.
_MIN_ORDER_DIGITS_BY_TYPE = {"receipt_target": 12}
# Unknown merchants. Guard only against the absurd -- a real Sprouts receipt's
# order number is 6 digits ("315261"), so the floor has to stay low. An
# unfamiliar format must never be called fraud just because we don't know it.
_MIN_ORDER_DIGITS = 5


def _order_number_plausible(digits: str, proof_type: str) -> bool:
    """Could this digit string be a real order number for this merchant?

    Deliberately lenient: a wrong answer here blocks an honest member. False only
    means "do not key on this" -- the caller turns that into needs_human, never an
    auto-reject.
    """
    if not digits:
        return False
    exact = _ORDER_NUMBER_DIGITS.get(proof_type)
    if exact:
        return len(digits) in exact
    floor = _MIN_ORDER_DIGITS_BY_TYPE.get(proof_type, _MIN_ORDER_DIGITS)
    return len(digits) >= floor


def _artifacts_for(flag: str, store_name: str = "") -> set:
    """What can satisfy this requirement flag at this store."""
    types = set(_REQUIREMENT_ARTIFACTS.get(flag) or set())
    if flag == "requires_review_link":
        if _norm_store(store_name) not in _STORES_WITH_REVIEW_URLS:
            # Known-no-URL (Target) or unknown -- a screenshot stands in.
            types.add("review_screenshot")
    return types
# Verified *inside* another artifact, never submitted on their own. The data is
# unambiguous: requires_order_id never appears without requires_receipt (41 gigs
# vs 0), and the app stores order_id and receipt_file on the same row;
# requires_review_rating never appears without requires_review_receipt (40 vs 0).
# Treating these as separate artifacts would leave a gig permanently incomplete.
_FIELD_LEVEL_REQUIREMENTS = {
    "requires_order_id", "requires_review_rating", "requires_store_address",
    "requires_tracking_id",
}

# Order/transaction number prefixes to strip before digit-normalizing a receipt id.
_ORDER_PREFIX_RE = re.compile(
    r"^\s*(rec\s*#?|order\s*#?|trans(action)?\s*#?|#)\s*", re.IGNORECASE
)
# platform -> ordered path patterns yielding the post id.
#
# Matched case-insensitively against the *raw* url so the captured id keeps its
# original case. YouTube ids and Instagram shortcodes are case-sensitive --
# dQw4w9WgXcQ and dQw4w9WgXcq are different videos. Folding case here would key
# them the same and reject an innocent member for "duplicating" a stranger's post.
_UGC_POST_PATTERNS = (
    ("tiktok", (
        re.compile(r"/video/(\d+)", re.IGNORECASE),
        re.compile(r"/photo/(\d+)", re.IGNORECASE),
    )),
    ("instagram", (
        re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE),
    )),
    ("youtube", (
        re.compile(r"/shorts/([A-Za-z0-9_-]+)", re.IGNORECASE),
        re.compile(r"/embed/([A-Za-z0-9_-]+)", re.IGNORECASE),
        re.compile(r"[?&]v=([A-Za-z0-9_-]+)", re.IGNORECASE),
        re.compile(r"youtu\.be/([A-Za-z0-9_-]+)", re.IGNORECASE),
    )),
)
_AMAZON_REVIEW_RE = re.compile(
    r"/(?:gp/customer-reviews|review)/([A-Z0-9]+)", re.IGNORECASE
)


def _ugc_platform(url: str) -> str:
    """Platform slug for a UGC url, or "" when it is not one we recognize."""
    host = url.lower()
    if "tiktok." in host:
        return "tiktok"
    if "instagram." in host:
        return "instagram"
    if "youtube." in host or "youtu.be" in host:
        return "youtube"
    return ""


def _normalize_proof_id(raw: str, proof_type: str = "") -> str:
    """Canonical dedup key for a proof identifier.

    Receipts/orders collapse to digits only, so ``REC# 2-6177-0190`` and
    ``26177-0190`` are one key. UGC links collapse to ``platform:post_id``,
    which survives tracking params, ``www.``, a missing ``@handle`` segment and
    short-link forms -- all of which point at the same post. The ``platform:``
    prefix keeps a YouTube id from colliding with an Instagram shortcode, which
    would otherwise reject a member for "duplicating" an unrelated stranger's post.

    Returns "" when nothing defensible can be extracted; callers must treat that
    as *not extractable* rather than as a key.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    proof_type = (proof_type or "").strip().lower()
    lowered = raw.lower()
    is_url = lowered.startswith(("http://", "https://")) or "://" in lowered

    if proof_type == "ugc_link" or (not proof_type and is_url and _ugc_platform(lowered)):
        platform = _ugc_platform(lowered)
        if platform:
            for name, patterns in _UGC_POST_PATTERNS:
                if name != platform:
                    continue
                for pattern in patterns:
                    # Search the raw url: the captured id must keep its case.
                    match = pattern.search(raw)
                    if match:
                        return f"{platform}:{match.group(1)}"
        # A recognized platform whose post id we could not read (e.g. an
        # unresolved vm.tiktok.com short link) is not a key -- say so.
        return ""

    if proof_type == "amazon_review_link" or (not proof_type and is_url and "amazon." in lowered):
        match = _AMAZON_REVIEW_RE.search(raw)
        if match:
            return match.group(1).upper()
        return ""

    if proof_type == "review_screenshot":
        # A screenshot rarely carries a per-review id, so the caller usually builds
        # a composite: "platform:product:handle". Slugify it whole -- digit-only
        # normalization would reduce target:A-95279869:sarah and
        # target:A-95279869:mike to the same key and reject the second reviewer.
        if is_url:
            # A product-page url (target.com/p/hj/-/A-95279869) is identical for
            # every member who reviews that product -- it identifies the product,
            # not whose review it is. Never key on it.
            return ""
        slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
        return slug

    if proof_type in _RECEIPT_TYPES or not is_url:
        digits = re.sub(r"\D", "", _ORDER_PREFIX_RE.sub("", raw))
        if not _order_number_plausible(digits, proof_type):
            return ""
        return digits

    # An unrecognized url is not a defensible key: it may identify a product or a
    # share target rather than this member's submission. Say so instead of
    # inventing a host+path key.
    return ""

# Fields that must never be returned from ``users``, regardless of projection.
_USER_SECRET_RE = re.compile(r"password|token|otp|secret", re.IGNORECASE)

# Explicit projections -- never return whole documents.
_USER_FIELDS = {
    "full_name": 1, "first_name": 1, "last_name": 1, "email": 1, "phone": 1,
    "bio": 1, "status": 1, "city": 1, "state": 1, "country": 1,
    "isBlocked": 1, "isDeleted": 1,
}
_GIG_FIELDS = {
    "name": 1, "description": 1, "gig_type": 1, "payout": 1, "price": 1,
    "gig_stores": 1, "start_date": 1, "end_date": 1, "type_of_work_proof": 1,
    "status": 1, "address": 1, "city": 1, "state": 1, "postal_code": 1,
    "image": 1, "isDeleted": 1,
}
_MEMBER_FIELDS = {
    "member": 1, "user_id": 1, "worker_id": 1, "crwd_id": 1, "status": 1,
    "isAccepted": 1, "isApproved": 1, "isCompleted": 1, "hasPaid": 1,
    "isDeleted": 1, "createdAt": 1, "updatedAt": 1,
}
# What product a member is approved to buy for a gig (name + buy link).
_PURCHASE_FIELDS = {
    "product_name": 1, "product_url": 1, "store_name": 1, "crwd_id": 1,
    "crwd_name": 1, "gig_type": 1, "source": 1, "purchasedAt": 1, "createdAt": 1,
}
# Receipt/proof validation status (current pipeline). Omits the S3 key.
_RECEIPT_FIELDS = {
    "status": 1, "fail_reason": 1, "receipt_type": 1, "order_number": 1,
    "campaign_id": 1, "extracted_data": 1, "fraud_band_after": 1,
    "created_at": 1, "updated_at": 1,
}
# Account notifications. Never project the device/chat token fields.
_NOTIF_FIELDS = {
    "title": 1, "description": 1, "notificationType": 1, "isSeen": 1,
    "date": 1, "status": 1, "createdAt": 1,
}

# Noise words stripped before fuzzy scoring gig names.
_NOISE_WORDS = {
    "the", "a", "an", "gig", "campaign", "crwd", "and", "for", "with",
    "supplement", "supplements", "review", "reviews",
}

_client = None
_uri_bridge_warned = False


# --- Availability / connection ---

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
    return _DB_DEFAULT


def check_crwd_db_requirements() -> bool:
    """Tool is only available when CRWD_MONGO_URI (or legacy MONGODB_URI) is set."""
    return bool(_resolve_mongo_uri())


def _get_client():
    global _client
    try:
        ensure("tool.mongodb", prompt=False)
    except FeatureUnavailable as exc:
        raise RuntimeError(str(exc)) from exc
    from pymongo import MongoClient

    uri = _resolve_mongo_uri()
    if not uri:
        raise RuntimeError("CRWD_MONGO_URI is not set")
    if _client is None:
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return _client


def _db():
    return _get_client()[_resolve_db_name()]


def _oid(value: Any):
    """Return an ObjectId for a 24-hex string, else None."""
    from bson import ObjectId

    if isinstance(value, str) and _OBJECTID_RE.match(value):
        return ObjectId(value)
    return None


# --- Serialization ---

def _serialize_doc(doc: Any) -> Any:
    from bson import json_util

    return json.loads(json_util.dumps(doc))


def _serialize_docs(docs: List[Any]) -> List[Any]:
    return [_serialize_doc(doc) for doc in docs]


def _now():
    import datetime

    return datetime.datetime.now()


def _open_gig_filter() -> Dict[str, Any]:
    """Filter for currently-open gigs: not deleted, Active, end_date in future."""
    return {
        "isDeleted": {"$ne": True},
        "status": {"$regex": r"^active$", "$options": "i"},
        "end_date": {"$gte": _now()},
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
        "start_date": gig.get("start_date"),
        "end_date": gig.get("end_date"),
        "effective_payout": _effective_payout(gig),
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

    coll = _db()[_COLL_CRWDS]
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


# --- Actions ---

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
    cursor = _db()[_COLL_MEMBERS].find(
        member_filter, {"crwd_id": 1}, max_time_ms=_MAX_TIME_MS
    )
    enrolled: set[str] = set()
    for row in cursor:
        crwd_id = row.get("crwd_id")
        if crwd_id is not None:
            enrolled.add(str(crwd_id))
    return enrolled


def _list_active_gigs(limit: int = 5, user_id: str = "", offset: int = 0) -> str:
    row_limit = max(1, min(int(limit or 5), _HARD_LIMIT))
    row_offset = max(0, int(offset or 0))
    query: Dict[str, Any] = dict(_open_gig_filter())
    user_id = (user_id or "").strip()
    excluded_count = 0
    if user_id:
        enrolled_ids = _get_enrolled_gig_ids(user_id)
        excluded_count = len(enrolled_ids)
        enrolled_oids = [oid for gid in enrolled_ids if (oid := _oid(gid)) is not None]
        if enrolled_oids:
            query["_id"] = {"$nin": enrolled_oids}
    coll = _db()[_COLL_CRWDS]
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
        gig = _db()[_COLL_CRWDS].find_one({"_id": oid}, _GIG_FIELDS, max_time_ms=_MAX_TIME_MS)
        if gig:
            item = _full_gig(gig) if top_n == 1 else _slim_gig(gig)
            item["score"] = 1.0
            return json.dumps(
                {"_type": "gig_match_candidates", "query": query, "items": [item]},
                ensure_ascii=False,
            )

    query_norm = _normalize(query)
    cursor = _db()[_COLL_CRWDS].find(
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


def _get_user(identifier: str) -> str:
    identifier = (identifier or "").strip()
    if not identifier:
        return tool_error("identifier is required for get_user")

    oid = _oid(identifier)
    if oid is not None:
        query: Dict[str, Any] = {"_id": oid}
    elif "@" in identifier:
        query = {"email": identifier}
    else:
        query = {"phone": identifier}

    user = _db()[_COLL_USERS].find_one(query, _USER_FIELDS, max_time_ms=_MAX_TIME_MS)
    return json.dumps(
        {"_type": "user", "items": [_serialize_doc(user)] if user else [], "error": None},
        ensure_ascii=False,
    )


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
        _db()[_COLL_MEMBERS]
        .find(member_filter, _MEMBER_FIELDS, max_time_ms=_MAX_TIME_MS)
    )
    crwd_ids = [m["crwd_id"] for m in members if m.get("crwd_id") is not None]
    gigs_by_id = {}
    if crwd_ids:
        for gig in _db()[_COLL_CRWDS].find(
            {"_id": {"$in": crwd_ids}}, _GIG_FIELDS, max_time_ms=_MAX_TIME_MS
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
        _db()[_COLL_MEMBERS]
        .find(_joined_member_filter(user_id), _MEMBER_FIELDS, max_time_ms=_MAX_TIME_MS)
    )
    crwd_ids = [m["crwd_id"] for m in members if m.get("crwd_id") is not None]
    gigs_by_id = {}
    if crwd_ids:
        for gig in _db()[_COLL_CRWDS].find(
            {"_id": {"$in": crwd_ids}}, _GIG_FIELDS, max_time_ms=_MAX_TIME_MS
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
        {"_type": "user_gigs", "items": items, "error": None}, ensure_ascii=False
    )


def _get_user_gig_history(user_id: str, limit: int = 50) -> str:
    """Past membership rows for a member (includes deleted/rejected rows)."""
    user_id = (user_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for get_user_gig_history")
    row_limit = max(1, min(int(limit or 50), _HARD_LIMIT))

    db = _db()
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


def _id_values(user_id: str) -> list:
    """Match values for a user id stored as either ObjectId or string."""
    oid = _oid(user_id)
    return [oid, user_id] if oid is not None else [user_id]


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
        "purchase_confirmed": bool(purchases),
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

    progress["purchase_confirmed"] = True

    if gig_type == "irl":
        if not store_orders:
            return {
                "stage": "need_receipt",
                "next_step": (
                    f"For {gig_name}, visit the store, buy the product, then upload "
                    "your receipt in the app."
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
                    f"Receipt approved for {gig_name}! Next: post your review/UGC "
                    "and submit the links in the app."
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
                    f"For {gig_name}, order the product, then upload your order "
                    "receipt screenshot in the app."
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
                    f"Order approved for {gig_name}! Leave your review, then upload "
                    "the order + review screenshots in the app."
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

    db = _db()
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
    db = _db()

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
            {"_id": {"$in": crwd_ids}}, _GIG_FIELDS, max_time_ms=_MAX_TIME_MS
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
            gig = _db()[_COLL_CRWDS].find_one(
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
            _db()[_COLL_PURCHASES].find(
                purchase_filter, _PURCHASE_FIELDS, max_time_ms=_MAX_TIME_MS
            )
        )
        items = _collect_buy_products(gig or {}, purchases)[:row_limit]
        return json.dumps(
            {"_type": "user_products", "items": items, "crwd_id": crwd_id, "error": None},
            ensure_ascii=False,
        )

    cursor = (
        _db()[_COLL_PURCHASES]
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
        _db()[_COLL_RECEIPTS]
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
        _db()[_COLL_NOTIFS]
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


# --- Proof submissions ---

_proof_index_ready = False


def _ensure_proof_index(coll) -> None:
    """Create the proof_submissions indexes once per process.

    Two jobs, deliberately split, because they cannot be one index:

    * The **unique** index gives *idempotency*: one accepted row per
      (purchase, member, gig, artifact type). Re-sending the identical artifact
      cannot double-store. It is partial on ``status: accepted`` so
      rejected/needs_human rows may repeat.
    * The **fraud rule** -- "every accepted row for a purchase must belong to one
      (member, gig)" -- is enforced by ``_proof_conflict``, not by an index. No
      unique index can express it: a proof id names a *purchase*, and one purchase
      legitimately backs several artifacts for one member (real gig_store_orders
      rows carry two receipt files for a single order), while still being barred
      to everyone else. Keying on the id alone hard-blocks the honest member's
      second artifact; scoping it to the member unblocks the fraudster.

    The residual race is two members storing the same purchase within the same
    instant, which leaves two accepted rows for one purchase for a human to catch.
    """
    global _proof_index_ready
    if _proof_index_ready:
        return
    try:
        coll.create_index(
            [("normalized_proof_id", 1), ("user_id", 1), ("crwd_id", 1), ("proof_type", 1)],
            unique=True,
            partialFilterExpression={"status": "accepted"},
            name="uniq_accepted_artifact",
        )
        # Backs _proof_conflict, which is the actual duplicate enforcement.
        coll.create_index(
            [("normalized_proof_id", 1), ("status", 1)], name="proof_id_status"
        )
        coll.create_index([("user_id", 1), ("created_at", -1)], name="user_recent")
    except Exception:
        # An index we cannot create must not block recording the proof.
        logger.warning("could not ensure proof_submissions indexes", exc_info=True)
    _proof_index_ready = True


def _user_email(user_id: str) -> str:
    """Best-effort email for a user id, for the internal duplicate note."""
    try:
        doc = _db()[_COLL_USERS].find_one(
            {"_id": {"$in": _id_values(user_id)}}, {"email": 1}, max_time_ms=_MAX_TIME_MS
        )
    except Exception:
        return ""
    return str((doc or {}).get("email") or "")


def _required_artifacts(crwd_id: str) -> Dict[str, Any]:
    """Artifact-level proof requirements for a gig, from its stores' requires_* flags."""
    oid = _oid(crwd_id)
    gig = _db()[_COLL_CRWDS].find_one(
        {"_id": oid if oid is not None else crwd_id},
        {"gig_stores": 1, "name": 1}, max_time_ms=_MAX_TIME_MS,
    )
    if not gig:
        return {"found": False, "required": {}, "field_level": []}
    required: Dict[str, Any] = {}
    field_level = []
    for store in gig.get("gig_stores") or []:
        for flag in _REQUIREMENT_ARTIFACTS:
            if store.get(flag):
                required.setdefault(flag, set()).update(
                    _artifacts_for(flag, store.get("store_name") or "")
                )
        for flag in _FIELD_LEVEL_REQUIREMENTS:
            if store.get(flag) and flag not in field_level:
                field_level.append(flag)
    return {"found": True, "required": required, "field_level": field_level,
            "gig_name": gig.get("name")}


def _gig_proof_completion(user_id: str, crwd_id: str) -> Dict[str, Any]:
    """Which required artifacts this member has accepted for this gig, and what's left.

    Completion means every requirement flag that demands an artifact has at least
    one accepted proof. Field-level flags (order id, rating, ...) are verified
    inside another artifact and never gate completion on their own.
    """
    spec = _required_artifacts(crwd_id)
    required = spec.get("required") or {}
    if not spec.get("found") or not required:
        # No gig, or a gig that demands no artifact -- completion is not a fact we
        # can assert. Say so rather than defaulting to "complete".
        return {
            "complete": False, "determinable": bool(spec.get("found")) and bool(required),
            "satisfied": [], "outstanding": sorted(required),
            "field_level": spec.get("field_level") or [],
            "accepted_types": [],
        }
    accepted = set()
    cursor = _db()[_COLL_PROOFS].find(
        {"user_id": str(user_id).strip(), "crwd_id": str(crwd_id).strip(),
         "status": "accepted"},
        {"proof_type": 1}, max_time_ms=_MAX_TIME_MS,
    )
    for row in cursor:
        if row.get("proof_type"):
            accepted.add(row["proof_type"])
    satisfied = [flag for flag, types in required.items() if accepted & types]
    outstanding = [flag for flag in required if flag not in satisfied]
    return {
        "complete": not outstanding,
        "determinable": True,
        "satisfied": sorted(satisfied),
        "outstanding": sorted(outstanding),
        # What would satisfy each outstanding flag at this gig's store(s) -- store
        # aware, so a Target review link accepts a screenshot and an Amazon one
        # does not.
        "accepts": {flag: sorted(required[flag]) for flag in sorted(outstanding)},
        "field_level": spec.get("field_level") or [],
        "accepted_types": sorted(accepted),
        "gig_name": spec.get("gig_name"),
    }


def _mark_proof_risk_scored(proof_record_id: str) -> str:
    """Flag a proof as risk-scored so it is never scored twice.

    The risk skill runs every turn against a delta-only score with no history, so
    a second pass over the same proof would silently double a member's risk. This
    is the only durable guard -- turn-local memory loses on any retry or resume.

    Deliberately the narrowest possible write: one boolean, on our own collection,
    on one record. It cannot touch any other field.
    """
    proof_record_id = (proof_record_id or "").strip()
    if not proof_record_id:
        return tool_error("proof_record_id is required for mark_proof_risk_scored")
    oid = _oid(proof_record_id)
    if oid is None:
        return tool_error("proof_record_id must be a 24-hex proof record id")
    coll = _db()[_COLL_PROOFS]
    # Match on risk_scored too, so "already marked" is decided by the filter.
    # modified_count cannot tell us: the $set bumps updated_at, so the document
    # always changes and modified_count is never 0.
    result = coll.update_one(
        {"_id": oid, "risk_scored": {"$ne": True}},
        {"$set": {"risk_scored": True, "updated_at": _now()}},
    )
    if result.matched_count == 0:
        # No match means either no such record, or it was already marked -- and
        # those must not be conflated: one is a caller error, the other is the
        # double-score guard firing.
        if coll.count_documents({"_id": oid}, limit=1) == 0:
            return tool_error("no proof record with that id")
        return json.dumps(
            {
                "_type": "crwd_proof_risk_scored", "proof_record_id": proof_record_id,
                "marked": True, "already_marked": True, "error": None,
            },
            ensure_ascii=False, default=str,
        )
    return json.dumps(
        {
            "_type": "crwd_proof_risk_scored", "proof_record_id": proof_record_id,
            "marked": True, "already_marked": False, "error": None,
        },
        ensure_ascii=False, default=str,
    )


def _check_gig_proof_completion(user_id: str, crwd_id: str) -> str:
    """Has this member submitted every proof artifact the gig requires?"""
    user_id = (user_id or "").strip()
    crwd_id = (crwd_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for check_gig_proof_completion")
    if not crwd_id:
        return tool_error("crwd_id is required for check_gig_proof_completion")
    out = _gig_proof_completion(user_id, crwd_id)
    out["_type"] = "crwd_gig_proof_completion"
    out["error"] = None
    return json.dumps(out, ensure_ascii=False, default=str)


def _store_proof(
    proof_id: str,
    proof_type: str,
    user_id: str,
    status: str,
    reason_code: str,
    reason: str,
    crwd_id: str = "",
    gig_name: str = "",
    confidence: str = "",
    proof_info: Optional[Dict[str, Any]] = None,
    product_name: str = "",
    store_name: str = "",
    source_url: str = "",
    proof_link: str = "",
) -> str:
    """Record one proof submission. The only write path in this module."""
    proof_id = (proof_id or "").strip()
    proof_type = (proof_type or "").strip().lower()
    user_id = (user_id or "").strip()
    status = (status or "").strip().lower()
    reason_code = (reason_code or "").strip()
    reason = (reason or "").strip()
    confidence = (confidence or "").strip().lower()
    product_name = (product_name or "").strip()
    store_name = _norm_store(store_name)

    if not proof_id:
        return tool_error("proof_id is required for store_proof")
    if not user_id:
        return tool_error("user_id is required for store_proof")
    # An accepted proof must be one we actually looked at. Without this, a typed
    # order number with no image can be accepted and complete a gig -- the whole
    # of the order-number-guessing hole. rejected/needs_human are exempt: we must
    # be able to record a proof we could not read.
    if status == "accepted" and not (source_url or "").strip() and not (proof_link or "").strip():
        return tool_error(
            "an accepted proof needs source_url (the attachment you read) or proof_link "
            "(the link you opened) -- never accept a proof with no evidence attached"
        )
    if proof_type not in _PROOF_TYPES:
        return tool_error(f"proof_type must be one of: {', '.join(sorted(_PROOF_TYPES))}")
    if status not in _PROOF_STATUSES:
        return tool_error(f"status must be one of: {', '.join(sorted(_PROOF_STATUSES))}")
    if confidence and confidence not in _PROOF_CONFIDENCE:
        return tool_error(f"confidence must be one of: {', '.join(sorted(_PROOF_CONFIDENCE))}")
    # Required on every status, accepted included: an approval with no recorded
    # reason cannot be audited later.
    if not reason_code:
        return tool_error("reason_code is required for store_proof (use clean_match on an accept)")
    if reason_code not in _PROOF_REASON_CODES:
        return tool_error(
            f"reason_code must be one of: {', '.join(sorted(_PROOF_REASON_CODES))}"
        )
    if not reason:
        return tool_error("reason is required for store_proof, including when status is accepted")

    normalized = _normalize_proof_id(proof_id, proof_type)
    if not normalized:
        return tool_error(
            "proof_id could not be normalized into a dedup key -- do not invent one; "
            "store the proof as needs_human with reason_code no_identifier instead"
        )

    coll = _db()[_COLL_PROOFS]
    _ensure_proof_index(coll)

    # The fraud rule lives here, not in an index (see _ensure_proof_index): an
    # accepted purchase belongs to exactly one (member, gig).
    if status == "accepted":
        conflict = _proof_conflict(normalized, user_id=user_id, crwd_id=crwd_id)
        if conflict is not None:
            return json.dumps(
                {
                    "_type": "crwd_proof_stored", "stored": False, "duplicate": True,
                    "already_recorded": False,
                    "normalized_proof_id": normalized, "conflict": conflict,
                    "error": None,
                },
                ensure_ascii=False, default=str,
            )

    now = _now()
    doc = {
        "proof_id": proof_id,
        "normalized_proof_id": normalized,
        "proof_type": proof_type,
        "user_id": user_id,
        "user_email": _user_email(user_id),
        "crwd_id": crwd_id or "",
        "gig_name": gig_name or "",
        "status": status,
        "reason_code": reason_code,
        "reason": reason,
        "confidence": confidence or "",
        # Promoted to top level because risk groups by them ("how many
        # wrong_product at this store") -- a nested free-form blob indexes poorly.
        "product_name": product_name,
        "store_name": store_name,
        # Everything else we could read off the proof, shaped by proof_type.
        "metadata": {"proof_info": proof_info if isinstance(proof_info, dict) else {}},
        # The risk skill runs every turn against a delta-only tool, so it must be
        # able to tell an unscored proof from one it already scored.
        "risk_scored": False,
        "source_url": source_url or "",
        "proof_link": proof_link or "",
        # True only on the proof whose acceptance completes the gig; False on every
        # proof submitted before it. Computed here, never taken from the caller --
        # it is a fact about DB state, not a judgement.
        "is_gig_completed": False,
        "conversation_id": (os.getenv("HERMES_SESSION_CHAT_ID") or "").strip(),
        "created_at": now,
        "updated_at": now,
        "created_by": "hermes",
    }
    if status == "accepted" and crwd_id:
        # Would this acceptance leave nothing outstanding? Evaluate against the
        # rows already on file plus this one.
        progress = _gig_proof_completion(user_id, crwd_id)
        if progress.get("determinable"):
            accepts = progress.get("accepts") or {}
            still_out = [
                flag for flag in progress.get("outstanding") or []
                if proof_type not in set(accepts.get(flag) or ())
            ]
            doc["is_gig_completed"] = not still_out

    from pymongo.errors import DuplicateKeyError

    try:
        result = coll.insert_one(doc)
    except DuplicateKeyError:
        # This exact artifact is already on file for this member+gig -- an
        # idempotent re-send, NOT a duplicate proof. Do not flip the verdict.
        return json.dumps(
            {
                "_type": "crwd_proof_stored", "stored": False, "duplicate": False,
                "already_recorded": True,
                "normalized_proof_id": normalized, "status": status,
                "error": None,
            },
            ensure_ascii=False, default=str,
        )
    return json.dumps(
        {
            "_type": "crwd_proof_stored", "stored": True, "duplicate": False,
            "already_recorded": False,
            "proof_record_id": str(result.inserted_id),
            "normalized_proof_id": normalized, "status": status,
            # True = this proof completed the gig's required artifacts.
            "is_gig_completed": doc["is_gig_completed"],
            "error": None,
        },
        ensure_ascii=False, default=str,
    )


def _proof_conflict(
    normalized: str, user_id: str = "", crwd_id: str = ""
) -> Optional[Dict[str, Any]]:
    """An accepted record that would *block* this submission, if any.

    A record by the same member on the same gig does not block: one purchase
    legitimately backs several artifacts (order screenshot + receipt). Only
    another member's claim on the purchase, or the same member reusing it on a
    different gig, is a real conflict.
    """
    query: Dict[str, Any] = {"normalized_proof_id": normalized, "status": "accepted"}
    if user_id and crwd_id:
        query["$nor"] = [{"user_id": str(user_id).strip(), "crwd_id": str(crwd_id).strip()}]
    doc = _db()[_COLL_PROOFS].find_one(
        query,
        {
            "user_id": 1, "user_email": 1, "crwd_id": 1,
            "gig_name": 1, "proof_type": 1, "created_at": 1,
        },
        max_time_ms=_MAX_TIME_MS,
    )
    return _serialize_doc(doc) if doc else None


def _check_duplicate_proof(
    proof_id: str, proof_type: str = "", user_id: str = "", crwd_id: str = ""
) -> str:
    """Can this proof id still be accepted? Advisory -- the unique index decides.

    Pass ``crwd_id`` alongside ``user_id``: without it, this cannot tell the
    member's own second artifact for the same gig from a real conflict, and will
    report a duplicate that ``store_proof`` would happily accept.
    """
    proof_id = (proof_id or "").strip()
    if not proof_id:
        return tool_error("proof_id is required for check_duplicate_proof")
    normalized = _normalize_proof_id(proof_id, proof_type)
    if not normalized:
        return tool_error(
            "proof_id could not be normalized into a dedup key -- treat the proof as "
            "needs_human with reason_code no_identifier rather than guessing an id"
        )
    conflict = _proof_conflict(normalized, user_id=user_id, crwd_id=crwd_id)
    same_user = bool(
        conflict and user_id
        and str(conflict.get("user_id") or "") == str(user_id).strip()
    )
    return json.dumps(
        {
            "_type": "crwd_proof_duplicate_check",
            "normalized_proof_id": normalized,
            "duplicate": conflict is not None,
            # True = same member reusing this purchase on a DIFFERENT gig.
            "same_user": same_user,
            "conflict": conflict,
            "error": None,
        },
        ensure_ascii=False, default=str,
    )


def _find_proof(
    proof_id: str, proof_type: str = "", user_id: str = "", limit: int = 10
) -> str:
    """Full submission history for a proof id, every status included."""
    proof_id = (proof_id or "").strip()
    if not proof_id:
        return tool_error("proof_id is required for find_proof")
    normalized = _normalize_proof_id(proof_id, proof_type)
    if not normalized:
        return tool_error("proof_id could not be normalized into a lookup key")
    query: Dict[str, Any] = {"normalized_proof_id": normalized}
    if proof_type:
        query["proof_type"] = str(proof_type).strip().lower()
    if user_id:
        query["user_id"] = str(user_id).strip()
    row_limit = max(1, min(int(limit or 10), _HARD_LIMIT))
    cursor = (
        _db()[_COLL_PROOFS]
        .find(query, max_time_ms=_MAX_TIME_MS)
        .sort("created_at", -1)
        .limit(row_limit)
    )
    items = _serialize_docs(list(cursor))
    return json.dumps(
        {
            "_type": "crwd_proof_lookup", "items": items,
            "count": len(items), "error": None,
        },
        ensure_ascii=False, default=str,
    )


# --- custom_query escape hatch ---

def _has_where(obj: Any) -> bool:
    if isinstance(obj, dict):
        if "$where" in obj:
            return True
        return any(_has_where(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_where(v) for v in obj)
    return False


def _redact_secrets(doc: Any) -> Any:
    """Strip any password/token/otp/secret-looking key at any depth.

    Applied to every custom_query result, not just ``users`` -- e.g.
    ``notifications`` carries device/chat tokens.
    """
    if isinstance(doc, dict):
        return {
            k: _redact_secrets(v)
            for k, v in doc.items()
            if not _USER_SECRET_RE.search(str(k))
        }
    if isinstance(doc, list):
        return [_redact_secrets(v) for v in doc]
    return doc


def _custom_query(
    collection: str,
    operation: str,
    filter: Optional[Dict[str, Any]] = None,
    projection: Optional[Dict[str, Any]] = None,
    sort: Optional[Dict[str, Any]] = None,
    limit: int = 20,
) -> str:
    if collection not in _ALLOWED_COLLECTIONS:
        return tool_error(
            f"collection must be one of {sorted(_ALLOWED_COLLECTIONS)}"
        )
    if operation not in {"find", "count"}:
        return tool_error("operation must be 'find' or 'count'")
    filter = filter or {}
    if not isinstance(filter, dict):
        return tool_error("filter must be an object")
    if _has_where(filter):
        return tool_error("$where is not allowed")

    coll = _db()[collection]
    if operation == "count":
        total = coll.count_documents(filter, maxTimeMS=_MAX_TIME_MS)
        return json.dumps(
            {"_type": "custom_query_result", "operation": "count",
             "collection": collection, "count": total, "error": None},
            ensure_ascii=False,
        )

    row_limit = max(1, min(int(limit or _HARD_LIMIT), _HARD_LIMIT))
    proj = projection if isinstance(projection, dict) else None
    cursor = coll.find(filter, proj, max_time_ms=_MAX_TIME_MS)
    if isinstance(sort, dict) and sort:
        cursor = cursor.sort(list(sort.items()))
    docs = [_redact_secrets(d) for d in _serialize_docs(list(cursor.limit(row_limit)))]
    return json.dumps(
        {"_type": "custom_query_result", "operation": "find",
         "collection": collection, "items": docs, "count": len(docs), "error": None},
        ensure_ascii=False,
    )


# --- Prefetch helpers (used by app-chatbot CLI router and other hooks) ---

def _parse_tool_payload(raw: str) -> Dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "invalid tool response"}
    if isinstance(payload, dict) and payload.get("error"):
        return {"success": False, "error": payload["error"]}
    return payload


def fetch_active_gigs(user_id: str, *, limit: int = 10, offset: int = 0) -> Dict[str, Any]:
    """Return list_active_gigs payload as a dict."""
    if not check_crwd_db_requirements():
        return {"success": False, "error": "CRWD_MONGO_URI is not configured"}
    user_id = (user_id or "").strip()
    if not user_id:
        return {"success": False, "error": "user_id is required", "items": []}
    return _parse_tool_payload(_list_active_gigs(limit=limit, user_id=user_id, offset=offset))


def fetch_user_joined_gigs(user_id: str, limit: int = 10) -> Dict[str, Any]:
    if not check_crwd_db_requirements():
        return {"success": False, "error": "CRWD_MONGO_URI is not configured"}
    return _parse_tool_payload(_get_user_gigs(user_id=user_id, limit=limit))


def fetch_waitlisted_gigs(user_id: str, limit: int = 10) -> Dict[str, Any]:
    if not check_crwd_db_requirements():
        return {"success": False, "error": "CRWD_MONGO_URI is not configured"}
    return _parse_tool_payload(_get_waitlisted_gigs(user_id=user_id, limit=limit))


def fetch_user_gig_history(user_id: str, limit: int = 50) -> Dict[str, Any]:
    if not check_crwd_db_requirements():
        return {"success": False, "error": "CRWD_MONGO_URI is not configured"}
    return _parse_tool_payload(_get_user_gig_history(user_id=user_id, limit=limit))


def fetch_user_profile(user_id: str) -> Dict[str, Any]:
    if not check_crwd_db_requirements():
        return {"success": False, "error": "CRWD_MONGO_URI is not configured"}
    user_id = (user_id or "").strip()
    if not user_id:
        return {"success": False, "error": "user_id is required"}
    payload = _parse_tool_payload(_get_user(identifier=user_id))
    items = payload.get("items") or []
    if not items:
        return {"success": False, "error": f"User not found: {user_id}"}
    user = items[0]
    return {
        "success": True,
        "user": {
            "_id": user.get("_id", {}).get("$oid") if isinstance(user.get("_id"), dict) else str(user.get("_id", "")),
            "email": user.get("email"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "full_name": user.get("full_name"),
            "phone": user.get("phone"),
            "status": user.get("status"),
            "bio": user.get("bio"),
            "city": user.get("city"),
            "state": user.get("state"),
            "country": user.get("country"),
        },
    }


def fetch_gig_details(query: str, *, full: bool = True) -> Dict[str, Any]:
    if not check_crwd_db_requirements():
        return {"success": False, "error": "CRWD_MONGO_URI is not configured"}
    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "Provide gig_id or name"}
    payload = _parse_tool_payload(_get_gig_details(query=query, top_n=1, full=full))
    items = payload.get("items") or []
    if not items:
        err = payload.get("error") or f"Gig not found: {query}"
        return {"success": False, "error": err}
    return {"success": True, "gig": items[0]}


# --- Router ---

def crwd_db_tool(args: Dict[str, Any], **_kw: Any) -> str:
    if not check_crwd_db_requirements():
        return tool_error("CRWD_MONGO_URI is not configured")

    action = str(args.get("action", "")).strip()
    try:
        if action == "list_active_gigs":
            return _list_active_gigs(
                limit=args.get("limit", 5),
                user_id=args.get("user_id", ""),
                offset=args.get("offset", 0),
            )
        if action == "get_gig_details":
            return _get_gig_details(
                query=args.get("query", ""),
                top_n=args.get("top_n", 3),
                full=bool(args.get("full")),
            )
        if action == "get_user":
            return _get_user(identifier=args.get("identifier", ""))
        if action == "get_user_gigs":
            return _get_user_gigs(user_id=args.get("user_id", ""), limit=args.get("limit", 10))
        if action == "get_user_gig_history":
            return _get_user_gig_history(
                user_id=args.get("user_id", ""), limit=args.get("limit", 50)
            )
        if action == "get_waitlisted_gigs":
            return _get_waitlisted_gigs(
                user_id=args.get("user_id", ""), limit=args.get("limit", 10)
            )
        if action == "get_user_products":
            return _get_user_products(
                user_id=args.get("user_id", ""),
                limit=args.get("limit", 10),
                crwd_id=args.get("crwd_id", "") or args.get("gig_id", ""),
            )
        if action == "get_user_receipts":
            return _get_user_receipts(user_id=args.get("user_id", ""), limit=args.get("limit", 10))
        if action == "get_user_notifications":
            return _get_user_notifications(user_id=args.get("user_id", ""), limit=args.get("limit", 10))
        if action == "get_user_gig_status":
            return _get_user_gig_status(
                user_id=args.get("user_id", ""),
                crwd_id=args.get("crwd_id", ""),
                gig_name=args.get("gig_name", ""),
                include_waitlisted=bool(args.get("include_waitlisted")),
                limit=args.get("limit", _HARD_LIMIT),
            )
        if action == "store_proof":
            return _store_proof(
                proof_id=args.get("proof_id", ""),
                proof_type=args.get("proof_type", ""),
                user_id=args.get("user_id", ""),
                status=args.get("status", ""),
                reason_code=args.get("reason_code", ""),
                reason=args.get("reason", ""),
                crwd_id=args.get("crwd_id", "") or args.get("gig_id", ""),
                gig_name=args.get("gig_name", ""),
                confidence=args.get("confidence", ""),
                proof_info=args.get("proof_info"),
                product_name=args.get("product_name", ""),
                store_name=args.get("store_name", ""),
                source_url=args.get("source_url", ""),
                proof_link=args.get("proof_link", ""),
            )
        if action == "mark_proof_risk_scored":
            return _mark_proof_risk_scored(
                proof_record_id=args.get("proof_record_id", ""),
            )
        if action == "check_duplicate_proof":
            return _check_duplicate_proof(
                proof_id=args.get("proof_id", ""),
                proof_type=args.get("proof_type", ""),
                user_id=args.get("user_id", ""),
                crwd_id=args.get("crwd_id", "") or args.get("gig_id", ""),
            )
        if action == "check_gig_proof_completion":
            return _check_gig_proof_completion(
                user_id=args.get("user_id", ""),
                crwd_id=args.get("crwd_id", "") or args.get("gig_id", ""),
            )
        if action == "find_proof":
            return _find_proof(
                proof_id=args.get("proof_id", ""),
                proof_type=args.get("proof_type", ""),
                user_id=args.get("user_id", ""),
                limit=args.get("limit", 10),
            )
        if action == "custom_query":
            return _custom_query(
                collection=str(args.get("collection", "")),
                operation=str(args.get("operation", "")),
                filter=args.get("filter"),
                projection=args.get("projection"),
                sort=args.get("sort"),
                limit=args.get("limit", 20),
            )
        return tool_error(
            "Unknown action. Use: list_active_gigs, get_gig_details, get_user, "
            "get_user_gigs, get_user_gig_history, get_waitlisted_gigs, get_user_gig_status, "
            "get_user_products, get_user_receipts, get_user_notifications, "
            "store_proof, check_duplicate_proof, find_proof, check_gig_proof_completion, "
            "mark_proof_risk_scored, custom_query"
        )
    except RuntimeError as exc:
        # Config/connection problems -- safe to surface the short message.
        return tool_error(str(exc))
    except Exception:
        logger.exception("crwd_db action %r failed", action)
        return tool_error("query failed")


# --- Schema ---

CRWD_DB_SCHEMA = {
    "name": "crwd_db",
    "description": (
        "Query CRWD's MongoDB data: gigs/campaigns, users, campaign "
        "membership, a member's approved products (buy links), their receipt/"
        "proof upload status, and their account notifications. Read-only apart "
        "from the proof-submission actions below. "
        "Gig scope: list_active_gigs = open gigs the member has NOT joined "
        "(available/browse/join questions). get_user_gig_status / get_user_gigs = "
        "enrolled in-progress memberships only (my gigs, next steps, proof). "
        "Ambiguous bare asks (e.g. list gigs, give gigs) → get_user_gig_status first, "
        "then a mandatory clarifying question about open gigs — never skip the follow-up. "
        "Never answer an available-gig question from enrolled actions or vice versa. "
        "Use the specific action if it fits (list_active_gigs, get_gig_details, "
        "get_user, get_user_gigs, get_user_gig_history, get_waitlisted_gigs, get_user_gig_status, "
        "get_user_products, "
        "get_user_receipts, get_user_notifications); use custom_query only when none of the "
        "others answer the question. list_active_gigs accepts user_id to "
        "exclude gigs the member already has a membership for, and offset for "
        "pagination; it returns has_more and next_offset for the next page. "
        "get_gig_details fuzzy-matches gig names and returns ranked candidates "
        "(set full=true or top_n=1 for the full gig payload). Each store carries a "
        "requirements dict (requires_receipt, requires_review_link, requires_review_rating, "
        "requires_ugc_post, ...) — these flags are the gig's proof spec; use them, not "
        "type_of_work_proof, which is unset on almost every gig. "
        "get_user_gig_history returns past membership rows including rejected/completed gigs. "
        "get_waitlisted_gigs returns gigs the member applied for but is not "
        "yet accepted into (isAccepted false / pending approval). "
        "get_user_gig_status returns per-gig stage and personalized next_step "
        "from membership + proof progress. "
        "Proof submissions (used by the crwd-proof-validator skill): "
        "check_duplicate_proof asks whether a proof id is already claimed — pass "
        "user_id AND crwd_id, because a proof id names a purchase, not a submission: "
        "the same member may back one gig with several artifacts of the same purchase "
        "(order screenshot + receipt), but another member using it, or the same member "
        "reusing it on a different gig, is a duplicate. find_proof returns the full "
        "submission history for a proof id across every status; store_proof records one "
        "validated submission (reason_code and reason are required on every status, "
        "accepted included) and sets is_gig_completed itself on the proof that leaves "
        "nothing outstanding. check_gig_proof_completion(user_id, crwd_id) reports which "
        "required artifacts are accepted and which are still outstanding — use it to know "
        "what to coach for, and to decide whether the gig is done. Proof ids are normalized "
        "before comparison, so REC#/Order # prefixes, spacing and hyphens do not matter, "
        "and UGC links key on platform:post_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_active_gigs", "get_gig_details", "get_user",
                    "get_user_gigs", "get_user_gig_history", "get_waitlisted_gigs",
                    "get_user_gig_status",
                    "get_user_products",
                    "get_user_receipts", "get_user_notifications",
                    "store_proof", "check_duplicate_proof", "find_proof",
                    "check_gig_proof_completion", "mark_proof_risk_scored",
                    "custom_query",
                ],
            },
            "limit": {"type": "integer", "description": "max rows per page (capped at 20; list_active_gigs default 5; get_user_gig_status default 20)"},
            "offset": {
                "type": "integer",
                "description": (
                    "skip N results for pagination (list_active_gigs). "
                    "Use next_offset from the previous result to get the next page."
                ),
            },
            "identifier": {"type": "string", "description": "email, phone, or user _id (get_user)"},
            "user_id": {
                "type": "string",
                "description": (
                    "users._id. For list_active_gigs: exclude gigs the member "
                    "already has a membership for. Also used by get_user_gigs, "
                    "get_user_gig_history, get_waitlisted_gigs, get_user_products, get_user_receipts, "
                    "get_user_notifications, get_user_gig_status. Required by store_proof; "
                    "optional on check_duplicate_proof (to tell a self-resubmit from another "
                    "member's proof) and find_proof (to filter)."
                ),
            },
            "crwd_id": {
                "type": "string",
                "description": (
                    "Optional gig _id. For get_user_gig_status: filter to that gig. "
                    "For get_user_products: return every product on that gig's catalog "
                    "(plus the member's purchase rows for it), not only one buy_link."
                ),
            },
            "gig_id": {
                "type": "string",
                "description": "Alias of crwd_id for get_user_products",
            },
            "gig_name": {
                "type": "string",
                "description": (
                    "Optional fuzzy gig name filter (get_user_gig_status); "
                    "the gig's name to record alongside the proof (store_proof)"
                ),
            },
            "include_waitlisted": {
                "type": "boolean",
                "description": "Include waitlisted memberships (get_user_gig_status)",
            },
            "query": {"type": "string", "description": "gig _id, name, or free text to fuzzy-match (get_gig_details)"},
            "top_n": {"type": "integer", "description": "max candidates to return, default 3, max 10 (get_gig_details)"},
            "full": {
                "type": "boolean",
                "description": "Return full gig payload for get_gig_details (terms, stores, targeting)",
            },
            "proof_id": {
                "type": "string",
                "description": (
                    "The proof's own identifier, as extracted (store_proof, "
                    "check_duplicate_proof, find_proof). Target REC#, Amazon Order #, "
                    "Amazon review link, or UGC post link. Normalized before comparison. "
                    "Never invent one: if no identifier can be read, store the proof as "
                    "needs_human with reason_code no_identifier."
                ),
            },
            "proof_type": {
                "type": "string",
                "enum": [
                    "receipt_target", "receipt_amazon", "receipt_other",
                    "order_screenshot", "review_screenshot", "amazon_review_link",
                    "ugc_link",
                ],
                "description": (
                    "The artifact's kind. An order confirmation (order_screenshot) and "
                    "the receipt for that same order are different artifacts of one "
                    "purchase and share an order number — typing them apart is what "
                    "lets a member record both."
                ),
            },
            "status": {
                "type": "string",
                "enum": ["accepted", "rejected", "needs_human"],
                "description": "Verdict for this proof (store_proof)",
            },
            "reason_code": {
                "type": "string",
                "enum": [
                    "clean_match", "duplicate_proof", "gig_not_active_for_user",
                    "wrong_proof_type", "incomplete_submission", "date_outside_gig_window",
                    "no_identifier", "invalid_order_number", "wrong_product",
                    "wrong_quantity", "unreadable", "suspected_edited",
                    "link_unreachable", "link_not_owned", "content_mismatch",
                ],
                "description": (
                    "Required on every store_proof, accepted included (use clean_match on "
                    "an accept). Internal only — never tell the member."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "Required on every store_proof: one human-readable line on what "
                    "matched or failed. Internal only — never tell the member."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Confidence the proof is authentic and matches the gig (store_proof)",
            },
            "proof_info": {
                "type": "object",
                "description": (
                    "Everything you read off the proof, shaped by proof_type; stored as "
                    "metadata.proof_info (store_proof). "
                    "Receipts/order screenshots: merchant_name, store_location, purchase_date, "
                    "order_number, total_amount, tax_amount, payment_method, "
                    "line_items[{product_name, quantity, price, amount}]. "
                    "Reviews: platform, rating, review_text, handle, posted_at, verified_purchase. "
                    "UGC: platform, handle, posted_at, likes, comments, views, caption. "
                    "Record what you actually saw — the risk assessment reads this."
                ),
            },
            "product_name": {
                "type": "string",
                "description": "The gig product this proof is for, as matched (store_proof)",
            },
            "store_name": {
                "type": "string",
                "description": "Store the proof came from; normalized on write (store_proof)",
            },
            "source_url": {"type": "string", "description": "Attachment/media URL that was read. Required on an accepted proof (store_proof)"},
            "proof_link": {"type": "string", "description": "Member-supplied review/UGC link, when the proof is a link. Satisfies the evidence requirement on an accept (store_proof)"},
            "proof_record_id": {
                "type": "string",
                "description": "proof_record_id returned by store_proof (mark_proof_risk_scored)",
            },
            "collection": {"type": "string", "enum": [
                "crwds", "users", "added_crwd_members",
                "user_product_purchases", "receipt_upload_history", "notifications",
                "proof_submissions",
            ]},
            "operation": {"type": "string", "enum": ["find", "count"]},
            "filter": {"type": "object"},
            "projection": {"type": "object"},
            "sort": {"type": "object"},
        },
        "required": ["action"],
    },
}


# --- Registration ---

registry.register(
    name="crwd_db",
    toolset="crwd",
    schema=CRWD_DB_SCHEMA,
    handler=crwd_db_tool,
    check_fn=check_crwd_db_requirements,
    requires_env=["CRWD_MONGO_URI"],
    emoji="🛍️",
)
