"""Dot transfers tool -- fetch a member's Dot transfers and transfer detail.

Dot is CRWD's payments partner. This tool talks ONLY to the Dot HTTP API -- it
holds no MongoDB/CRWD logic. Gig, membership, and approval lookups stay in the
``crwd_db`` tool; the ``crwd-payment-status`` skill is what combines the two.

Two actions, nothing more:
- ``get_user_transfers`` -- given the member's Dot ``user_id``, list their
  transfers (``GET /transfers?user_id=<id>``).
- ``get_transfer`` -- given a ``transfer_id`` (from a transfer in the list
  above), fetch that single transfer in full (``GET /transfers/<transfer_id>``).

Auth is HTTP Basic: ``Authorization: Basic base64(DOTS_CLIENT_ID:DOTS_API_KEY)``.
Gated on ``DOTS_CLIENT_ID`` + ``DOTS_API_KEY``; ``DOTS_BASE_URL`` defaults to the
Dot sandbox. Every failure (network, HTTP, bad JSON) is returned as
``{"error": ...}`` -- the tool never raises -- so the coach can fall back to
``crwd_db`` + an honest handoff.

BETA ASSUMPTION: CRWD and Dot don't yet expose a real id-mapping lookup, so
for now the member's CRWD ``user_id`` (from the ``[CRWD member]`` context
line) is passed straight through as the Dot ``user_id``. No separate Dot
id lookup or hardcoded test id -- just reuse the CRWD id as-is. Revisit once
CRWD/Dot ship a real cross-reference.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30
_DEFAULT_BASE_URL = "https://pls.senddotssandbox.com/api/v2"


# --- Availability ---

def check_dot_requirements() -> bool:
    """Available only when the Dot client id and API key are both configured."""
    return bool(
        os.getenv("DOTS_CLIENT_ID", "").strip()
        and os.getenv("DOTS_API_KEY", "").strip()
    )


# --- HTTP seam ---

def _base_url() -> str:
    """Dot API base URL (already includes ``/api/v2``). Defaults to the sandbox."""
    return (os.getenv("DOTS_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip("/")


def _auth_headers() -> Dict[str, str]:
    """HTTP Basic auth header: ``Basic base64(client_id:api_key)``."""
    client_id = os.getenv("DOTS_CLIENT_ID", "").strip()
    api_key = os.getenv("DOTS_API_KEY", "").strip()
    token = base64.b64encode(f"{client_id}:{api_key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _dot_get(path: str, params: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
    """GET ``{base}{path}?params`` from Dot. Returns ``(parsed_json, error)``.

    Never raises: transport/HTTP/JSON problems come back as the error string so
    callers can degrade gracefully.
    """
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    query = urllib.parse.urlencode(clean)
    url = f"{_base_url()}{path}" + (f"?{query}" if query else "")
    headers = {"Accept": "application/json", **_auth_headers()}
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            if not (200 <= resp.status < 300):
                return None, f"HTTP {resp.status}"
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # network / URL / timeout
        return None, str(exc)
    try:
        return json.loads(raw), None
    except Exception:
        return None, "invalid JSON from Dot"


# --- Actions ---

def _get_user_transfers(user_id: str) -> str:
    user_id = (user_id or "").strip()
    if not user_id:
        return tool_error("user_id is required for get_user_transfers")
    data, err = _dot_get("/transfers", {"user_id": user_id})
    if err:
        return tool_error(f"Dot lookup failed: {err}")
    return json.dumps(
        {"_type": "dot_user_transfers", "user_id": user_id, "data": data, "error": None},
        ensure_ascii=False,
    )


def _get_transfer(transfer_id: str) -> str:
    transfer_id = (transfer_id or "").strip()
    if not transfer_id:
        return tool_error("transfer_id is required for get_transfer")
    path = f"/transfers/{urllib.parse.quote(transfer_id, safe='')}"
    data, err = _dot_get(path, {})
    if err:
        return tool_error(f"Dot lookup failed: {err}")
    return json.dumps(
        {"_type": "dot_transfer", "transfer_id": transfer_id, "data": data, "error": None},
        ensure_ascii=False,
    )


# --- Handler ---

def dot_tool(args: Dict[str, Any], **_kw: Any) -> str:
    if not check_dot_requirements():
        return tool_error("Dot is not configured (set DOTS_CLIENT_ID and DOTS_API_KEY).")
    action = str(args.get("action", "")).strip()
    try:
        if action == "get_user_transfers":
            return _get_user_transfers(user_id=str(args.get("user_id", "")))
        if action == "get_transfer":
            return _get_transfer(transfer_id=str(args.get("transfer_id", "")))
        return tool_error("Unknown action. Use get_user_transfers or get_transfer.")
    except Exception:
        logger.exception("dot action %r failed", action)
        return tool_error("Dot query failed")


# --- Schema ---

DOT_SCHEMA = {
    "name": "dot",
    "description": (
        "Look up a CRWD member's Dot transfers (Dot is CRWD's payments partner). "
        "Read-only. Use for 'did I get paid?', 'where's my money?', 'when will I "
        "be paid?', or 'show my payment history'. Two actions: get_user_transfers "
        "(list a member's transfers by their Dot user_id) and get_transfer (full "
        "detail of one transfer by transfer_id, taken from a transfer in the list). "
        "Each transfer has a `status` (created, pending, failed, completed, "
        "reversed, canceled, flagged) and a `created` or acted something like that timestamp (ISO 8601) — these "
        "are the key fields for answering payment questions. "
        "Returns Dot's transfer records only — pair it with crwd_db for gig/approval "
        "context (the crwd-payment-status skill does this). Escalate genuine money "
        "disputes to a human."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get_user_transfers", "get_transfer"],
                "description": "get_user_transfers = list a member's transfers by user_id; get_transfer = full detail of one transfer by transfer_id.",
            },
            "user_id": {
                "type": "string",
                "description": (
                    "The member's user_id. Required for get_user_transfers. Beta "
                    "assumption: CRWD and Dot ids are treated as the same value for "
                    "now, so pass the member's CRWD user_id straight through -- "
                    "don't look up a separate Dot id."
                ),
            },
            "transfer_id": {
                "type": "string",
                "description": (
                    "The Dot transfer id (UUID, from a transfer returned by "
                    "get_user_transfers). Required for get_transfer. Use this to "
                    "drill into a specific transfer's status and created date."
                ),
            },
        },
        "required": ["action"],
    },
}


# --- Registration ---

registry.register(
    name="dot",
    toolset="dot",
    schema=DOT_SCHEMA,
    handler=dot_tool,
    check_fn=check_dot_requirements,
    requires_env=["DOTS_CLIENT_ID", "DOTS_API_KEY"],
    emoji="💰",
)
