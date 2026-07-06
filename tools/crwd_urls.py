"""CRWD member-app URL helpers (gig pages, etc.)."""

from __future__ import annotations

import os
import re
from typing import Any, List, Optional

_OBJECTID_RE = re.compile(r"^[a-fA-F0-9]{24}$")


def crwd_app_base_url() -> str:
    """Member-facing CRWD web app base URL (no trailing slash)."""
    return os.getenv("CRWD_APP_BASE_URL", "").strip().rstrip("/")


def normalize_gig_id(value: Any) -> str:
    """Return a 24-char hex gig id string, or empty when not parseable."""
    if value is None:
        return ""
    if isinstance(value, dict):
        oid = value.get("$oid")
        if oid:
            return normalize_gig_id(oid)
        return ""
    raw = str(value).strip()
    if _OBJECTID_RE.fullmatch(raw):
        return raw
    match = re.search(r"\b[0-9a-fA-F]{24}\b", raw)
    return match.group(0) if match else ""


def build_gig_page_url(gig_id: Any) -> Optional[str]:
    """Build ``{CRWD_APP_BASE_URL}/my-gigs/{gig_id}`` when base URL is configured."""
    base = crwd_app_base_url()
    gid = normalize_gig_id(gig_id)
    if not base or not gid:
        return None
    return f"{base}/my-gigs/{gid}"


def format_gig_name_link(name: Any, gig_id: Any) -> Optional[str]:
    """Return markdown ``[name](gig_page_url)`` when base URL, id, and name are set."""
    label = str(name or "").strip()
    url = build_gig_page_url(gig_id)
    if not label or not url:
        return None
    return f"[{label}]({url})"


def name_aliases(name: str) -> List[str]:
    """Paraphrase variants for the same gig title (model often shortens or respaces)."""
    aliases: List[str] = []

    def _add(value: str) -> None:
        text = (value or "").strip()
        if text and text not in aliases:
            aliases.append(text)

    _add(name)
    _add(re.sub(r"\s*-\s*", " ", name))
    _add(re.sub(r"\s*-\s*", " - ", name.strip()))

    parts = re.split(r"\s*-\s*", name, maxsplit=1)
    if len(parts) > 1:
        prefix, suffix = parts[0].strip(), parts[1].strip()
        _add(prefix)
        if suffix:
            _add(f"{prefix} - {suffix}")
            _add(f"{prefix}- {suffix}")
            _add(f"{prefix}-{suffix}")

    return aliases


def format_gig_list_line(item: dict[str, Any], *, name_key: str = "name") -> Optional[str]:
    """One markdown bullet with linked ``name``/``gig_name`` from fetch-time data."""
    if not isinstance(item, dict):
        return None
    display = item.get(name_key) or item.get("name") or item.get("gig_name")
    if not display:
        return None
    payout = item.get("effective_payout")
    if payout in (None, "", 0, 0.0):
        payout = item.get("payout")
    line = f"- {display}"
    if payout not in (None, "", 0, 0.0):
        try:
            num = float(payout)
            line += f" — ${int(num) if num == int(num) else payout}"
        except (TypeError, ValueError):
            line += f" — {payout}"
    return line


def build_gig_list_markdown(items: List[Any], *, name_key: str = "name") -> str:
    """Ready-to-send markdown list built at DB fetch time (linked gig titles)."""
    lines: List[str] = []
    for item in items or []:
        row = format_gig_list_line(item, name_key=name_key)
        if row:
            lines.append(row)
    return "\n".join(lines)


def attach_gig_url(item: dict[str, Any], *, inline_name: bool = False) -> dict[str, Any]:
    """Add ``gig_url`` and optional name linking when id, name, and base URL are available.

    When ``inline_name`` is False (default), adds a separate ``name_link`` field.
    When True (crwd_db), replaces ``name``/``gig_name`` with markdown,
    preserves plain text in ``name_plain``/``gig_name_plain``, and rewrites
    ``next_step`` if it contains the plain title.
    """
    if not isinstance(item, dict):
        return item
    gig_id = item.get("_id") or item.get("gig_id") or item.get("crwd_id")
    if "gig_name" in item:
        display_key, plain_key = "gig_name", "gig_name_plain"
    else:
        display_key, plain_key = "name", "name_plain"
    plain = str(item.get(display_key) or item.get("name") or item.get("gig_name") or "").strip()
    url = build_gig_page_url(gig_id)
    if not url:
        return item

    item["gig_url"] = url
    name_link = format_gig_name_link(plain, gig_id)
    if not name_link:
        return item

    if inline_name:
        if plain:
            item[plain_key] = plain
            item[display_key] = name_link
        next_step = item.get("next_step")
        if plain and isinstance(next_step, str) and plain in next_step:
            item["next_step"] = next_step.replace(plain, name_link)
    else:
        item["name_link"] = name_link
    return item
