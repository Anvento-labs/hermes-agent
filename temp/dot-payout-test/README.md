# Dot payout-links API smoke test

Small standalone script to verify Dot sandbox credentials and find payout links
by payee **email** or **phone** (client-side filter — the public API has no
`search=` param like the dashboard's internal API).

## Setup

```bash
cd temp/dot-payout-test
cp .env.example .env
# Edit .env with Sandbox client_id + api_key from dashboard.dots.dev
```

## Run

```bash
# Prove auth works — list first page of payout links (no filter)
python test_dot_payouts.py list

# Find payouts for a member (same as dashboard search by email)
python test_dot_payouts.py find --email makaylatmagat@gmail.com

# Find by phone (matches payee.country_code + payee.phone_number on each link)
python test_dot_payouts.py find --phone +14155551234
python test_dot_payouts.py find --phone 4155551234 --country-code 1

# Scan more pages if needed (default max 10 pages × 100 links)
python test_dot_payouts.py find --email user@example.com --max-pages 20
```

Uses only Python stdlib — no pip install required.
