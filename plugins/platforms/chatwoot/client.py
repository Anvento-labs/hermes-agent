"""Chatwoot Application API client (user/agent token).

Shared by labels and leads conversation-ensure. Auth prefers
``CHATWOOT_AGENT_TOKEN``, then ``CHATWOOT_TOKEN``. Header name is
``api_access_token`` for both token types.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

_TIMEOUT_S = 8


def user_token() -> str:
    return (os.getenv("CHATWOOT_AGENT_TOKEN", "") or os.getenv("CHATWOOT_TOKEN", "")).strip()


def base_url() -> str:
    return os.getenv("CHATWOOT_BASE_URL", "").strip().rstrip("/")


def account_id() -> str:
    return os.getenv("CHATWOOT_ACCOUNT_ID", "").strip()


def api_request(
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Any, str]:
    """Call Chatwoot Application API. Returns ``(ok, parsed_json_or_none, error)``."""
    base = base_url()
    token = user_token()
    if not base or not token:
        return False, None, "Chatwoot not configured"

    url = f"{base}{path}"
    if query:
        params = urllib.parse.urlencode(
            {k: v for k, v in query.items() if v is not None and str(v) != ""}
        )
        if params:
            url = f"{url}?{params}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "api_access_token": token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw.strip() else None
            if 200 <= resp.status < 300:
                return True, parsed, ""
            return False, parsed, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:
            pass
        parsed = None
        if err_body.strip():
            try:
                parsed = json.loads(err_body)
            except json.JSONDecodeError:
                parsed = {"message": err_body}
        return False, parsed, f"HTTP {exc.code}"
    except Exception as exc:
        return False, None, str(exc)
