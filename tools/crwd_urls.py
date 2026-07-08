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
    if _OBJECTID_RE.match(raw):
        return raw
    return ""


def gig_page_url(gig_id: Any, *, base_url: Optional[str] = None) -> Optional[str]:
    """Build ``{base}/my-gigs/{gig_id}``, or None when base/id are missing."""
    base = (base_url if base_url is not None else crwd_app_base_url()).strip().rstrip("/")
    gid = normalize_gig_id(gig_id)
    if not base or not gid:
        return None
    return f"{base}/my-gigs/{gid}"


def format_gig_name_link(name: str, gig_id: Any, *, base_url: Optional[str] = None) -> Optional[str]:
    """Return markdown ``[name](url)`` for a gig, or None when inputs are incomplete."""
    title = (name or "").strip()
    url = gig_page_url(gig_id, base_url=base_url)
    if not title or not url:
        return None
    return f"[{title}]({url})"


def _plain_title_from_display(value: str, url: str) -> str:
    """Recover a human title from a bare URL or markdown ``[title](url)``."""
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("[") and "](" in text:
        close = text.find("](")
        if close > 1:
            return text[1:close]
    if text == url or (url and text == url):
        return ""
    return text


def attach_gig_url(item: dict, *, inline_name: bool = True) -> dict:
    """Attach ``gig_url`` and optionally inline markdown into name fields.

    When ``inline_name`` is True and a URL can be built:
    - ``name`` / ``gig_name`` become ``[plain](gig_url)`` so the title is
      the clickable link (no trailing bare URL in replies)
    - originals are preserved as ``name_plain`` / ``gig_name_plain``
    - ``next_step`` occurrences of the plain title are rewritten to the
      markdown form

    When ``CRWD_APP_BASE_URL`` is unset (or id/name missing), the item is
    returned unchanged aside from an optional no-op.
    """
    if not isinstance(item, dict):
        return item

    gig_id = item.get("_id") or item.get("gig_id")
    url = gig_page_url(gig_id)
    if not url:
        return item

    item["gig_url"] = url

    if not inline_name:
        return item

    plain_title = ""
    for display_key, plain_key in (
        ("name", "name_plain"),
        ("gig_name", "gig_name_plain"),
    ):
        if display_key not in item:
            continue
        raw = item.get(display_key)
        if raw is None:
            continue
        plain = str(raw).strip()
        if not plain:
            continue

        # Already correct markdown for this gig.
        if plain.startswith("[") and f"]({url})" in plain:
            if plain_key not in item:
                recovered = _plain_title_from_display(plain, url)
                if recovered:
                    item[plain_key] = recovered
            plain_title = plain_title or item.get(plain_key) or ""
            continue

        # Bare URL from a previous attach — keep existing plain if present.
        if plain == url:
            recovered = str(item.get(plain_key) or "").strip()
            if recovered:
                item[display_key] = f"[{recovered}]({url})"
                plain_title = plain_title or recovered
            continue

        plain_title = plain_title or plain
        item[plain_key] = plain
        item[display_key] = f"[{plain}]({url})"

    if plain_title:
        next_step = item.get("next_step")
        if isinstance(next_step, str) and plain_title in next_step:
            linked = f"[{plain_title}]({url})"
            if linked not in next_step:
                # Also collapse older "Title (url)" forms.
                next_step = next_step.replace(f"{plain_title} ({url})", linked)
                item["next_step"] = next_step.replace(plain_title, linked)

    return item
