"""CRWD risk-score tool -- adjust a Chatwoot contact's ``risk_score`` attribute.

Registers a single LLM-callable tool ``crwd_risk_score`` (gated on Chatwoot
creds) that reads the current member's ``risk_score`` custom attribute, adds a
signed ``delta``, clamps the result to ``0..100``, and writes it back. The
``crwd-proof-validator`` skill decides *how many points* a submission is worth
(duplicate receipt, wrong product, fake receipt, etc.) and calls this tool to
persist the score; this tool only owns the deterministic read-merge-write.

Self-contained by design, like ``crwd_handoff``: it resolves the current
Chatwoot account + contact from the gateway session context and calls the
Chatwoot Contacts API directly.

  - ``account_id``  from ``HERMES_SESSION_CHAT_ID`` (``account:conversation``),
    falling back to ``CHATWOOT_ACCOUNT_ID``.
  - ``contact_id``  from ``HERMES_SESSION_USER_ID`` -- for Chatwoot the session
    user id is the sender/contact id.

The Chatwoot Contacts API is **not** authorized for Agent Bots, so this uses the
agent/user token (``CHATWOOT_AGENT_TOKEN``, falling back to ``CHATWOOT_TOKEN``),
matching ``coach_context._chatwoot_creds``.

Chatwoot's contact ``PUT`` replaces the whole ``custom_attributes`` object, so
the update is a read-merge-write: existing attributes (``joincrwd_user_id``,
``crwd_synced_at``, ...) are preserved and only ``risk_score`` changes.

If anything goes wrong (no creds, no conversation, API failure) the tool
degrades gracefully -- it never raises -- so the skill can still reply to the
member and hand off.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from tools.registry import registry

logger = logging.getLogger(__name__)

_TIMEOUT_S = 8
_RISK_ATTR = "risk_score"
_MIN_SCORE = 0
_MAX_SCORE = 100


# --- Availability ---

def _agent_token() -> str:
    return (os.getenv("CHATWOOT_AGENT_TOKEN", "") or os.getenv("CHATWOOT_TOKEN", "")).strip()


def _base_url() -> str:
    return os.getenv("CHATWOOT_BASE_URL", "").strip().rstrip("/")


def check_crwd_risk_score_requirements() -> bool:
    """Available only in a Chatwoot deployment (base URL + a usable token)."""
    return bool(_base_url() and _agent_token())


# --- Contact resolution ---

def _resolve_contact() -> Tuple[Optional[str], Optional[str]]:
    """Return ``(account_id, contact_id)`` for the current Chatwoot session."""
    try:
        from gateway.session_context import get_session_env
    except Exception:  # pragma: no cover - gateway always present in prod
        return None, None

    platform = (get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower()
    if platform and platform != "chatwoot":
        return None, None

    default_account = os.getenv("CHATWOOT_ACCOUNT_ID", "").strip()
    chat_id = (get_session_env("HERMES_SESSION_CHAT_ID", "") or "").strip()
    if ":" in chat_id:
        account = chat_id.partition(":")[0].strip() or default_account
    else:
        account = default_account

    contact_id = (get_session_env("HERMES_SESSION_USER_ID", "") or "").strip()
    if not account or not contact_id:
        return None, None
    return account, contact_id


# --- Chatwoot contact read / write ---

def _contact_url(account_id: str, contact_id: str) -> str:
    return f"{_base_url()}/api/v1/accounts/{account_id}/contacts/{contact_id}"


def _get_contact(account_id: str, contact_id: str) -> Optional[Dict[str, Any]]:
    token = _agent_token()
    req = urllib.request.Request(
        _contact_url(account_id, contact_id),
        method="GET",
        headers={"api_access_token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            if not (200 <= resp.status < 300):
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
        logger.debug("[crwd_risk_score] get_contact %s failed: %s", contact_id, exc)
        return None
    if isinstance(data, dict):
        rec = data.get("payload", data)
        return rec if isinstance(rec, dict) else None
    return None


def _put_custom_attributes(
    account_id: str, contact_id: str, custom_attributes: Dict[str, Any]
) -> Tuple[bool, str]:
    token = _agent_token()
    body = json.dumps({"custom_attributes": custom_attributes}).encode("utf-8")
    req = urllib.request.Request(
        _contact_url(account_id, contact_id),
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json", "api_access_token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            if 200 <= resp.status < 300:
                return True, ""
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # network / URL / timeout
        return False, str(exc)


# --- Helpers ---

def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _clamp(value: int) -> int:
    return max(_MIN_SCORE, min(_MAX_SCORE, value))


def _fail(reason: str, *, error: Optional[str] = None) -> str:
    return json.dumps(
        {
            "_type": "crwd_risk_score",
            "updated": False,
            "previous": None,
            "new_score": None,
            "reason": reason,
            "error": error,
        },
        ensure_ascii=False,
    )


# --- Handler ---

def crwd_risk_score_tool(args: Dict[str, Any], **_kw: Any) -> str:
    if "delta" not in args or args.get("delta") is None:
        return _fail("No delta provided; nothing to update.")
    delta = _coerce_int(args.get("delta"))
    reason = str(args.get("reason", "")).strip()

    if not check_crwd_risk_score_requirements():
        return _fail("Chatwoot not configured; risk score not updated.")

    account_id, contact_id = _resolve_contact()
    if not account_id or not contact_id:
        return _fail("No current Chatwoot contact; risk score not updated.")

    contact = _get_contact(account_id, contact_id)
    if contact is None:
        return _fail("Could not read the contact; risk score not updated.")

    attrs = contact.get("custom_attributes")
    if not isinstance(attrs, dict):
        attrs = {}
    else:
        attrs = dict(attrs)

    previous = _clamp(_coerce_int(attrs.get(_RISK_ATTR), 0))
    new_score = _clamp(previous + delta)
    attrs[_RISK_ATTR] = new_score

    ok, err = _put_custom_attributes(account_id, contact_id, attrs)
    if not ok:
        logger.warning("[crwd_risk_score] update failed (%s)", err)
        return _fail("Could not write the risk score.", error=None)

    logger.info(
        "[crwd_risk_score] contact=%s %s -> %s (delta=%s, reason=%s)",
        contact_id, previous, new_score, delta, reason or "-",
    )
    return json.dumps(
        {
            "_type": "crwd_risk_score",
            "updated": True,
            "previous": previous,
            "new_score": new_score,
            "reason": reason or None,
            "error": None,
        },
        ensure_ascii=False,
    )


# --- Schema ---

CRWD_RISK_SCORE_SCHEMA = {
    "name": "crwd_risk_score",
    "description": (
        "Adjust the CURRENT CRWD member's risk score (a 0-100 Chatwoot contact "
        "attribute) by adding a signed point `delta`. Use when a proof/receipt "
        "submission matches a risk scenario (duplicate receipt, wrong product, "
        "wrong quantity, fake/edited receipt, repeated validation failures) — see "
        "the crwd-proof-validator skill for the point values. Reads the current "
        "score, adds `delta`, and clamps to 0-100 (it does NOT set an absolute "
        "value). Safe to call even outside Chatwoot: it no-ops and reports that "
        "nothing was updated. Do not mention the risk score to the member."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "delta": {
                "type": "integer",
                "description": (
                    "Points to add to the current risk score (may be negative). "
                    "Use 0 to flag/no-op without changing the score."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "Short scenario label for the audit log (e.g. 'duplicate receipt', "
                    "'fake receipt', 'wrong product')."
                ),
            },
        },
        "required": ["delta"],
    },
}


# --- Registration ---

registry.register(
    name="crwd_risk_score",
    toolset="crwd",
    schema=CRWD_RISK_SCORE_SCHEMA,
    handler=crwd_risk_score_tool,
    check_fn=check_crwd_risk_score_requirements,
    requires_env=["CHATWOOT_BASE_URL"],
    emoji="⚠️",
)
