"""Shared helpers for the app-chatbot CLI prefetch plugin."""

from __future__ import annotations

import os
import re
from typing import Any

_OBJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


def default_user_id() -> str:
    return (
        str(os.getenv("CRWD_DEFAULT_USER_ID", "") or "").strip()
        or str(os.getenv("APP_CHATBOT_DEFAULT_USER_ID", "") or "").strip()
    )


def parse_object_id(value: str) -> Any:
    from tools.crwd_db_tool import _oid

    raw = (value or "").strip()
    if not _OBJECT_ID_RE.fullmatch(raw):
        raise ValueError(f"Invalid user_id: expected 24-char hex ObjectId, got {value!r}")
    oid = _oid(raw)
    if oid is None:
        raise ValueError(f"Invalid ObjectId: {value!r}")
    return oid
