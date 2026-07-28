"""User lookup action."""

from __future__ import annotations

import json
from typing import Any, Dict

from tools.registry import tool_error

from tools.crwd_db import connection as _conn
from tools.crwd_db.connection import _COLL_USERS, _MAX_TIME_MS, _USER_FIELDS, _oid
from tools.crwd_db.serialize import _serialize_doc

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
    return json.dumps(
        {"_type": "user", "items": [_serialize_doc(user)] if user else [], "error": None},
        ensure_ascii=False,
    )
