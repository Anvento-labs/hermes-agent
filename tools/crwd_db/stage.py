"""Gig stage / next_step computation and get_user_gig_status."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from tools.crwd_urls import attach_gig_url
from tools.registry import tool_error

from tools.crwd_db import connection as _conn
from tools.crwd_db.connection import (
    _COLL_CRWDS,
    _COLL_MEMBERS,
    _COLL_PROOFS,
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
    _all_member_filter,
    _gig_type_key,
    _sort_members_by_gig_end_date,
)
from tools.crwd_db.proofs import _RECEIPT_TYPES, _gig_proof_completion
from tools.crwd_db.serialize import _serialize_doc

_REVIEW_TYPES = frozenset({"review_screenshot", "ugc_link"})
_REVIEW_REQUIREMENT_FLAGS = frozenset({
    "requires_review_receipt", "requires_review_link", "requires_ugc_post",
})


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


def _chat_proof_progress(chat_proofs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize Hermes ``proof_submissions`` for stage. Any accept wins over older rows."""
    receipt_accepted = False
    receipt_needs_human = False
    receipt_rejected = False
    review_accepted = False
    review_needs_human = False
    review_rejected = False
    gig_completed_flag = False

    for row in chat_proofs or []:
        if row.get("is_gig_completed"):
            gig_completed_flag = True
        ptype = str(row.get("proof_type") or "").strip().lower()
        status = str(row.get("status") or "").strip().lower()
        if ptype in _RECEIPT_TYPES:
            if status == "accepted":
                receipt_accepted = True
            elif status == "needs_human":
                receipt_needs_human = True
            elif status == "rejected":
                receipt_rejected = True
        elif ptype in _REVIEW_TYPES:
            if status == "accepted":
                review_accepted = True
            elif status == "needs_human":
                review_needs_human = True
            elif status == "rejected":
                review_rejected = True

    return {
        "receipt_submitted": receipt_accepted or receipt_needs_human or receipt_rejected,
        "receipt_accepted": receipt_accepted,
        "receipt_needs_human": receipt_needs_human and not receipt_accepted,
        "receipt_rejected": (
            receipt_rejected and not receipt_accepted and not receipt_needs_human
        ),
        "review_submitted": review_accepted or review_needs_human or review_rejected,
        "review_accepted": review_accepted,
        "review_needs_human": review_needs_human and not review_accepted,
        "review_rejected": (
            review_rejected and not review_accepted and not review_needs_human
        ),
        "gig_completed_flag": gig_completed_flag,
    }


def _payout_stage(
    *,
    gig_name: str,
    is_accepted: Any,
    has_paid: Any,
    progress: Dict[str, Any],
    buy_link: Optional[str],
) -> Dict[str, Any]:
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
    # Same sentence either way, on purpose. The member's situation is identical --
    # every proof in, approved, money pending -- and that CRWD hasn't stamped
    # acceptance yet is internal. Telling someone who already bought the product
    # and sent proof that they were never accepted reads as a problem, invites a
    # question no skill can answer, and contradicts what lead intro told them.
    next_step = (
        f"All proof for {gig_name} is approved — payout typically lands in "
        "1–2 business days via Dot."
    )
    if is_accepted is False:
        return {
            "stage": "proof_complete_pending_acceptance",
            "next_step": next_step,
            "progress": progress,
            "buy_link": buy_link,
            "handoff_recommended": False,
        }
    return {
        "stage": "awaiting_payout",
        "next_step": next_step,
        "progress": progress,
        "buy_link": buy_link,
        "handoff_recommended": False,
    }


def compute_gig_stage(
    membership: Dict[str, Any],
    gig: Dict[str, Any],
    *,
    purchases: List[Dict[str, Any]],
    chat_proofs: Optional[List[Dict[str, Any]]] = None,
    proof_completion: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Derive machine-readable stage + coach-facing next_step for one membership.

    Receipt / review progress comes only from Hermes ``proof_submissions``
    (``chat_proofs``), not from app receipt tables. Optional ``proof_completion``
    is the result of ``_gig_proof_completion`` (pass it from the builder, or
    omit in unit tests and rely on type-level heuristics + ``is_gig_completed``).
    """
    gig_name = str(gig.get("name") or "this gig").strip()
    gig_type = _gig_type_key(gig)
    products = _collect_buy_products(gig, purchases)
    buy_link = products[0]["product_url"] if products else None

    is_accepted = membership.get("isAccepted")
    has_paid = membership.get("hasPaid")
    rejection = membership.get("rejectionReason") or membership.get("rejectionNotes")

    chat = _chat_proof_progress(chat_proofs or [])
    progress: Dict[str, Any] = {
        # NOT a purchase confirmation. A user_product_purchases row is written when
        # the member is *accepted to join* (every row in the data is
        # source: "join_approved", and purchasedAt is just createdAt on most of
        # them) -- it records which product they may buy and the buy link, not that
        # they bought anything. Naming it purchase_confirmed made the coach tell a
        # member "the system registered that you ordered the product" when the
        # system had registered no such thing.
        "product_assigned": bool(purchases),
        "receipt_submitted": chat["receipt_submitted"],
        "receipt_accepted": chat["receipt_accepted"],
        "review_submitted": chat["review_submitted"],
        "review_accepted": chat["review_accepted"],
    }

    if rejection:
        return {
            "stage": "rejected",
            "next_step": (
                f"Your enrollment for {gig_name} was not accepted. "
                "I'll loop in a human who can help."
            ),
            "progress": progress,
            "buy_link": buy_link,
            "handoff_recommended": True,
        }

    # No acceptance gate. isAccepted only decides the terminal stage below (and
    # capacity/app-tab bucketing elsewhere) -- a member marked Interested buys and
    # submits proof on the same path as an accepted one.
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

    # Fast path: a store_proof row already marked this gig complete.
    if chat["gig_completed_flag"] or (
        proof_completion
        and proof_completion.get("determinable")
        and proof_completion.get("complete")
    ):
        progress["receipt_submitted"] = True
        progress["receipt_accepted"] = True
        progress["review_submitted"] = True
        progress["review_accepted"] = True
        return _payout_stage(
            gig_name=gig_name, is_accepted=is_accepted, has_paid=has_paid,
            progress=progress, buy_link=buy_link,
        )

    if not chat["receipt_accepted"]:
        if chat["receipt_needs_human"]:
            return {
                "stage": "receipt_review",
                "next_step": (
                    f"Your receipt for {gig_name} is being reviewed — "
                    "we'll notify you when it's accepted."
                ),
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": False,
            }
        if chat["receipt_rejected"]:
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
        if gig_type == "irl":
            next_step = (
                f"For {gig_name}, visit the store, buy the product, then send "
                "your receipt right here in the chat."
            )
        else:
            next_step = (
                f"For {gig_name}, order the product, then send your order "
                "receipt screenshot right here in the chat."
            )
        return {
            "stage": "need_receipt",
            "next_step": next_step,
            "progress": progress,
            "buy_link": buy_link,
            "handoff_recommended": False,
        }

    # Receipt accepted in chat. Use requirement completion when available.
    if proof_completion and proof_completion.get("determinable"):
        outstanding = list(proof_completion.get("outstanding") or [])
        if not outstanding:
            progress["review_submitted"] = True
            progress["review_accepted"] = True
            return _payout_stage(
                gig_name=gig_name, is_accepted=is_accepted, has_paid=has_paid,
                progress=progress, buy_link=buy_link,
            )
        needs_review_artifact = any(f in _REVIEW_REQUIREMENT_FLAGS for f in outstanding)
        if needs_review_artifact:
            if chat["review_needs_human"]:
                return {
                    "stage": "review_review",
                    "next_step": (
                        f"Your review for {gig_name} is under review — "
                        "we'll notify you when it's accepted."
                    ),
                    "progress": progress,
                    "buy_link": buy_link,
                    "handoff_recommended": False,
                }
            if chat["review_rejected"]:
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
            if gig_type == "irl":
                next_step = (
                    f"Receipt accepted for {gig_name}! Next: post your review, then "
                    "send it here in the chat."
                )
            else:
                next_step = (
                    f"Order accepted for {gig_name}! Leave your review, then send "
                    "the review screenshot here in the chat."
                )
            return {
                "stage": "need_review",
                "next_step": next_step,
                "progress": progress,
                "buy_link": buy_link,
                "handoff_recommended": False,
            }
        # Outstanding is receipt-shaped (or unknown) despite an accepted receipt type —
        # coach for another receipt artifact rather than inventing payout.
        return {
            "stage": "need_receipt",
            "next_step": (
                f"For {gig_name}, send your remaining receipt proof right here "
                "in the chat."
            ),
            "progress": progress,
            "buy_link": buy_link,
            "handoff_recommended": False,
        }

    # No completion payload (unit tests / undetermined requirements): type heuristics.
    if chat["review_accepted"]:
        return _payout_stage(
            gig_name=gig_name, is_accepted=is_accepted, has_paid=has_paid,
            progress=progress, buy_link=buy_link,
        )
    if chat["review_needs_human"]:
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
    if chat["review_rejected"]:
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
    if gig_type == "irl":
        next_step = (
            f"Receipt accepted for {gig_name}! Next: post your review, then "
            "send it here in the chat."
        )
    else:
        next_step = (
            f"Order accepted for {gig_name}! Leave your review, then send "
            "the review screenshot here in the chat."
        )
    return {
        "stage": "need_review",
        "next_step": next_step,
        "progress": progress,
        "buy_link": buy_link,
        "handoff_recommended": False,
    }


def _progress_for_crwd(
    user_id: str,
    crwd_id: Any,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch purchase + Hermes proof rows for one gig."""
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
    # proof_submissions keys user_id / crwd_id as strings (store_proof path).
    crwd_id_strs = list({str(v) for v in crwd_values if v is not None})
    chat_proofs = list(
        db[_COLL_PROOFS]
        .find(
            {
                "user_id": str(user_id).strip(),
                "crwd_id": {"$in": crwd_id_strs},
            },
            {
                "proof_type": 1, "status": 1, "is_gig_completed": 1,
                "created_at": 1,
            },
            max_time_ms=_MAX_TIME_MS,
        )
        .sort("created_at", -1)
        .limit(50)
    )
    return {
        "purchases": purchases,
        "chat_proofs": chat_proofs,
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
    include_waitlisted: bool = False,  # inert; kept so existing callers don't break
    limit: int = _HARD_LIMIT,
) -> Dict[str, Any]:
    """Build gig status payload (dict) for one member — used by tool + prefetch hook."""
    user_id = (user_id or "").strip()
    if not user_id:
        return {"_type": "user_gig_status", "items": [], "error": "user_id is required"}

    row_limit = max(1, min(int(limit or _HARD_LIMIT), _HARD_LIMIT))
    db = _conn._db()

    # Every non-deleted row, accepted or not -- acceptance no longer gates
    # progression, so compute_gig_stage decides how to coach each one.
    # include_waitlisted is inert now: what it used to union in is already here.
    members = list(
        db[_COLL_MEMBERS]
        .find(_all_member_filter(user_id), _MEMBER_FIELDS, max_time_ms=_MAX_TIME_MS)
    )

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
        completion = _gig_proof_completion(user_id, str(gid))
        stage_info = compute_gig_stage(
            m, gig,
            purchases=prog["purchases"],
            chat_proofs=prog["chat_proofs"],
            proof_completion=completion,
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
