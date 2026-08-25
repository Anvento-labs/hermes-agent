"""Action dispatch for the ``crwd_db`` LLM tool."""

from __future__ import annotations

import logging
from typing import Any, Dict

from tools.registry import tool_error

from tools.crwd_db.connection import _HARD_LIMIT, check_crwd_db_requirements
from tools.crwd_db.custom_query import _custom_query
from tools.crwd_db.gigs import _get_gig_details, _list_active_gigs
from tools.crwd_db.membership import (
    _get_user_gig_history,
    _get_user_gigs,
    _get_waitlisted_gigs,
)
from tools.crwd_db.products import (
    _get_user_notifications,
    _get_user_products,
    _get_user_receipts,
)
from tools.crwd_db.proofs import (
    _check_duplicate_proof,
    _check_gig_proof_completion,
    _find_proof,
    _get_user_proofs,
    _mark_proof_risk_scored,
    _store_proof,
    _user_has_completed_gig,
)
from tools.crwd_db.stage import _get_user_gig_status
from tools.crwd_db.users import _get_user

logger = logging.getLogger(__name__)

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
        if action == "get_user_proofs":
            return _get_user_proofs(
                user_id=args.get("user_id", ""),
                crwd_id=args.get("crwd_id", "") or args.get("gig_id", ""),
                status=args.get("status", ""),
                limit=args.get("limit", 10),
            )
        if action == "check_gig_proof_completion":
            return _check_gig_proof_completion(
                user_id=args.get("user_id", ""),
                crwd_id=args.get("crwd_id", "") or args.get("gig_id", ""),
            )
        if action == "user_has_completed_gig":
            return _user_has_completed_gig(user_id=args.get("user_id", ""))
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
            "store_proof, get_user_proofs, check_duplicate_proof, find_proof, "
            "check_gig_proof_completion, user_has_completed_gig, "
            "mark_proof_risk_scored, custom_query"
        )
    except RuntimeError as exc:
        # Config/connection problems -- safe to surface the short message.
        return tool_error(str(exc))
    except Exception:
        logger.exception("crwd_db action %r failed", action)
        return tool_error("query failed")
