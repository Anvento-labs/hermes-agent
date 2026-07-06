#!/usr/bin/env python3
"""Find all payouts and their status for a user, by email or phone.

Read-only: only calls GET {DOT_API_BASE_URL}/v2/payout-links.
Reads DOT_CLIENT_ID, DOT_API_KEY, DOT_API_BASE_URL from .env in this folder.

Usage:
    python check_payouts.py --email user@example.com
    python check_payouts.py --phone 4155551234
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def load_env():
    """Load KEY=VALUE lines from the .env file next to this script."""
    path = Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def get_page(base_url, headers, starting_after=""):
    """Fetch one page of payout links. Returns (items, has_more)."""
    params = {"limit": 100}
    if starting_after:
        params["starting_after"] = starting_after
    url = f"{base_url}/v2/payout-links?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    return data.get("data") or [], bool(data.get("has_more"))


def digits(s):
    return re.sub(r"\D", "", s or "")


def matches(payee, email, phone):
    """True if this payout's payee matches the given email or phone."""
    if email and (payee.get("email") or "").lower() == email.lower():
        return True
    if phone:
        want = digits(phone)[-10:]
        have = digits(payee.get("phone_number"))[-10:]
        if want and want == have:
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Find a user's payouts and their status.")
    parser.add_argument("--email", default="", help="Payee email")
    parser.add_argument("--phone", default="", help="Payee phone (any format)")
    args = parser.parse_args()

    if not args.email and not args.phone:
        sys.exit("Provide --email or --phone")

    load_env()
    client_id = os.getenv("DOT_CLIENT_ID", "").strip()
    api_key = os.getenv("DOT_API_KEY", "").strip()
    base_url = os.getenv("DOT_API_BASE_URL", "").strip().rstrip("/")
    if not (client_id and api_key and base_url):
        sys.exit("Set DOT_CLIENT_ID, DOT_API_KEY and DOT_API_BASE_URL in .env")

    token = base64.b64encode(f"{client_id}:{api_key}".encode()).decode()
    headers = {"Accept": "application/json", "Authorization": f"Basic {token}"}

    # Page through every payout link and keep the ones for this user.
    found, scanned, cursor = [], 0, ""
    while True:
        items, has_more = get_page(base_url, headers, cursor)
        scanned += len(items)
        found += [x for x in items if matches(x.get("payee") or {}, args.email, args.phone)]
        if not has_more or not items:
            break
        cursor = str(items[-1].get("id") or "")
        if not cursor:
            break

    print(f"Scanned {scanned} payout(s); found {len(found)} for this user.\n")
    for p in found:
        payee = p.get("payee") or {}
        name = f"{payee.get('first_name', '')} {payee.get('last_name', '')}".strip()
        amount = p.get("amount")
        amount_usd = f"${amount / 100:.2f}" if isinstance(amount, (int, float)) else "?"
        print(f"- {p.get('id')} | {p.get('status')} | {amount_usd} | {name} | {payee.get('email')}")


if __name__ == "__main__":
    main()
