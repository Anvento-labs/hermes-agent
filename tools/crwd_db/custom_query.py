"""Guarded custom find/count escape hatch."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from tools.registry import tool_error

from tools.crwd_db import connection as _conn
from tools.crwd_db.connection import (
    _ALLOWED_COLLECTIONS,
    _HARD_LIMIT,
    _MAX_TIME_MS,
    _USER_SECRET_RE,
)
from tools.crwd_db.serialize import _serialize_docs

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

    coll = _conn._db()[collection]
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
