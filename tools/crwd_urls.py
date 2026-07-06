"""CRWD member-app URL helpers (gig pages, etc.)."""

from __future__ import annotations

import os
import re
from typing import Any, Optional

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


def attach_gig_url(item: dict[str, Any]) -> dict[str, Any]:
    """Add ``gig_url`` and ``name_link`` when id, name, and base URL are available."""
    if not isinstance(item, dict):
        return item
    gig_id = item.get("_id") or item.get("gig_id") or item.get("crwd_id")
    name = item.get("name") or item.get("gig_name")
    url = build_gig_page_url(gig_id)
    if url:
        item["gig_url"] = url
        name_link = format_gig_name_link(name, gig_id)
        if name_link:
            item["name_link"] = name_link
    return item
