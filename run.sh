#!/usr/bin/env bash
#
# run.sh — pull this branch, build, and run the Chatwoot gateway. Meant to be
# run on the staging server from inside the repo checkout:
#
#   ./run.sh                # pull + install deps + run in foreground
#   BRANCH=other ./run.sh   # deploy a different branch
#
# To keep it running after you close the session:
#   nohup ./run.sh > gateway.log 2>&1 &
#
# One-time setup on the server before the first run:
#   1. git clone https://github.com/Anvento-labs/hermes-agent.git  (log in when asked)
#   2. mkdir -p ~/.hermes  and create ~/.hermes/.env + ~/.hermes/config.yaml
#      with the same contents as on your local machine.
#
# Deps install via uv's standalone installer — no apt packages needed, so a
# broken apt state on the box can't block a deploy. The extras cover exactly
# what the chatwoot platform_toolsets use (crwd/web/memory/... need no npm,
# no Playwright, no dashboard build): aiohttp for the webhook listener
# (messaging), boto3 for Bedrock, pymongo for the crwd tools.
#
set -euo pipefail

BRANCH="${BRANCH:-feat/crwd-proof-validator-and-risk-score}"
EXTRAS="${EXTRAS:-messaging,bedrock,cli}"

cd "$(dirname "$0")"

echo "==> Checking config"
if [ ! -f "$HOME/.hermes/.env" ] || [ ! -f "$HOME/.hermes/config.yaml" ]; then
  echo "ERROR: ~/.hermes/.env and ~/.hermes/config.yaml must exist (copy them from local)." >&2
  exit 1
fi

echo "==> Pulling ${BRANCH}"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "==> Installing uv (if missing)"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo "==> Installing dependencies (uv sync, extras: ${EXTRAS})"
# --frozen: exactly what uv.lock pins, same as the Dockerfile build.
uv sync --frozen $(printf -- '--extra %s ' ${EXTRAS//,/ })

# pymongo can't ride the lockfile: the `mongodb` extra was added to
# pyproject.toml without re-locking, and the lock can't currently be
# regenerated (the aiohttp CVE bump to 3.14.1 in messaging/matrix conflicts
# with the 3.13.4 pins in homeassistant/sms/teams, so `uv lock` fails).
# Install it explicitly at the version the extra pins.
uv pip install pymongo==4.11.3

echo "==> Starting gateway (HERMES_HOME=$HOME/.hermes)"
exec .venv/bin/hermes gateway run
