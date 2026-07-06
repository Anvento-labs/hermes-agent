#!/usr/bin/env python3
"""Smoke-test Dot (Dots.dev) payout-links API — find payouts by email or phone.

Public API: GET {DOT_API_BASE_URL}/v2/payout-links (paginated, no server search).
We paginate and match payee.email / payee.phone_number client-side, same data
the dashboard shows but without internalapi.dots.dev.

Configure via .env in this directory (see .env.example) or env vars:
  DOT_CLIENT_ID, DOT_API_KEY, DOT_API_BASE_URL
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_TIMEOUT_S = 30
_PAGE_SIZE = 100
_DEFAULT_MAX_PAGES = 10

_ENV_PATH = Path(__file__).resolve().parent / ".env"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _normalize_phone(country_code: str, phone_number: str) -> str:
    """E.164-ish digits only, e.g. 14154332334."""
    cc = _digits(country_code)
    pn = _digits(phone_number)
    if not pn:
        return ""
    if cc and not pn.startswith(cc):
        return cc + pn
    return pn


def _phone_matches(
    payee: Dict[str, Any],
    query_phone: str,
    query_country_code: str = "",
) -> bool:
    if not query_phone:
        return False
    payee_cc = str(payee.get("country_code") or "")
    payee_pn = str(payee.get("phone_number") or "")
    if not payee_pn:
        return False

    want = _normalize_phone(query_country_code or "1", query_phone)
    have = _normalize_phone(payee_cc or "1", payee_pn)
    if not want or not have:
        return False
    if want == have:
        return True
    # US: compare last 10 digits
    if len(want) >= 10 and len(have) >= 10:
        return want[-10:] == have[-10:]
    return False


def _email_matches(payee: Dict[str, Any], query_email: str) -> bool:
    if not query_email:
        return False
    payee_email = (payee.get("email") or "").strip().lower()
    return payee_email == query_email.strip().lower()


def _auth_headers() -> Dict[str, str]:
    client_id = os.getenv("DOT_CLIENT_ID", "").strip()
    api_key = os.getenv("DOT_API_KEY", "").strip()
    if not client_id or not api_key:
        raise SystemExit(
            "Set DOT_CLIENT_ID and DOT_API_KEY in .env or the environment "
            "(see .env.example)."
        )
    token = base64.b64encode(f"{client_id}:{api_key}".encode()).decode("ascii")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {token}",
    }
    app_id = os.getenv("DOT_APP_ID", "").strip()
    if app_id:
        headers["Api-App-Id"] = app_id
    return headers


def _base_url() -> str:
    base = os.getenv(
        "DOT_API_BASE_URL", "https://pls.senddotssandbox.com/api"
    ).strip().rstrip("/")
    if not base:
        raise SystemExit("Set DOT_API_BASE_URL (e.g. https://pls.senddotssandbox.com/api)")
    return base


def dot_get(path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Any, Optional[str]]:
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    query = urllib.parse.urlencode(params)
    url = f"{_base_url()}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, method="GET", headers=_auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
            if not (200 <= resp.status < 300):
                return None, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return None, f"HTTP {exc.code}" + (f": {body}" if body else "")
    except Exception as exc:
        return None, str(exc)
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return None, "invalid JSON from Dot"


def list_payout_links_page(
    limit: int = _PAGE_SIZE,
    starting_after: str = "",
) -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
    params: Dict[str, Any] = {"limit": limit}
    if starting_after:
        params["starting_after"] = starting_after
    data, err = dot_get("/v2/payout-links", params)
    if err:
        return [], False, err
    if not isinstance(data, dict):
        return [], False, "unexpected response shape (expected object with data[])"
    items = data.get("data") or []
    if not isinstance(items, list):
        return [], False, "unexpected data[] type"
    has_more = bool(data.get("has_more"))
    return items, has_more, None


def iter_payout_links(max_pages: int = _DEFAULT_MAX_PAGES):
    cursor = ""
    for page in range(max_pages):
        items, has_more, err = list_payout_links_page(starting_after=cursor)
        if err:
            raise RuntimeError(err)
        yield page + 1, items
        if not has_more or not items:
            break
        cursor = str(items[-1].get("id") or "")
        if not cursor:
            break


def find_payout_links(
    email: str = "",
    phone: str = "",
    country_code: str = "1",
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> List[Dict[str, Any]]:
    if not email and not phone:
        raise SystemExit("Provide --email and/or --phone")

    matches: List[Dict[str, Any]] = []
    scanned = 0
    for _page_num, items in iter_payout_links(max_pages=max_pages):
        scanned += len(items)
        for link in items:
            payee = link.get("payee") if isinstance(link.get("payee"), dict) else {}
            if email and _email_matches(payee, email):
                matches.append(link)
            elif phone and _phone_matches(payee, phone, country_code):
                matches.append(link)
    print(f"Scanned {scanned} payout link(s) across up to {max_pages} page(s).", file=sys.stderr)
    return matches


def _summarize_link(link: Dict[str, Any]) -> Dict[str, Any]:
    payee = link.get("payee") or {}
    amount_cents = link.get("amount")
    amount_usd = None
    if isinstance(amount_cents, (int, float)):
        amount_usd = round(float(amount_cents) / 100.0, 2)
    return {
        "id": link.get("id"),
        "created": link.get("created"),
        "status": link.get("status"),
        "amount_cents": amount_cents,
        "amount_usd": amount_usd,
        "memo": link.get("memo"),
        "payee_name": " ".join(
            p for p in (payee.get("first_name"), payee.get("last_name")) if p
        ).strip() or None,
        "payee_email": payee.get("email"),
        "payee_phone": _normalize_phone(
            str(payee.get("country_code") or ""),
            str(payee.get("phone_number") or ""),
        ) or None,
        "delivery_method": (link.get("delivery") or {}).get("method"),
        "claimed_user_id": link.get("claimed_user_id"),
        "transfer_id": link.get("transfer_id"),
    }


def cmd_list(args: argparse.Namespace) -> int:
    items, has_more, err = list_payout_links_page(limit=args.limit)
    if err:
        print(json.dumps({"error": err}, indent=2))
        return 1
    out = {
        "count": len(items),
        "has_more": has_more,
        "items": [_summarize_link(x) for x in items],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    try:
        raw = find_payout_links(
            email=args.email or "",
            phone=args.phone or "",
            country_code=args.country_code or "1",
            max_pages=args.max_pages,
        )
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1

    out = {
        "query": {
            "email": args.email or None,
            "phone": args.phone or None,
            "country_code": args.country_code if args.phone else None,
        },
        "match_count": len(raw),
        "items": [_summarize_link(x) for x in raw],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    _load_dotenv(_ENV_PATH)

    parser = argparse.ArgumentParser(
        description="Test Dot GET /v2/payout-links (sandbox or production)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List first page of payout links (auth check)")
    p_list.add_argument("--limit", type=int, default=20, help="Page size (1-100)")
    p_list.set_defaults(func=cmd_list)

    p_find = sub.add_parser("find", help="Paginate and filter by payee email or phone")
    p_find.add_argument("--email", default="", help="Payee email to match")
    p_find.add_argument("--phone", default="", help="Payee phone (any format)")
    p_find.add_argument(
        "--country-code",
        default="1",
        help="Country code for --phone if not embedded (default: 1)",
    )
    p_find.add_argument(
        "--max-pages",
        type=int,
        default=_DEFAULT_MAX_PAGES,
        help=f"Max pages to scan ({_PAGE_SIZE} links/page, default {_DEFAULT_MAX_PAGES})",
    )
    p_find.set_defaults(func=cmd_find)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
