"""Find or create a Chatwoot conversation for a CRWD lead (API inbox).

Called from ``leads.py`` after user upsert + gig interest. Not an LLM tool.
This slice targets ``Channel::Api`` only; flip ``LEADS_INBOX_CHANNEL`` later
for SMS.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from plugins.platforms.chatwoot.client import account_id, api_request, user_token, base_url

logger = logging.getLogger(__name__)

# Switch this (and source_id rules) to ``Channel::TwilioSms`` / ``Channel::Sms`` later.
LEADS_INBOX_CHANNEL = "Channel::Api"

_JOINCRWD_USER_ID = "joincrwd_user_id"
_AI_MODE = "ai_mode"

# Lower is better. pending first; resolved last. ``open`` is CRWD handoff but we still reuse.
_STATUS_RANK = {
    "pending": 0,
    "snoozed": 1,
    "open": 2,
    "resolved": 3,
}


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"error": message}
    out.update(extra)
    return out


def _payload_list(data: Any) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        payload = data.get("payload")
        if isinstance(payload, list):
            return payload
    return []


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unwrap_contact(data: Any) -> Dict[str, Any]:
    """Normalize create/show/search contact payloads to a contact dict."""
    raw = data
    if isinstance(raw, dict):
        payload = raw.get("payload")
        if isinstance(payload, dict):
            contact = payload.get("contact")
            if isinstance(contact, dict):
                # Merge contact_inbox from create so source_id is available.
                inbox_row = payload.get("contact_inbox")
                if isinstance(inbox_row, dict) and inbox_row:
                    existing = contact.get("contact_inboxes")
                    inboxes = list(existing) if isinstance(existing, list) else []
                    if inbox_row not in inboxes:
                        inboxes.append(inbox_row)
                    merged = dict(contact)
                    merged["contact_inboxes"] = inboxes
                    return merged
                return contact
            if payload.get("id") is not None:
                return payload
        if raw.get("id") is not None:
            return raw
    return {}


def _inbox_id_of(inbox: Mapping[str, Any]) -> str:
    return str(inbox.get("id") or "").strip()


def _channel_type(inbox: Mapping[str, Any]) -> str:
    return str(inbox.get("channel_type") or "").strip()


def _list_inboxes(acct: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    ok, data, err = api_request("GET", f"/api/v1/accounts/{acct}/inboxes")
    if not ok:
        return None, err or "failed to list inboxes"
    items: List[Dict[str, Any]] = []
    for row in _payload_list(data):
        if isinstance(row, dict):
            items.append(row)
    return items, ""


def resolve_api_inbox(acct: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return the target API inbox, or ``(None, error)``."""
    inboxes, err = _list_inboxes(acct)
    if err:
        return None, err
    api_inboxes = [i for i in inboxes if _channel_type(i) == LEADS_INBOX_CHANNEL]
    preferred = (
        os.getenv("CHATWOOT_INBOX_ID", "") or ""
    ).strip()
    if preferred:
        match = next((i for i in inboxes if _inbox_id_of(i) == preferred), None)
        if match is None:
            return None, f"CHATWOOT_INBOX_ID={preferred} not found on account {acct}"
        if _channel_type(match) != LEADS_INBOX_CHANNEL:
            return None, (
                f"CHATWOOT_INBOX_ID={preferred} is {_channel_type(match) or 'unknown'}, "
                f"expected {LEADS_INBOX_CHANNEL}"
            )
        return match, ""
    if len(api_inboxes) == 1:
        return api_inboxes[0], ""
    if not api_inboxes:
        return None, f"no {LEADS_INBOX_CHANNEL} inbox on account {acct}"
    names = ", ".join(
        f"{_inbox_id_of(i)}:{i.get('name') or '?'}" for i in api_inboxes
    )
    return None, (
        f"multiple {LEADS_INBOX_CHANNEL} inboxes ({names}); set CHATWOOT_INBOX_ID"
    )


def _norm_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_phone(value: Any) -> str:
    return str(value or "").strip()


def _contact_matches(contact: Mapping[str, Any], email: str, phone: str) -> bool:
    if email and _norm_email(contact.get("email")) == email:
        return True
    if phone and _norm_phone(contact.get("phone_number")) == phone:
        return True
    return False


def _search_contact(acct: str, email: str, phone: str) -> Tuple[Optional[Dict[str, Any]], str]:
    queries: List[str] = []
    if email:
        queries.append(email)
    if phone:
        queries.append(phone)
    last_err = ""
    saw_ok = False
    for q in queries:
        ok, data, err = api_request(
            "GET",
            f"/api/v1/accounts/{acct}/contacts/search",
            query={"q": q},
        )
        if not ok:
            last_err = err or "contact search failed"
            continue
        saw_ok = True
        for row in _payload_list(data):
            if isinstance(row, dict) and _contact_matches(row, email, phone):
                return row, ""
    if not saw_ok and last_err:
        return None, last_err
    return None, ""


def _create_contact(
    acct: str,
    inbox_id: str,
    email: str,
    phone: str,
    name: str,
    crwd_user_id: str,
) -> Tuple[Optional[Dict[str, Any]], str]:
    body: Dict[str, Any] = {
        "inbox_id": int(inbox_id) if str(inbox_id).isdigit() else inbox_id,
        "identifier": crwd_user_id,
        "custom_attributes": {_JOINCRWD_USER_ID: crwd_user_id},
    }
    if name:
        body["name"] = name
    if email:
        body["email"] = email
    if phone:
        body["phone_number"] = phone
    ok, data, err = api_request("POST", f"/api/v1/accounts/{acct}/contacts", body=body)
    if not ok:
        return None, _format_api_error(err, data, "create contact failed")
    contact = _unwrap_contact(data)
    if not contact.get("id"):
        return None, "create contact returned no id"
    return contact, ""


def _format_api_error(err: str, data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        desc = data.get("message") or data.get("description") or data.get("error")
        if desc:
            return f"{err or fallback}: {desc}"
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            return f"{err or fallback}: {errors}"
    return err or fallback


def _ensure_joincrwd_user_id(acct: str, contact: Dict[str, Any], crwd_user_id: str) -> Dict[str, Any]:
    attrs = contact.get("custom_attributes")
    current = ""
    if isinstance(attrs, dict):
        current = str(attrs.get(_JOINCRWD_USER_ID) or "").strip()
    if current:
        return contact
    cid = str(contact.get("id") or "").strip()
    merged = dict(attrs) if isinstance(attrs, dict) else {}
    merged[_JOINCRWD_USER_ID] = crwd_user_id
    ok, data, err = api_request(
        "PUT",
        f"/api/v1/accounts/{acct}/contacts/{cid}",
        body={"custom_attributes": merged},
    )
    if not ok:
        logger.warning("[chatwoot-leads] set joincrwd_user_id failed: %s", err)
        return contact
    updated = _unwrap_contact(data)
    return updated or contact


def _ensure_contact_profile(
    acct: str, contact: Dict[str, Any], name: str, phone: str
) -> Dict[str, Any]:
    """Fill in ``name`` / ``phone_number`` on an existing contact if it's missing them.

    Never overwrites a value the contact already has — this lead POST may be
    less complete than what Chatwoot already knows (e.g. a name added later
    by an agent).
    """
    fields: Dict[str, Any] = {}
    if name and not str(contact.get("name") or "").strip():
        fields["name"] = name
    if phone and not str(contact.get("phone_number") or "").strip():
        fields["phone_number"] = phone
    if not fields:
        return contact
    cid = str(contact.get("id") or "").strip()
    ok, data, err = api_request(
        "PUT",
        f"/api/v1/accounts/{acct}/contacts/{cid}",
        body=fields,
    )
    if not ok:
        logger.warning("[chatwoot-leads] set contact profile failed: %s", err)
        return contact
    updated = _unwrap_contact(data)
    return updated or contact


def _ai_mode_enabled(value: Any) -> bool:
    """Same convention as ``ai_mode._is_enabled``: bool True or string ``"true"``."""
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() == "true":
        return True
    return False


def _ensure_ai_mode(acct: str, contact: Dict[str, Any]) -> Dict[str, Any]:
    """Turn on the ``ai_mode`` opt-in for this contact so the Coach keeps replying."""
    attrs = contact.get("custom_attributes")
    current = attrs.get(_AI_MODE) if isinstance(attrs, dict) else None
    if _ai_mode_enabled(current):
        return contact
    cid = str(contact.get("id") or "").strip()
    merged = dict(attrs) if isinstance(attrs, dict) else {}
    merged[_AI_MODE] = True
    ok, data, err = api_request(
        "PUT",
        f"/api/v1/accounts/{acct}/contacts/{cid}",
        body={"custom_attributes": merged},
    )
    if not ok:
        logger.warning("[chatwoot-leads] set ai_mode failed: %s", err)
        return contact
    updated = _unwrap_contact(data)
    return updated or contact


def _inbox_id_from_conversation(conv: Mapping[str, Any]) -> str:
    raw = conv.get("inbox_id")
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    meta = conv.get("meta")
    if isinstance(meta, dict):
        inbox = meta.get("inbox")
        if isinstance(inbox, dict) and inbox.get("id") is not None:
            return str(inbox["id"]).strip()
    return ""


def _list_conversations(acct: str, contact_id: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    ok, data, err = api_request(
        "GET",
        f"/api/v1/accounts/{acct}/contacts/{contact_id}/conversations",
    )
    if not ok:
        return None, err or "failed to list conversations"
    items: List[Dict[str, Any]] = []
    for row in _payload_list(data):
        if isinstance(row, dict):
            items.append(row)
    return items, ""


def _status_rank(status: Any) -> int:
    key = str(status or "").strip().lower()
    if key in _STATUS_RANK:
        return _STATUS_RANK[key]
    if key:
        return 1
    return 4


def _pick_api_conversation(
    conversations: Sequence[Mapping[str, Any]],
    target_inbox_id: str,
) -> Optional[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for conv in conversations:
        if not isinstance(conv, dict):
            continue
        if _inbox_id_from_conversation(conv) != str(target_inbox_id):
            continue
        if conv.get("id") is None:
            continue
        matches.append(conv)
    if not matches:
        return None
    matches.sort(key=lambda c: (_status_rank(c.get("status")), -int(c.get("last_activity_at") or 0)))
    return matches[0]


def _source_id_for_inbox(contact: Mapping[str, Any], inbox_id: str) -> str:
    rows = contact.get("contact_inboxes")
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        inbox = row.get("inbox") if isinstance(row.get("inbox"), dict) else {}
        rid = str(row.get("inbox_id") or inbox.get("id") or "").strip()
        if rid == str(inbox_id):
            return str(row.get("source_id") or "").strip()
    return ""


def _ensure_contact_inbox(acct: str, contact_id: str, inbox_id: str) -> Tuple[str, str]:
    ok, data, err = api_request(
        "POST",
        f"/api/v1/accounts/{acct}/contacts/{contact_id}/contact_inboxes",
        body={"inbox_id": int(inbox_id) if str(inbox_id).isdigit() else inbox_id},
    )
    if not ok:
        return "", _format_api_error(err, data, "create contact_inbox failed")
    payload = data.get("payload") if isinstance(data, dict) else data
    row = payload if isinstance(payload, dict) else _as_dict(data)
    source = str(row.get("source_id") or "").strip()
    if not source:
        return "", "contact_inbox create returned no source_id"
    return source, ""


def _create_conversation(
    acct: str,
    inbox_id: str,
    contact_id: str,
    source_id: str,
) -> Tuple[Optional[str], str]:
    body: Dict[str, Any] = {
        "source_id": source_id,
        "inbox_id": int(inbox_id) if str(inbox_id).isdigit() else inbox_id,
        "contact_id": int(contact_id) if str(contact_id).isdigit() else contact_id,
        "status": "pending",
    }
    ok, data, err = api_request(
        "POST",
        f"/api/v1/accounts/{acct}/conversations",
        body=body,
    )
    if not ok:
        return None, _format_api_error(err, data, "create conversation failed")
    payload = data.get("payload") if isinstance(data, dict) and isinstance(data.get("payload"), dict) else data
    row = _as_dict(payload)
    conv_id = row.get("id")
    if conv_id is None and isinstance(data, dict):
        conv_id = data.get("id")
    if conv_id is None:
        return None, "create conversation returned no id"
    return str(conv_id), ""


def ensure_conversation(
    *,
    email: str = "",
    phone: str = "",
    name: str = "",
    crwd_user_id: str,
) -> Dict[str, Any]:
    """Find or create an API-inbox Chatwoot thread for this person.

    Returns identity fields, or ``{"error": "..."}``.
    """
    if not base_url() or not user_token():
        return _error("Chatwoot not configured (CHATWOOT_BASE_URL + user/agent token)")
    acct = account_id()
    if not acct:
        return _error("CHATWOOT_ACCOUNT_ID is required to ensure a conversation")
    crwd = str(crwd_user_id or "").strip()
    if not crwd:
        return _error("crwd_user_id is required")
    email_n = _norm_email(email)
    phone_n = _norm_phone(phone)
    if not email_n and not phone_n:
        return _error("email or phone is required")

    inbox, err = resolve_api_inbox(acct)
    if err or not inbox:
        return _error(err or "could not resolve API inbox")
    inbox_id = _inbox_id_of(inbox)

    contact, err = _search_contact(acct, email_n, phone_n)
    created_contact = False
    if err:
        return _error(err)
    if contact is None:
        contact, err = _create_contact(acct, inbox_id, email_n, phone_n, name, crwd)
        if err or not contact:
            return _error(err or "create contact failed")
        created_contact = True
    else:
        contact = _ensure_joincrwd_user_id(acct, contact, crwd)
        contact = _ensure_contact_profile(acct, contact, name, phone_n)

    contact_id = str(contact.get("id") or "").strip()
    if not contact_id:
        return _error("contact has no id")

    contact = _ensure_ai_mode(acct, contact)

    conversations, err = _list_conversations(acct, contact_id)
    if err:
        return _error(err)
    existing = _pick_api_conversation(conversations or (), inbox_id)
    if existing is not None:
        conv_id = str(existing["id"])
        logger.info(
            "[chatwoot-leads] reused conversation account=%s conversation=%s inbox=%s",
            acct,
            conv_id,
            inbox_id,
        )
        return {
            "account_id": acct,
            "contact_id": contact_id,
            "conversation_id": conv_id,
            "inbox_id": inbox_id,
            "chat_id": f"{acct}:{conv_id}",
            "created": False,
            "contact_created": created_contact,
            "conversation_status": str(existing.get("status") or "").strip().lower() or "pending",
        }

    source_id = _source_id_for_inbox(contact, inbox_id)
    if not source_id:
        source_id, err = _ensure_contact_inbox(acct, contact_id, inbox_id)
        if err:
            return _error(err)

    conv_id, err = _create_conversation(acct, inbox_id, contact_id, source_id)
    if err or not conv_id:
        return _error(err or "create conversation failed")
    logger.info(
        "[chatwoot-leads] created conversation account=%s conversation=%s inbox=%s",
        acct,
        conv_id,
        inbox_id,
    )
    return {
        "account_id": acct,
        "contact_id": contact_id,
        "conversation_id": conv_id,
        "inbox_id": inbox_id,
        "chat_id": f"{acct}:{conv_id}",
        "created": True,
        "contact_created": created_contact,
        "conversation_status": "pending",
    }
