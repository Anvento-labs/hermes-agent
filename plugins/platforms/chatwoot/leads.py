"""CRWD leads ingress (``POST /leads``): upsert user, interest, Chatwoot thread, Coach turn.

Lovable (and similar) POST ``{gig_id, business_owner_id, user}``. After the
same ``?token=`` shared secret as the Chatwoot Agent Bot webhook, this path
calls ``_create_user`` then ``_add_user_gig_interest`` then
``ensure_conversation``. The adapter then schedules a Hermes Coach turn on
that conversation (outgoing only; see ``lead_turn.py``).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Mapping, Optional, Tuple

from plugins.platforms.chatwoot.conversations import ensure_conversation
from tools.crwd_db.connection import _oid
from tools.crwd_db.membership import _add_user_gig_interest
from tools.crwd_db.users import _create_user

try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dep
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_LEADS_PATH = "/leads"

# Keys whose values must never appear in logs (nested, case-insensitive).
_SECRET_KEY_RE = re.compile(
    r"password|token|secret|otp|authorization|api[_-]?key",
    re.IGNORECASE,
)
_LOG_VALUE_MAX = 500
_LOG_LIST_MAX = 20
_E164_RE = re.compile(r"^\+\d{8,15}$")
_DIGITS_RE = re.compile(r"\D")


def sanitize_for_log(value: Any) -> Any:
    """Return a JSON-serializable copy with secret-looking fields redacted."""
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_KEY_RE.search(name):
                out[name] = "<redacted>"
            else:
                out[name] = sanitize_for_log(item)
        return out
    if isinstance(value, list):
        clipped = value[:_LOG_LIST_MAX]
        return [sanitize_for_log(item) for item in clipped]
    if isinstance(value, str) and len(value) > _LOG_VALUE_MAX:
        return value[:_LOG_VALUE_MAX] + "…"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:_LOG_VALUE_MAX]


def _parse_payload(raw: bytes) -> Optional[Dict[str, Any]]:
    if not raw or not raw.strip():
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_phone(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if _E164_RE.match(raw):
        return raw
    digits = _DIGITS_RE.sub("", raw)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return raw


def _split_name(full: str) -> Tuple[str, str]:
    parts = full.split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _parse_lead(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return normalized lead fields, or None if the body is invalid."""
    gig_id = str(payload.get("gig_id") or "").strip()
    owner_raw = payload.get("business_owner_id")
    owner_id = str(owner_raw or "").strip() if owner_raw is not None else ""
    user = payload.get("user")
    if _oid(gig_id) is None or not isinstance(user, dict):
        return None
    if owner_id and _oid(owner_id) is None:
        return None
    email = _normalize_email(user.get("email"))
    phone = _normalize_phone(user.get("phone"))
    if not email and not phone:
        return None
    first_name = str(user.get("first_name") or "").strip()
    last_name = str(user.get("last_name") or "").strip()
    full_name = str(user.get("full_name") or user.get("name") or "").strip()
    if not first_name and not last_name and full_name:
        first_name, last_name = _split_name(full_name)
    if not full_name:
        full_name = " ".join(p for p in (first_name, last_name) if p).strip()
    return {
        "gig_id": gig_id,
        "business_owner_id": owner_id,
        "email": email,
        "phone": phone,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
    }


def _id_from_item(item: Mapping[str, Any]) -> str:
    raw = item.get("_id")
    if isinstance(raw, Mapping) and raw.get("$oid"):
        return str(raw["$oid"])
    return str(raw or "").strip()


async def handle_post(request: Any, *, max_body_bytes: int) -> Any:
    """Read JSON, upsert CRWD user + interest, ensure Chatwoot thread, return JSON."""
    if not AIOHTTP_AVAILABLE:
        raise RuntimeError("aiohttp is required for the leads endpoint")

    try:
        raw = await request.read()
    except Exception:
        return web.Response(status=413)
    if len(raw) > max_body_bytes:
        return web.Response(status=413)

    payload = _parse_payload(raw)
    if payload is None:
        return web.Response(status=400)

    safe = sanitize_for_log(payload)
    logger.info("[chatwoot-leads] received payload=%s", json.dumps(safe, ensure_ascii=False))

    lead = _parse_lead(payload)
    if lead is None:
        return web.Response(status=400)

    try:
        raw_result = _create_user(
            email=lead["email"],
            phone=lead["phone"],
            first_name=lead["first_name"],
            last_name=lead["last_name"],
            full_name=lead["full_name"],
        )
        result = json.loads(raw_result)
    except Exception:
        logger.exception("[chatwoot-leads] create_user failed")
        return web.json_response({"status": "error", "accepted": False}, status=500)

    if result.get("error"):
        logger.warning("[chatwoot-leads] create_user error=%s", result["error"])
        return web.json_response(
            {"status": "error", "accepted": False, "error": result["error"]},
            status=500,
        )

    items = result.get("items") or []
    user_id = _id_from_item(items[0]) if items else ""
    created = bool(result.get("created"))
    if not user_id:
        return web.json_response(
            {"status": "error", "accepted": False, "error": "user upsert returned no id"},
            status=500,
        )

    try:
        raw_interest = _add_user_gig_interest(
            user_id=user_id,
            crwd_id=lead["gig_id"],
            business_owner_id=lead["business_owner_id"],
        )
        interest = json.loads(raw_interest)
    except Exception:
        logger.exception("[chatwoot-leads] add_user_gig_interest failed")
        return web.json_response(
            {
                "status": "error",
                "accepted": False,
                "user_id": user_id,
                "error": "interest write failed",
            },
            status=500,
        )

    if interest.get("error"):
        logger.warning("[chatwoot-leads] add_user_gig_interest error=%s", interest["error"])
        return web.json_response(
            {
                "status": "error",
                "accepted": False,
                "user_id": user_id,
                "error": interest["error"],
            },
            status=500,
        )

    interest_items = interest.get("items") or []
    membership_id = _id_from_item(interest_items[0]) if interest_items else ""

    try:
        cw = ensure_conversation(
            email=lead["email"],
            phone=lead["phone"],
            name=lead["full_name"],
            crwd_user_id=user_id,
        )
    except Exception:
        logger.exception("[chatwoot-leads] ensure_conversation failed")
        return web.json_response(
            {
                "status": "error",
                "accepted": False,
                "user_id": user_id,
                "membership_id": membership_id,
                "error": "conversation ensure failed",
            },
            status=500,
        )

    if cw.get("error"):
        logger.warning("[chatwoot-leads] ensure_conversation error=%s", cw["error"])
        return web.json_response(
            {
                "status": "error",
                "accepted": False,
                "user_id": user_id,
                "membership_id": membership_id,
                "error": cw["error"],
            },
            status=500,
        )

    return web.json_response(
        {
            "status": "ok",
            "accepted": True,
            "gig_id": lead["gig_id"],
            "business_owner_id": lead["business_owner_id"],
            "user_id": user_id,
            "created": created,
            "interest_created": bool(interest.get("created")),
            "membership_id": membership_id,
            "account_id": cw.get("account_id"),
            "conversation_id": cw.get("conversation_id"),
            "chat_id": cw.get("chat_id"),
            "inbox_id": cw.get("inbox_id"),
            "channel_type": cw.get("channel_type"),
            "inbox_name": cw.get("inbox_name"),
            "contact_id": cw.get("contact_id"),
            "conversation_created": bool(cw.get("created")),
            "conversation_status": cw.get("conversation_status") or "pending",
            "coach_turn_started": False,
        }
    )
