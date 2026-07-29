"""Dict wrappers used by app-chatbot CLI router and other hooks."""

from __future__ import annotations

import json
from typing import Any, Dict

from tools.crwd_db.connection import check_crwd_db_requirements
from tools.crwd_db.gigs import _get_gig_details, _list_active_gigs
from tools.crwd_db.membership import (
    _get_user_gig_history,
    _get_user_gigs,
    _get_waitlisted_gigs,
)
from tools.crwd_db.users import _get_user

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
            "postal_code": user.get("postal_code"),
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
