"""Dot transfers tool -- fetch a member's Dot transfers and transfer detail.

Dot is CRWD's payments partner. This tool talks ONLY to the Dot HTTP API -- it
holds no MongoDB/CRWD logic. Gig, membership, and approval lookups stay in the
``crwd_db`` tool; the ``crwd-payment-status`` skill is what combines the two.

Three actions, nothing more:
- ``create_user`` -- create the member in Dot (``POST /users``) and return the
  Dot ``user_id`` to feed into ``get_user_transfers``.
- ``get_user_transfers`` -- given the member's Dot ``user_id``, list their
  transfers (``GET /transfers?user_id=<id>``).
- ``get_transfer`` -- given a ``transfer_id`` (from a transfer in the list
  above), fetch that single transfer in full (``GET /transfers/<transfer_id>``).

Auth is HTTP Basic: ``Authorization: Basic base64(DOTS_CLIENT_ID:DOTS_API_KEY)``.
Gated on ``DOTS_CLIENT_ID`` + ``DOTS_API_KEY``; ``DOTS_BASE_URL`` defaults to the
Dot sandbox. Every failure (network, HTTP, bad JSON) is returned as
``{"error": ...}`` -- the tool never raises -- so the coach can fall back to
``crwd_db`` + an honest handoff.

DUPLICATE-USER RISK (read before touching ``create_user``): Dot does NOT
document create-as-upsert -- ``POST /users`` is a plain create, and the
white-labeled payouts guide describes no lookup-by-email/phone endpoint at all.
Dot's own environments doc states the sandbox "allows you to use duplicate
phone numbers, unlike the production environment", so against the sandbox
default below repeated creates DO mint duplicate payees; in production a
duplicate phone is rejected (an error, not the existing user). Two guards
follow from that:

1. ``idempotency_key`` is derived deterministically from the member's
   email/phone, so repeated creates replay the first response instead of
   minting a second user -- but Dot expires idempotency keys after 24h, so
   this only collapses same-day repeats. It is a damper, not a fix.
2. A freshly created user has no transfers and is ``unverified`` (Dot won't
   attach payout methods until phone verification). ``get_user_transfers``
   therefore reports ``user_is_new`` so the coach never reads "empty transfer
   list" as "you were never paid" -- that would be a false answer about money.

The durable fix is for the CRWD backend to create the Dot user at onboarding /
first payout and store the resulting Dot ``user_id`` on the member record, so
this tool only ever reads. Revisit once that mapping exists.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, Optional, Tuple

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30
_DEFAULT_BASE_URL = "https://pls.senddotssandbox.com/api/v2"


def _digits(s: str) -> str:
    """Digits only -- Dot wants bare national numbers and country codes."""
    return re.sub(r"\D", "", s or "")


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


def _dot_post(path: str, body: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
    """POST JSON to ``{base}{path}``. Returns ``(parsed_json, error)``; never raises."""
    url = f"{_base_url()}{path}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **_auth_headers(),
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            if not (200 <= resp.status < 300):
                return None, f"HTTP {resp.status}"
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        # A duplicate phone/email in production surfaces here as a 4xx. Keep the
        # body: it's the only signal telling the coach the member already exists.
        return None, f"HTTP {exc.code}" + (f": {detail}" if detail else "")
    except Exception as exc:  # network / URL / timeout
        return None, str(exc)
    try:
        return json.loads(raw), None
    except Exception:
        return None, "invalid JSON from Dot"


def _idempotency_key(email: str, phone_number: str, country_code: str) -> str:
    """Stable per-member key so same-day repeat creates replay one response.

    Dot expires idempotency keys after 24h, so this collapses only same-day
    repeats -- see the DUPLICATE-USER RISK note in the module docstring.
    """
    seed = f"{email.strip().lower()}|{_digits(country_code)}{_digits(phone_number)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"crwd-dot-user:{seed}"))


# --- Actions ---

def _create_user(
    first_name: str,
    last_name: str,
    email: str,
    phone_number: str,
    country_code: str,
) -> str:
    email = (email or "").strip()
    phone_number = (phone_number or "").strip()
    country_code = (country_code or "").strip() or "1"
    if not email and not phone_number:
        return tool_error("create_user needs the member's email and/or phone_number.")

    body: Dict[str, Any] = {
        "first_name": (first_name or "").strip(),
        "last_name": (last_name or "").strip(),
        "email": email,
        "phone_number": _digits(phone_number),
        "country_code": _digits(country_code),
        "idempotency_key": _idempotency_key(email, phone_number, country_code),
    }
    body = {k: v for k, v in body.items() if v not in (None, "")}

    data, err = _dot_post("/users", body)
    if err:
        # Do NOT paper over this: in production a duplicate phone is rejected
        # here, and the member very likely already has a Dot account.
        return tool_error(
            f"Dot create_user failed: {err}. If this says the user already "
            "exists, the member has a Dot account this tool cannot look up -- "
            "hand off to a human rather than guessing about their money."
        )

    user_id = ""
    if isinstance(data, dict):
        user_id = str(data.get("id") or data.get("user_id") or "").strip()
    if not user_id:
        return tool_error("Dot create_user returned no user id.")

    return json.dumps(
        {
            "_type": "dot_user_created",
            "user_id": user_id,
            "user_is_new": True,
            "note": (
                "This user was just created in Dot, so it is unverified and has "
                "no transfer history yet. An empty transfer list for this id does "
                "NOT mean the member was never paid -- it means Dot has no record "
                "under this brand-new id. Do not tell the member they weren't paid."
            ),
            "data": data,
            "error": None,
        },
        ensure_ascii=False,
    )


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
        if action == "create_user":
            return _create_user(
                first_name=str(args.get("first_name", "")),
                last_name=str(args.get("last_name", "")),
                email=str(args.get("email", "")),
                phone_number=str(args.get("phone_number", "")),
                country_code=str(args.get("country_code", "")),
            )
        if action == "get_user_transfers":
            return _get_user_transfers(user_id=str(args.get("user_id", "")))
        if action == "get_transfer":
            return _get_transfer(transfer_id=str(args.get("transfer_id", "")))
        return tool_error(
            "Unknown action. Use create_user, get_user_transfers or get_transfer."
        )
    except Exception:
        logger.exception("dot action %r failed", action)
        return tool_error("Dot query failed")


# --- Schema ---

DOT_SCHEMA = {
    "name": "dot",
    "description": (
        "Look up a CRWD member's Dot transfers (Dot is CRWD's payments partner). "
        "Use for 'did I get paid?', 'where's my money?', 'when will I be paid?', or "
        "'show my payment history'. Three actions: create_user (create the member in "
        "Dot and get back a Dot user_id), get_user_transfers (list a member's "
        "transfers by their Dot user_id) and get_transfer (full detail of one "
        "transfer by transfer_id, taken from a transfer in the list). "
        "Each transfer has a `status` (created, pending, failed, completed, "
        "reversed, canceled, flagged) and a `created` timestamp (ISO 8601) — these "
        "are the key fields for answering payment questions. "
        "IMPORTANT: create_user WRITES to a live payments system, so only call it "
        "when you have no Dot user_id for the member. If you had to create the user, "
        "their transfer list will come back empty because the id is brand new — that "
        "means Dot has no history under this id, NOT that the member was never paid. "
        "Never tell a member they weren't paid on the strength of an empty list from "
        "a just-created user; hand off to a human instead. "
        "Returns Dot's transfer records only — pair it with crwd_db for gig/approval "
        "context (the crwd-payment-status skill does this). Escalate genuine money "
        "disputes to a human."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create_user", "get_user_transfers", "get_transfer"],
                "description": "create_user = create the member in Dot, returns a Dot user_id (a WRITE — only when you have no id); get_user_transfers = list a member's transfers by user_id; get_transfer = full detail of one transfer by transfer_id.",
            },
            "user_id": {
                "type": "string",
                "description": (
                    "The member's Dot user_id. Required for get_user_transfers. Use "
                    "the user_id returned by create_user."
                ),
            },
            "first_name": {
                "type": "string",
                "description": "Member's first name (create_user). From crwd_db get_user — don't invent it.",
            },
            "last_name": {
                "type": "string",
                "description": "Member's last name (create_user). From crwd_db get_user — don't invent it.",
            },
            "email": {
                "type": "string",
                "description": (
                    "Member's email (create_user). Must come from their CRWD record "
                    "via crwd_db get_user — never from a guess, or you create a junk "
                    "payee record. Email and/or phone_number is required."
                ),
            },
            "phone_number": {
                "type": "string",
                "description": (
                    "Member's phone number (create_user), digits or any format. Must "
                    "come from their CRWD record via crwd_db get_user. Email and/or "
                    "phone_number is required."
                ),
            },
            "country_code": {
                "type": "string",
                "description": "Phone country code for create_user, digits only (default '1').",
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
