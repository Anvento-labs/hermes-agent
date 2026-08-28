"""Proof submission storage, dedup, completion, and risk-scored flag."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from tools.registry import tool_error

from tools.crwd_db import connection as _conn
from tools.crwd_db.connection import (
    _COLL_CRWDS,
    _COLL_MEMBERS,
    _COLL_PROOFS,
    _COLL_USERS,
    _HARD_LIMIT,
    _MAX_TIME_MS,
    _id_values,
    _now,
    _oid,
)

from tools.crwd_db.membership import _mark_membership_approved, _member_or_filter
from tools.crwd_db.serialize import _serialize_doc, _serialize_docs
from tools.crwd_urls import attach_gig_url

logger = logging.getLogger(__name__)


def _linkify_proof_payload(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Attach markdown gig titles on proof-shaped tool payloads (read path only).

    Proof docs use ``_id`` for the proof record and ``crwd_id`` for the gig —
    ``attach_gig_url`` prefers ``crwd_id`` / ``gig_id`` over ``_id``.
    """
    if not item:
        return item
    return attach_gig_url(item, inline_name=True)

_PROOF_TYPES = {
    "receipt_target", "receipt_amazon", "receipt_other",
    "order_screenshot", "review_screenshot", "ugc_link",
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
    # Legacy name: no store gives a review a link we can read (Target's is the
    # product page; Amazon's needs a login), so a screenshot is the only proof.
    "requires_review_link": {"review_screenshot"},
    "requires_ugc_post": {"ugc_link"},
}


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
    """What can satisfy this requirement flag. ``store_name`` no longer changes
    the answer -- a review is a screenshot everywhere -- but callers pass it."""
    return set(_REQUIREMENT_ARTIFACTS.get(flag) or set())
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
_REVIEW_SCREENSHOT_KEY_RE = re.compile(
    r"^([0-9a-fA-F]{24})\s*[:|/]\s*(.+?)\s*[:|/]\s*(.+)$"
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

    Review screenshots key on ``{crwd_id}:{handle}:{date}``, read off the image.
    The handle is load-bearing: without it, two honest members reviewing one gig
    on one day collide and the second is rejected as a duplicate. The product is
    deliberately absent -- phone screenshots truncate titles, so it would hash two
    ways for one review. This slugifies only; date, product and legibility checks
    are skill-side (vision). Review *urls* never key: Target's is the product page
    and Amazon's needs a login, so neither can be read.

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

    if proof_type == "review_screenshot":
        if is_url:
            return ""
        match = _REVIEW_SCREENSHOT_KEY_RE.match(raw)
        if not match:
            return ""
        crwd_id = match.group(1).lower()
        handle_slug = re.sub(r"[^a-z0-9]+", "-", match.group(2).lower()).strip("-")
        date_slug = re.sub(r"[^a-z0-9]+", "-", match.group(3).lower()).strip("-")
        if not handle_slug or not date_slug:
            return ""
        return f"{crwd_id}:{handle_slug}:{date_slug}"

    if proof_type in _RECEIPT_TYPES or not is_url:
        digits = re.sub(r"\D", "", _ORDER_PREFIX_RE.sub("", raw))
        if not _order_number_plausible(digits, proof_type):
            return ""
        return digits

    # An unrecognized url is not a defensible key: it may identify a product or a
    # share target rather than this member's submission. Say so instead of
    # inventing a host+path key.
    return ""


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
        doc = _conn._db()[_COLL_USERS].find_one(
            {"_id": {"$in": _id_values(user_id)}}, {"email": 1}, max_time_ms=_MAX_TIME_MS
        )
    except Exception:
        return ""
    return str((doc or {}).get("email") or "")


def _required_artifacts(crwd_id: str) -> Dict[str, Any]:
    """Artifact-level proof requirements for a gig, from its stores' requires_* flags."""
    oid = _oid(crwd_id)
    gig = _conn._db()[_COLL_CRWDS].find_one(
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
            "gig_name": gig.get("name"), "crwd_id": str(crwd_id).strip()}


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
    cursor = _conn._db()[_COLL_PROOFS].find(
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
        # What would satisfy each outstanding flag. requires_review_link takes a
        # review_screenshot at every store -- the flag's name is legacy.
        "accepts": {flag: sorted(required[flag]) for flag in sorted(outstanding)},
        "field_level": spec.get("field_level") or [],
        "accepted_types": sorted(accepted),
        "gig_name": spec.get("gig_name"),
        "crwd_id": str(crwd_id).strip(),
    }


def user_has_completed_gig(user_id: str) -> Optional[bool]:
    """True when the member has completed ≥1 gig (all required proofs accepted).

    Payment/payout status is irrelevant. Returns ``None`` when Mongo is
    unavailable or the lookup fails (caller should not guess ``new-user``).
    """
    user_id = (user_id or "").strip()
    if not user_id:
        return None
    if not (os.getenv("CRWD_MONGO_URI") or "").strip():
        return None
    try:
        # Fast path: a store_proof that completed a gig sets this flag.
        done = _conn._db()[_COLL_PROOFS].find_one(
            {"user_id": user_id, "is_gig_completed": True},
            {"_id": 1},
            max_time_ms=_MAX_TIME_MS,
        )
        if done:
            return True
        # Legacy / edge: recompute completion from memberships + accepted proofs.
        crwd_ids: List[str] = []
        for row in _conn._db()[_COLL_MEMBERS].find(
            _member_or_filter(user_id),
            {"crwd_id": 1},
            max_time_ms=_MAX_TIME_MS,
        ).limit(50):
            cid = str(row.get("crwd_id") or "").strip()
            if cid and cid not in crwd_ids:
                crwd_ids.append(cid)
        for crwd_id in crwd_ids:
            progress = _gig_proof_completion(user_id, crwd_id)
            if progress.get("determinable") and progress.get("complete"):
                return True
        return False
    except Exception:
        logger.debug("user_has_completed_gig lookup failed", exc_info=True)
        return None


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
    coll = _conn._db()[_COLL_PROOFS]
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
    return json.dumps(
        _linkify_proof_payload(out), ensure_ascii=False, default=str,
    )


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
    # Required on every status, accepted included: an acceptance with no recorded
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

    coll = _conn._db()[_COLL_PROOFS]
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
    if doc["is_gig_completed"]:
        # Surfaces the member in CRWD's own "approved" bucket now that every
        # required artifact is in. Deliberately absent from the payload below:
        # the proof reply must never carry a membership-status claim, and a
        # field named for one is an invitation to narrate it.
        _mark_membership_approved(user_id, crwd_id)
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
    doc = _conn._db()[_COLL_PROOFS].find_one(
        query,
        {
            "user_id": 1, "user_email": 1, "crwd_id": 1,
            "gig_name": 1, "proof_type": 1, "created_at": 1,
        },
        max_time_ms=_MAX_TIME_MS,
    )
    return _linkify_proof_payload(_serialize_doc(doc)) if doc else None


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


def _get_user_proofs(
    user_id: str, crwd_id: str = "", status: str = "", limit: int = 10
) -> str:
    """Everything this member has submitted -- optionally just for one gig.

    The member-centric read. ``find_proof`` is keyed on a proof id, which answers
    "who else touched this purchase"; it cannot answer "what have I submitted?" --
    the question a coach is actually asked. Without this the agent had to ask the
    member for an order number it had already stored.
    """
    user_id = (user_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for get_user_proofs")
    query: Dict[str, Any] = {"user_id": user_id}
    if crwd_id:
        query["crwd_id"] = str(crwd_id).strip()
    if status:
        status = status.strip().lower()
        if status not in _PROOF_STATUSES:
            return tool_error(f"status must be one of: {', '.join(sorted(_PROOF_STATUSES))}")
        query["status"] = status
    row_limit = max(1, min(int(limit or 10), _HARD_LIMIT))
    cursor = (
        _conn._db()[_COLL_PROOFS]
        .find(query, max_time_ms=_MAX_TIME_MS)
        .sort("created_at", -1)
        .limit(row_limit)
    )
    items = [
        _linkify_proof_payload(doc) for doc in _serialize_docs(list(cursor))
    ]
    return json.dumps(
        {
            "_type": "crwd_user_proofs", "items": items, "count": len(items),
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
        _conn._db()[_COLL_PROOFS]
        .find(query, max_time_ms=_MAX_TIME_MS)
        .sort("created_at", -1)
        .limit(row_limit)
    )
    items = [
        _linkify_proof_payload(doc) for doc in _serialize_docs(list(cursor))
    ]
    return json.dumps(
        {
            "_type": "crwd_proof_lookup", "items": items,
            "count": len(items), "error": None,
        },
        ensure_ascii=False, default=str,
    )
