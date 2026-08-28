"""User lookup and lead-ingest upsert (not an LLM action)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from tools.registry import tool_error

from tools.crwd_db import connection as _conn
from tools.crwd_db.connection import _COLL_USERS, _MAX_TIME_MS, _USER_FIELDS, _oid
from tools.crwd_db.serialize import _normalize_dob, _serialize_doc

_COLL_ROLES = "roles"


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

    user = _conn._db()[_COLL_USERS].find_one(query, _USER_FIELDS, max_time_ms=_MAX_TIME_MS)
    return _user_payload(user, created=None)


def _user_payload(user: Optional[Dict[str, Any]], *, created: Optional[bool]) -> str:
    if user:
        dob = _normalize_dob(user.get("dob"))
        if dob:
            user["dob"] = dob
        else:
            user.pop("dob", None)
    body: Dict[str, Any] = {
        "_type": "user",
        "items": [_serialize_doc(user)] if user else [],
        "error": None,
    }
    if created is not None:
        body["created"] = created
    return json.dumps(body, ensure_ascii=False)


def _email_query(email: str) -> Dict[str, Any]:
    return {"email": re.compile(f"^{re.escape(email)}$", re.IGNORECASE)}


def _find_user(email: str, phone: str) -> Optional[Dict[str, Any]]:
    coll = _conn._db()[_COLL_USERS]
    if email:
        found = coll.find_one(_email_query(email), _USER_FIELDS, max_time_ms=_MAX_TIME_MS)
        if found:
            return found
    if phone:
        return coll.find_one({"phone": phone}, _USER_FIELDS, max_time_ms=_MAX_TIME_MS)
    return None


def _member_role_id() -> Any:
    doc = _conn._db()[_COLL_ROLES].find_one(
        {"role": "user"}, {"_id": 1}, max_time_ms=_MAX_TIME_MS
    )
    if not doc:
        return None
    return doc.get("_id")


def _minimal_user_doc(
    *,
    email: str,
    phone: str,
    first_name: str,
    last_name: str,
    full_name: str,
    role_id: Any,
) -> Dict[str, Any]:
    now = _conn._now()
    return {
        "email": email,
        "phone": phone,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        # Login-minimum fields (app User.create). Password left blank on purpose.
        "password": "",
        "role": role_id,
        "isEmailVerified": True,
        "blacklistToken": [],
        "register_type": "normal",
        "status": "Active",
        "isBlocked": False,
        "isDeleted": False,
        "isSignupCompleted": False,
        "web_signup": False,
        "sign_up_request_status": "Accept",
        "isMobileVerified": False,
        "identityStatus": "none",
        "bio": "",
        "address": "",
        "city": "",
        "state": "",
        "country": "",
        "postal_code": "",
        "gender": "",
        "dob": "",
        "emailOTP": "",
        "phoneOTP": "",
        "resetPassCode": "",
        "createdAt": now,
        "updatedAt": now,
    }


def _create_user(
    email: str = "",
    phone: str = "",
    first_name: str = "",
    last_name: str = "",
    full_name: str = "",
) -> str:
    """Find-or-insert a CRWD ``users`` row. Lead ingest only — not a tool action."""
    email = (email or "").strip()
    phone = (phone or "").strip()
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    full_name = (full_name or "").strip()
    if not email and not phone:
        return tool_error("email or phone is required for create_user")

    existing = _find_user(email, phone)
    if existing:
        return _user_payload(existing, created=False)

    role_id = _member_role_id()
    if role_id is None:
        return tool_error("could not resolve member role")

    if not full_name:
        full_name = " ".join(p for p in (first_name, last_name) if p).strip()

    doc = _minimal_user_doc(
        email=email,
        phone=phone,
        first_name=first_name,
        last_name=last_name,
        full_name=full_name,
        role_id=role_id,
    )
    result = _conn._db()[_COLL_USERS].insert_one(doc)
    created = _conn._db()[_COLL_USERS].find_one(
        {"_id": result.inserted_id}, _USER_FIELDS, max_time_ms=_MAX_TIME_MS
    )
    if created is None:
        created = {"_id": result.inserted_id, "email": email, "phone": phone}
    return _user_payload(created, created=True)
