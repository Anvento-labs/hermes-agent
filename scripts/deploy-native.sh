#!/usr/bin/env bash
#
# deploy-native.sh — run the Chatwoot gateway natively on the staging EC2.
#
#   ./scripts/deploy-native.sh                 # full deploy
#   ./scripts/deploy-native.sh --config-only   # resync ~/.hermes + restart
#
# Why native rather than the Docker path (scripts/deploy-staging.sh): the image
# build spends nearly all its time on things the gateway never uses — the web
# dashboard build, the TUI build, Playwright's chromium, and `uv sync
# --extra all`. The chatwoot toolsets (crwd, skills, web, cronjob, clarify,
# memory, session_search, vision, file, todo) need none of it, so a venv with
# four extras is minutes instead of ~40.
#
# Why SSM rather than ssh: the box has NO port 22 ingress by design (the SG
# opens only 80/443/3000) and is SSM-managed. Everything below goes through
# ssm send-command; the source and config travel as short-lived S3 presigned
# URLs, so the instance needs no S3 permission of its own.
#
# Staging mirrors ~/.hermes exactly. That directory is treated as READ-ONLY:
# a live local gateway runs from it, so this script copies out of it and never
# writes to it. Set CONFIG_DIR to diverge staging from local.
#
# Prerequisite you must do once (it grants Bedrock access to the box; the
# gateway starts without it but fails on the first message):
#
#   aws iam put-role-policy --role-name icrwd-chatwoot-ec2-staging \
#     --policy-name HermesBedrockInvoke \
#     --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
#       "Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream",
#       "bedrock:ListFoundationModels","bedrock:ListInferenceProfiles"],
#       "Resource":"*"}]}'
#
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-i-0fd88a62ed130dcdb}"
AWS_REGION="${AWS_REGION:-us-east-2}"
# Staging mirrors the local config exactly: ~/.hermes is the source of truth.
# READ-ONLY — this script copies out of it and never writes to it, because a
# live local gateway runs from that directory. If staging ever needs to differ
# (e.g. its own Chatwoot bot token), point CONFIG_DIR at a separate dir rather
# than editing ~/.hermes.
CONFIG_DIR="${CONFIG_DIR:-$HOME/.hermes}"
S3_BUCKET="${S3_BUCKET:-cdk-hnb659fds-assets-079110101908-us-east-2}"
S3_PREFIX="${S3_PREFIX:-hermes-deploy}"
APP_DIR="${APP_DIR:-/opt/hermes-agent}"
RUN_USER="${RUN_USER:-ubuntu}"
SERVICE="${SERVICE:-hermes-gateway}"
EXTRAS="${EXTRAS:-messaging,bedrock,mongodb,cli}"
URL_TTL=1200   # presigned URL lifetime, seconds

CONFIG_ONLY=0
if [ "${1:-}" = "--config-only" ]; then CONFIG_ONLY=1; fi

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# Run a shell script on the box via SSM and stream back its output. Takes the
# script on stdin so quoting stays sane; fails loudly on a non-zero exit.
ssm_run() {
  local comment="$1" timeout="${2:-600}" script b64 params cmd status
  script="$(cat)"
  # SSM's AWS-RunShellScript executes with /bin/sh (dash on Ubuntu), so a
  # payload with `set -o pipefail` or /dev/tcp dies at line 1. Base64 the
  # script and pipe it to bash: one sh-safe command string, no quoting
  # hazards, and the payload runs under the shell it was written for.
  b64="$(printf '%s' "$script" | base64 | tr -d '\n')"
  params="$(jq -n --arg c "echo '${b64}' | base64 -d | bash" '{commands:[$c]}')"
  cmd=$(aws ssm send-command \
        --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" \
        --document-name AWS-RunShellScript \
        --comment "$comment" --timeout-seconds "$timeout" \
        --parameters "$params" \
        --query Command.CommandId --output text) || die "send-command failed"
  # Poll rather than sleep: installs are variable, and a fixed sleep either
  # wastes time or truncates the result.
  local waited=0
  while :; do
    status=$(aws ssm get-command-invocation --region "$AWS_REGION" \
             --command-id "$cmd" --instance-id "$INSTANCE_ID" \
             --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in
      Success|Failed|Cancelled|TimedOut) break ;;
    esac
    sleep 5; waited=$((waited+5))
    # `[ ... ] && break` would abort the script under set -e when the test is
    # false, so this stays an if.
    if [ "$waited" -gt "$timeout" ]; then break; fi
  done
  aws ssm get-command-invocation --region "$AWS_REGION" \
    --command-id "$cmd" --instance-id "$INSTANCE_ID" \
    --query 'StandardOutputContent' --output text
  if [ "$status" != "Success" ]; then
    aws ssm get-command-invocation --region "$AWS_REGION" \
      --command-id "$cmd" --instance-id "$INSTANCE_ID" \
      --query 'StandardErrorContent' --output text >&2
    die "remote step '$comment' ended as $status"
  fi
}

cleanup() {
  if [ -n "${UPLOADED:-}" ]; then
    aws s3 rm "s3://${S3_BUCKET}/${S3_PREFIX}/" --recursive --quiet 2>/dev/null || true
  fi
  rm -rf "${TMPDIR_LOCAL:-}" 2>/dev/null || true
}
trap cleanup EXIT

# --- preflight -------------------------------------------------------------
say "Preflight"
cd "$(dirname "$0")/.."
command -v jq >/dev/null || die "jq is required"
[ -f pyproject.toml ] || die "not at the repo root"
aws sts get-caller-identity >/dev/null 2>&1 || die "no working AWS credentials"

[ -f "$CONFIG_DIR/.env" ] || die "$CONFIG_DIR/.env not found"
[ -f "$CONFIG_DIR/config.yaml" ] || die "$CONFIG_DIR/config.yaml not found"

# Bedrock is what the gateway answers with; warn early if the role can't call it.
if ! aws iam get-role-policy --role-name icrwd-chatwoot-ec2-staging \
      --policy-name HermesBedrockInvoke >/dev/null 2>&1; then
  echo "WARNING: instance role has no HermesBedrockInvoke policy — the gateway"
  echo "         will start but fail on the first message. See the header."
fi

TMPDIR_LOCAL="$(mktemp -d)"
GIT_SHA="$(git rev-parse --short HEAD)"
echo "instance=$INSTANCE_ID  sha=$GIT_SHA  extras=$EXTRAS"

# --- package + upload ------------------------------------------------------
say "Packaging config from $CONFIG_DIR"
# .env and config.yaml only — never skills/. They self-seed from the repo on
# startup, and sync_skills() skips anything it reads as user-modified, so
# shipping a local skills/ would pin staging to local versions.
tar -czf "$TMPDIR_LOCAL/config.tar.gz" -C "$CONFIG_DIR" .env config.yaml
aws s3 cp --quiet "$TMPDIR_LOCAL/config.tar.gz" "s3://${S3_BUCKET}/${S3_PREFIX}/config.tar.gz"
UPLOADED=1
CONFIG_URL="$(aws s3 presign "s3://${S3_BUCKET}/${S3_PREFIX}/config.tar.gz" \
              --region "$AWS_REGION" --expires-in "$URL_TTL")"

if [ "$CONFIG_ONLY" -eq 0 ]; then
  say "Packaging source at $GIT_SHA"
  # git archive = exactly the committed tree, no .git, no node_modules.
  git archive --format=tar.gz -o "$TMPDIR_LOCAL/src.tar.gz" HEAD
  aws s3 cp --quiet "$TMPDIR_LOCAL/src.tar.gz" "s3://${S3_BUCKET}/${S3_PREFIX}/src.tar.gz"
  SRC_URL="$(aws s3 presign "s3://${S3_BUCKET}/${S3_PREFIX}/src.tar.gz" \
             --region "$AWS_REGION" --expires-in "$URL_TTL")"

  say "Installing on the box (python venv + uv + deps)"
  ssm_run "hermes gateway: install" 900 <<REMOTE
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip curl tar

rm -rf "${APP_DIR}"; mkdir -p "${APP_DIR}"
curl -fsSL --retry 3 -o /tmp/src.tar.gz "${SRC_URL}"
tar -xzf /tmp/src.tar.gz -C "${APP_DIR}"
rm -f /tmp/src.tar.gz

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install -q --upgrade pip uv
# uv resolves and installs far faster than pip; editable so paths resolve the
# same way they do in development.
cd "${APP_DIR}"
"${APP_DIR}/.venv/bin/uv" pip install --python "${APP_DIR}/.venv/bin/python" \
  -e ".[${EXTRAS}]"
"${APP_DIR}/.venv/bin/hermes" --version || true
chown -R ${RUN_USER}:${RUN_USER} "${APP_DIR}"
REMOTE
fi

# --- config + service ------------------------------------------------------
say "Syncing config and (re)starting the service"
ssm_run "hermes gateway: config + service" 300 <<REMOTE
set -euo pipefail
install -d -m 700 -o ${RUN_USER} -g ${RUN_USER} /home/${RUN_USER}/.hermes
curl -fsSL --retry 3 -o /tmp/config.tar.gz "${CONFIG_URL}"
tar -xzf /tmp/config.tar.gz -C /home/${RUN_USER}/.hermes
rm -f /tmp/config.tar.gz
chown -R ${RUN_USER}:${RUN_USER} /home/${RUN_USER}/.hermes
chmod 600 /home/${RUN_USER}/.hermes/.env

cat > /etc/systemd/system/${SERVICE}.service <<'UNIT_EOF'
[Unit]
Description=Hermes Chatwoot gateway (CRWD)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=HERMES_HOME=/home/${RUN_USER}/.hermes
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/hermes gateway run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT_EOF

systemctl daemon-reload
systemctl enable ${SERVICE} >/dev/null 2>&1
systemctl restart ${SERVICE}
sleep 8
systemctl is-active ${SERVICE} || true
REMOTE

# --- verify ----------------------------------------------------------------
say "Status"
ssm_run "hermes gateway: verify" 180 <<REMOTE
set -uo pipefail
echo "--- service ---"
systemctl is-active ${SERVICE}; systemctl show -p NRestarts --value ${SERVICE} | sed 's/^/restarts=/'
echo "--- webhook listener (8647) ---"
(ss -lntp | grep -q ':8647 ' && echo "LISTENING on 8647") || echo "NOT LISTENING on 8647"
echo "--- crwd skills seeded ---"
ls /home/${RUN_USER}/.hermes/skills/crwd 2>/dev/null | head -8 || echo "no crwd skills yet"
echo "--- mongo reachable ---"
(timeout 5 bash -c 'cat < /dev/null > /dev/tcp/3.15.164.188/27017' 2>/dev/null && echo "mongo OK") || echo "MONGO UNREACHABLE"
echo "--- last logs ---"
journalctl -u ${SERVICE} -n 25 --no-pager 2>&1 | tail -25
REMOTE

cat <<EOF

Deployed ${GIT_SHA} natively as ${SERVICE}.

  Logs:    aws ssm send-command --region ${AWS_REGION} --instance-ids ${INSTANCE_ID} \\
             --document-name AWS-RunShellScript \\
             --parameters 'commands=["journalctl -u ${SERVICE} -n 50 --no-pager"]'
  Restart: same, with 'systemctl restart ${SERVICE}'
  Config:  ./scripts/deploy-native.sh --config-only

Chatwoot is on this same box, so point the Agent Bot webhook at:
  http://127.0.0.1:8647/chatwoot/webhook?token=<CHATWOOT_WEBHOOK_SECRET>

Now send a real message in a Chatwoot conversation to confirm the bot replies.
EOF
