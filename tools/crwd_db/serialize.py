"""BSON → JSON-safe serialization helpers."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any, List, Optional

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})$")


def _valid_ymd(year: int, month: int, day: int) -> Optional[str]:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _normalize_dob(value: Any) -> Optional[str]:
    """Return ``YYYY-MM-DD`` or ``None`` if ``value`` is missing/unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict) and "$date" in value:
        inner = value["$date"]
        if isinstance(inner, dict) and "$numberLong" in inner:
            try:
                ms = int(inner["$numberLong"])
                return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                return None
        return _normalize_dob(inner)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Epoch milliseconds (13 digits) or seconds (10 digits).
        try:
            ts = float(value)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    iso = _ISO_DATE_RE.match(text[:10]) if len(text) >= 10 and text[4] == "-" else _ISO_DATE_RE.match(text)
    if iso:
        return _valid_ymd(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    # ISO datetime prefix: 1998-12-20T00:00:00Z
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return _valid_ymd(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    slash = _SLASH_DATE_RE.match(text)
    if slash:
        a, b, c = slash.group(1), slash.group(2), slash.group(3)
        if len(a) == 4:
            return _valid_ymd(int(a), int(b), int(c))
        year = int(c)
        if year < 100:
            year += 2000
        return _valid_ymd(year, int(a), int(b))
    return None


def _age_from_dob(dob: str, today: Optional[date] = None) -> Optional[int]:
    """Integer age from a normalized ``YYYY-MM-DD`` string, or ``None``."""
    parsed = _normalize_dob(dob)
    if not parsed:
        return None
    born = date.fromisoformat(parsed)
    now = today or date.today()
    age = now.year - born.year - ((now.month, now.day) < (born.month, born.day))
    if age < 0 or age > 130:
        return None
    return age


def _serialize_doc(doc: Any) -> Any:
    from bson import json_util

    return json.loads(json_util.dumps(doc))


def _serialize_docs(docs: List[Any]) -> List[Any]:
    return [_serialize_doc(doc) for doc in docs]
