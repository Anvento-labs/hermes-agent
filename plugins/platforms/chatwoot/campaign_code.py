"""Deterministic Chatwoot campaign-code lookup for short opaque tokens.

The model often replies to codes like FRGP / AAAB with zero tools
(``tool_turns=0``). This module runs ``lookup_campaign_code`` in
``pre_llm_call`` and injects the result so the coach can follow the
gig-discovery miss/hit copy without a tool round-trip.
"""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

CAMPAIGN_CODE_MARKER = (
    "[Campaign code lookup — the member sent a short code, not a question. "
    "Do not quote this block.]"
)

_MISS_REPLY = (
    "Looks like a campaign code — that one doesn’t match any active campaign. "
    "Want me to show gigs you can join?"
)

_CODE_RE = re.compile(r"^[A-Za-z0-9]{3,16}$")
_NOT_CODES = frozenset({
    "yes", "yeah", "yep", "yup", "ok", "okay", "no", "nah", "hi", "hey", "yo",
    "help", "sure", "thanks", "thx", "wait", "why", "hmm", "lol", "haha",
    "please", "pls", "stop", "new", "hiya", "sup",
})


def looks_like_campaign_code(text: str) -> bool:
    """True when the whole message is an opaque token, not chat/yes/no."""
    token = (text or "").strip()
    if not token or not _CODE_RE.fullmatch(token):
        return False
    return token.lower() not in _NOT_CODES


def _parse_lookup(raw: str) -> List[dict]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _product_lines(gig: dict) -> List[str]:
    lines: List[str] = []
    for store in gig.get("stores") or []:
        if not isinstance(store, dict):
            continue
        for product in store.get("products") or []:
            if not isinstance(product, dict):
                continue
            name = str(product.get("name") or "").strip()
            url = str(product.get("product_url") or "").strip()
            if name and url:
                lines.append(f"[{name}]({url})")
            elif name:
                lines.append(name)
    return lines


def _record_interest(member_id: str, gig_id: str) -> Optional[bool]:
    try:
        from tools.crwd_db.membership import _add_user_gig_interest

        raw = _add_user_gig_interest(user_id=member_id, crwd_id=gig_id)
        payload = json.loads(raw)
        if payload.get("error"):
            logger.warning("[chatwoot-campaign-code] interest error=%s", payload.get("error"))
            return None
        return bool(payload.get("created"))
    except Exception:
        logger.debug("[chatwoot-campaign-code] interest write failed", exc_info=True)
        return None


def campaign_code_context_lines(user_message: str, member_id: str) -> List[str]:
    """Lookup a short token and return context lines, or empty if not a code."""
    if not looks_like_campaign_code(user_message):
        return []
    token = user_message.strip()
    try:
        from tools.crwd_db.gigs import _lookup_campaign_code

        items = _parse_lookup(_lookup_campaign_code(query=token))
    except Exception:
        logger.debug("[chatwoot-campaign-code] lookup failed", exc_info=True)
        return []

    lines = [CAMPAIGN_CODE_MARKER]
    if len(items) == 0:
        lines.extend([
            "- Result: no matching active gig.",
            f"- Reply in this spirit (do not invent a gig): {_MISS_REPLY}",
            "- Do not call get_gig_details for this token. Do not treat it as a typo.",
        ])
        return lines

    if len(items) > 1:
        lines.append("- Result: more than one active gig matched. Ask which one before recording interest.")
        for gig in items[:5]:
            gid = str(gig.get("_id") or "")
            name = gig.get("name") or gid
            lines.append(f"- Candidate: {name} (_id {gid})")
        return lines

    gig = items[0]
    gig_id = str(gig.get("_id") or "").strip()
    created: Optional[bool] = None
    if member_id and gig_id:
        created = _record_interest(member_id, gig_id)
    name = gig.get("name") or gig_id
    payout = gig.get("effective_payout")
    end_date = gig.get("end_date")
    lines.extend([
        "- Result: one matching active gig. Interest is already recorded if the write succeeded.",
        f"- Gig _id: {gig_id}",
        f"- Linked title (paste verbatim): {name}",
        f"- Payout: {payout}",
        f"- Deadline: {end_date}",
        "- Confirm they are interested in this gig using the linked title, payout, and deadline.",
        "- Do not call get_gig_details for the token. Do not say they are accepted or applied.",
    ])
    if created is False:
        lines.append("- Membership row already existed (created false) — do not claim a new signup.")
    elif created is None and gig_id:
        lines.append(
            f"- Interest write did not confirm; call add_user_gig_interest with this member user_id and crwd_id {gig_id}."
        )
    products = _product_lines(gig)
    if products:
        lines.append("- Products (one markdown link per line):")
        lines.extend(f"  {p}" for p in products)
    return lines
