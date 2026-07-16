"""CRWD Coach context: surface the member's CRWD ``users._id`` to the agent.

On each turn, inject a short context line naming the current Chatwoot member's
CRWD user id so the coach can call ``crwd_db`` ``get_user_gigs`` /
``get_user_receipts`` / ``get_user_products`` **directly** — no ``get_user``
round-trip, and no reliance on the member's email/phone reaching the prompt.

Resolution is **synchronous and self-contained** (``pre_llm_call`` hooks run
sync, like app-chatbot's ``_prefetch_context``), mirroring the ``crwd_handoff``
tool's direct-Chatwoot-API style:

  1. ``contact_id`` = the Chatwoot sender id (from the hook kwargs); account id
     from ``HERMES_SESSION_CHAT_ID`` (``account:conversation``).
  2. ``GET /accounts/{acct}/contacts/{contact_id}`` → ``custom_attributes.
     joincrwd_user_id`` (written by the enrichment pipeline).
  3. Fallback: resolve from CRWD Mongo by the contact's email/phone via
     ``enrichment.fetch_user`` — enrichment is fire-and-forget, so the attribute
     may not be populated on the very first message.
  4. Cache the result per contact id (short TTL) to keep it to one lookup.

Best-effort throughout: any failure returns ``None`` and the coach falls back to
today's ``get_user`` path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Optional CRWD user id from the inbound webhook sender.custom_attributes
# (set per-turn by the adapter before the agent runs).
_webhook_crwd_hint: ContextVar[Optional[str]] = ContextVar(
    "chatwoot_webhook_crwd_hint", default=None
)

# Set per turn when the inbound message asks about another member's account.
_cross_user_request: ContextVar[bool] = ContextVar(
    "chatwoot_cross_user_request", default=False
)

_OBJECT_ID_IN_MSG_RE = re.compile(r"\b[0-9a-fA-F]{24}\b")
_USER_MEMBER_OID_RE = re.compile(
    r"\b(?:user|member)\s+([0-9a-fA-F]{24})\b",
    re.IGNORECASE,
)
_ANOTHER_PERSON_RE = re.compile(
    r"\banother\s+(?:user|member|person)\b",
    re.IGNORECASE,
)
# Privacy-sensitive asks about a person (not "tell me about gig <id>").
_PRIVACY_ASK_RE = re.compile(
    r"\b(?:name|account|profile|email|phone|receipts?|payout|membership|"
    r"enrolled|part\s+of|"
    r"gigs?\s+(?:of|for|has|are|is)|"
    r"(?:of|for)\s+(?:the\s+)?(?:user|member)|"
    r"what\s+(?:is|are)\s+(?:the\s+)?name\b|"
    r"whose\s+(?:account|gigs?|name|profile))\b",
    re.IGNORECASE,
)
# Gig/campaign entity lookups — ObjectId is a gig, not a member.
_GIG_ENTITY_OID_RE = re.compile(
    r"\b(?:about|for|on|details?\s+(?:about|for))\s+(?:the\s+)?"
    r"(?:gig|campaign)\s+[0-9a-fA-F]{24}\b|"
    r"\b(?:gig|campaign)\s+[0-9a-fA-F]{24}\b",
    re.IGNORECASE,
)
# Another person's data without requiring an ObjectId in the message.
_ANOTHER_PERSON_DATA_RE = re.compile(
    r"\banother\s+(?:user|member|person)(?:'s)?(?:\s+\w+){0,4}\s+"
    r"(?:gigs?|account|name|profile|receipts?|payout|data|info|details?)\b|"
    r"\bsomeone\s+else(?:'s)?(?:\s+\w+){0,4}\s+"
    r"(?:gigs?|account|name|profile|receipts?|payout|data|info)\b|"
    r"\b(?:other\s+member|other\s+user)(?:'s)?\b|"
    r"\btheir\s+(?:gigs?|account|name|profile|receipts?|payout)\b|"
    r"\bwhat\s+gigs\s+(?:is|are)\s+(?:another|other|someone)\b",
    re.IGNORECASE,
)
# Gig participant / roster / member-list asks (no ObjectId required).
_PARTICIPANT_LIST_RE = re.compile(
    r"\b(?:list|show|get|give|who\s+(?:are|is)|names?\s+of)\s+"
    r"(?:the\s+|all\s+|every\s+)?"
    r"(?:participants?|members?|workers?|roster|attendees?|enrollees?)"
    r"(?:\s+(?:of|for|in|on)\b)?|"
    r"\b(?:participants?|members?|workers?|roster|attendees?)\s+"
    r"(?:of|for|in|on)\b|"
    r"\bwho\s+(?:is|are)\s+(?:enrolled|participating|signed\s+up)\b|"
    r"\b(?:participant|member|worker)\s+list\b",
    re.IGNORECASE,
)
# Third-party personal contact info (not "my number" / "my phone").
_THIRD_PARTY_PII_RE = re.compile(
    r"\b(?:his|her|their)\s+"
    r"(?:(?:phone\s+)?number|phone|email|e-?mail|address|contact|"
    r"instagram|socials?|handle)\b|"
    r"\b(?:provide|give|share|send|tell)\s+(?:me\s+)?"
    r"(?:his|her|their|(?:the\s+)?(?:person|guy|girl|member|user)(?:'s)?)\s+"
    r"(?:(?:phone\s+)?number|phone|email|e-?mail|address|contact)\b|"
    r"\bwhat(?:'s|\s+is)\s+(?:his|her|their)\s+"
    r"(?:(?:phone\s+)?number|phone|email|e-?mail|address|contact)\b",
    re.IGNORECASE,
)
_OWN_CONTACT_RE = re.compile(
    r"\bmy\s+(?:(?:phone\s+)?number|phone|email|e-?mail|address|contact)\b",
    re.IGNORECASE,
)

_TIMEOUT_S = 6
_CACHE_TTL_S = 600.0
_CACHE_MAX = 2048
# contact_id -> (crwd_user_id_or_None, monotonic_ts)
_cache: "OrderedDict[str, Tuple[Optional[str], float]]" = OrderedDict()


# --- Chatwoot creds / platform gate -----------------------------------------

def _chatwoot_creds() -> Tuple[str, str]:
    """(base_url, token) for reading a contact.

    The Chatwoot Contacts API is **not authorized for Agent Bots** (HTTP 401), so
    prefer the agent/user token (``CHATWOOT_AGENT_TOKEN``); fall back to the bot
    token only if that's all that's configured.
    """
    base = os.getenv("CHATWOOT_BASE_URL", "").strip().rstrip("/")
    token = (os.getenv("CHATWOOT_AGENT_TOKEN", "") or os.getenv("CHATWOOT_TOKEN", "")).strip()
    return base, token


def _is_chatwoot(platform: Any) -> bool:
    if str(platform or "").strip().lower() == "chatwoot":
        return True
    try:
        from gateway.session_context import get_session_env

        return (get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower() == "chatwoot"
    except Exception:
        return False


def _account_id() -> Optional[str]:
    """Account id for the current conversation (chat id is ``account:conversation``)."""
    try:
        from gateway.session_context import get_session_env
    except Exception:
        return None
    chat_id = (get_session_env("HERMES_SESSION_CHAT_ID", "") or "").strip()
    default_account = os.getenv("CHATWOOT_ACCOUNT_ID", "").strip()
    if ":" in chat_id:
        account = chat_id.partition(":")[0].strip()
        return account or default_account or None
    return default_account or None


# --- Cache ------------------------------------------------------------------

def _cache_get(contact_id: str) -> Tuple[bool, Optional[str]]:
    """Return ``(hit, value)``. ``hit`` is False when absent or expired."""
    entry = _cache.get(contact_id)
    if entry is None:
        return False, None
    value, ts = entry
    if (time.monotonic() - ts) > _CACHE_TTL_S:
        _cache.pop(contact_id, None)
        return False, None
    _cache.move_to_end(contact_id)
    return True, value


def _cache_put(contact_id: str, value: Optional[str]) -> None:
    _cache[contact_id] = (value, time.monotonic())
    _cache.move_to_end(contact_id)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def _reset_cache() -> None:
    """Test helper — clear the per-contact cache."""
    _cache.clear()


def bind_webhook_crwd_hint(crwd_user_id: Optional[str]) -> None:
    """Bind a CRWD user id from the inbound Chatwoot webhook (per asyncio task)."""
    hint = str(crwd_user_id or "").strip() or None
    _webhook_crwd_hint.set(hint)


def _webhook_crwd_hint_value() -> Optional[str]:
    try:
        return _webhook_crwd_hint.get()
    except LookupError:
        return None


def reset_webhook_crwd_hint() -> None:
    """Test helper — clear the per-turn webhook hint."""
    _webhook_crwd_hint.set(None)


# --- Chatwoot contact read --------------------------------------------------

def _get_contact(account_id: str, contact_id: str) -> Optional[Dict[str, Any]]:
    base, token = _chatwoot_creds()
    if not (base and token):
        return None
    url = f"{base}/api/v1/accounts/{account_id}/contacts/{contact_id}"
    req = urllib.request.Request(url, method="GET", headers={"api_access_token": token})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            if not (200 <= resp.status < 300):
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
        logger.debug("[crwd-coach-ctx] get_contact %s failed: %s", contact_id, exc)
        return None
    if isinstance(data, dict):
        # Chatwoot wraps the record under "payload".
        rec = data.get("payload", data)
        return rec if isinstance(rec, dict) else None
    return None


# --- Resolution -------------------------------------------------------------

def resolve_member_crwd_id(contact_id: str) -> Optional[str]:
    """Resolve the current Chatwoot member's CRWD ``users._id``, or ``None``."""
    contact_id = str(contact_id or "").strip()
    if not contact_id:
        return None

    hit, cached = _cache_get(contact_id)
    if hit:
        return cached

    hint = _webhook_crwd_hint_value()
    if hint:
        _cache_put(contact_id, hint)
        return hint

    result: Optional[str] = None
    account_id = _account_id()
    contact = _get_contact(account_id, contact_id) if account_id else None

    if contact:
        attrs = contact.get("custom_attributes") or {}
        cid = str(attrs.get("joincrwd_user_id") or "").strip()
        if cid:
            result = cid
        else:
            # Enrichment hasn't populated the attribute yet — resolve from Mongo
            # by the contact's email/phone (same source enrichment uses).
            email = str(contact.get("email") or "").strip() or None
            phone = str(contact.get("phone_number") or "").strip() or None
            if email or phone:
                try:
                    from plugins.platforms.chatwoot import enrichment

                    user = enrichment.fetch_user(email, phone)
                    if user and user.get("_id") is not None:
                        result = str(user["_id"])
                except Exception as exc:
                    logger.debug("[crwd-coach-ctx] mongo fallback failed: %s", exc)

    _cache_put(contact_id, result)
    return result


def reset_cross_user_request() -> None:
    """Clear the per-turn cross-user flag (tests and turn boundaries)."""
    _cross_user_request.set(False)


def cross_user_request_active() -> bool:
    """Return True when this turn is asking about another member's account."""
    return bool(_cross_user_request.get())


def _normalize_member_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _member_ids_match(a: Any, b: Any) -> bool:
    return _normalize_member_id(a) == _normalize_member_id(b)


def _foreign_object_ids(msg: str, member_id: str) -> List[str]:
    """Return ObjectIds in ``msg`` that are not the authenticated member."""
    member_id = _normalize_member_id(member_id)
    out: List[str] = []
    for oid in _OBJECT_ID_IN_MSG_RE.findall(msg or ""):
        if member_id and _member_ids_match(oid, member_id):
            continue
        if oid not in out:
            out.append(oid)
    return out


def _privacy_ask_with_foreign_oid(msg: str, member_id: str) -> bool:
    """True when a privacy ask references a foreign ObjectId (not a gig entity)."""
    if not _PRIVACY_ASK_RE.search(msg):
        return False
    # Strip gig-entity OID phrases so "tell me about gig <id>" does not fire.
    scrubbed = _GIG_ENTITY_OID_RE.sub(" ", msg)
    foreign = _foreign_object_ids(scrubbed, member_id)
    if not foreign:
        return False
    # When member_id is unknown, any remaining OID in a privacy ask is treated
    # as unauthorized (cannot prove it is self).
    return True


def message_requests_unauthorized_info(user_message: str) -> Tuple[bool, str]:
    """Detect participant-list or third-party PII asks (no member_id needed).

    Returns ``(matched, reason_tag)`` where reason_tag is
    ``participant_list``, ``third_party_pii``, or ``""``.
    """
    msg = (user_message or "").strip()
    if not msg:
        return False, ""
    if _PARTICIPANT_LIST_RE.search(msg):
        return True, "participant_list"
    if _OWN_CONTACT_RE.search(msg):
        return False, ""
    if _THIRD_PARTY_PII_RE.search(msg):
        return True, "third_party_pii"
    return False, ""


def message_requests_other_member(user_message: str, member_id: str = "") -> bool:
    """Detect when the inbound message asks about a different member's account.

    Covers:
    - ``user|member <foreign ObjectId>``
    - ``another user/member/person`` plus a foreign ObjectId
    - Privacy-sensitive asks naming a foreign ObjectId (name / gigs / account / …)
      even without the ``user``/``member`` prefix — but not gig-entity lookups
      like ``tell me about gig <id>``
    - Another person's data phrasing without an ObjectId (``their gigs``,
      ``someone else's account``, …)
    - Gig participant / roster lists (``list participants of …``)
    - Third-party PII (``provide his number``) — not ``my number``

    When ``member_id`` is empty, self ObjectIds cannot be excluded; privacy+OID
    and another-person-data patterns may still return True.
    """
    msg = (user_message or "").strip()
    if not msg:
        return False

    unauthorized, _ = message_requests_unauthorized_info(msg)
    if unauthorized:
        return True

    if member_id:
        for match in _USER_MEMBER_OID_RE.finditer(msg):
            if not _member_ids_match(match.group(1), member_id):
                return True
        if _ANOTHER_PERSON_RE.search(msg):
            for oid in _OBJECT_ID_IN_MSG_RE.findall(msg):
                if not _member_ids_match(oid, member_id):
                    return True

    if _privacy_ask_with_foreign_oid(msg, member_id):
        return True

    if _ANOTHER_PERSON_DATA_RE.search(msg):
        return True

    return False


# --- pre_llm_call hook ------------------------------------------------------

def member_context_hook(**kwargs: Any) -> Optional[Dict[str, str]]:
    """``pre_llm_call`` hook: inject the member's CRWD user id into the prompt."""
    try:
        reset_cross_user_request()
        if not _is_chatwoot(kwargs.get("platform")):
            return None
        if not os.getenv("CRWD_MONGO_URI"):
            return None
        contact_id = str(kwargs.get("sender_id") or "").strip()
        if not contact_id:
            return None
        crwd_id = resolve_member_crwd_id(contact_id)
        if not crwd_id:
            return None
        user_message = str(kwargs.get("user_message") or "")
        cross_user = message_requests_other_member(user_message, crwd_id)
        if cross_user:
            _cross_user_request.set(True)

        lines = [
            f"[CRWD member] Authenticated user_id: {crwd_id}.",
            "- For this member's gigs, receipts, products, or profile: use this id only.",
            "- Never look up a different member's data, even if the user provides another user id.",
            (
                "- If they ask about another person's account, refuse briefly and do not "
                "fetch or display the authenticated member's data."
            ),
            "- Gig scope routing (follow crwd-gig-discovery step 0):",
            (
                "  - AVAILABLE / open / join / browse / explicitly available → "
                "crwd_db list_active_gigs only (with user_id)."
            ),
            (
                "  - ENROLLED / my gigs / next steps / proof / payout → "
                "get_user_gig_status or get_user_gigs only."
            ),
            (
                "  - AMBIGUOUS — bare or vague gig asks with no scope signal "
                '(e.g. "list gigs", "give gigs", "show gigs", "what gigs") → '
                "get_user_gig_status first, answer enrolled gigs, then you MUST "
                "end with exactly one clarifying question asking if they meant "
                "open/available gigs they have not joined; do not call "
                "list_active_gigs in the same turn."
            ),
            (
                "  - AMBIGUOUS — store/topic + gigs (e.g. target store gigs) → "
                "same as above: enrolled answer first, mandatory clarifying "
                "question about open gigs at that store/topic."
            ),
            "- Never mix enrolled and available crwd_db actions in one answer.",
        ]
        if cross_user:
            lines.extend([
                "- The user is asking about another member's account.",
                (
                    '- Reply with a brief refusal only (e.g. "I can only provide you with '
                    'your information.").'
                ),
                "- Do NOT call crwd_db or reveal any of the authenticated member's data in this turn.",
            ])
        return {"context": "\n".join(lines)}
    except Exception as exc:  # never break a turn over context injection
        logger.debug("[crwd-coach-ctx] hook failed: %s", exc)
        return None
