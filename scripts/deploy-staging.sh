#!/usr/bin/env bash
#
# deploy-staging.sh — build the gateway image on the staging EC2, push it to
# ECR, and run it there with docker compose.
#
#   ./scripts/deploy-staging.sh                  # full deploy
#   ./scripts/deploy-staging.sh --config-only    # resync ~/.hermes + restart
#
# Why the build runs on the box: the image is heavy (npm + two frontend
# builds + Playwright + `uv sync --extra all`) and the box is native amd64.
# Building on an arm64 laptop means QEMU emulation and a multi-GB push over a
# home uplink; the box builds natively and pushes to ECR in-region.
#
# Required: SSH access to the box, and local AWS creds that can push to ECR.
# The box needs NO AWS credentials — the ECR login is piped over SSH.
#
set -euo pipefail

EC2_HOST="${EC2_HOST:-3.12.225.78}"
EC2_USER="${EC2_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-}"                       # e.g. ~/.ssh/icrwd-staging.pem
AWS_REGION="${AWS_REGION:-us-east-2}"
ECR_REPO="${ECR_REPO:-hermes-agent}"
REMOTE_DIR="${REMOTE_DIR:-/home/${EC2_USER}/hermes-agent}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"
GIT_SHA="$(git rev-parse HEAD)"
COMPOSE_FILE="docker-compose.staging.yml"

# Plain `[ x ] && y` would abort the script under `set -e` whenever the test is
# false, so these stay as if-statements.
CONFIG_ONLY=0
if [ "${1:-}" = "--config-only" ]; then CONFIG_ONLY=1; fi

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [ -n "$SSH_KEY" ]; then SSH_OPTS+=(-i "$SSH_KEY"); fi
REMOTE="${EC2_USER}@${EC2_HOST}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
on_box() { ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"; }

# --- preflight -------------------------------------------------------------
say "Preflight"
cd "$(dirname "$0")/.."
[ -f Dockerfile ] || die "not at the repo root"
[ -f "$COMPOSE_FILE" ] || die "$COMPOSE_FILE missing"
[ -f "$HOME/.hermes/.env" ] || die "~/.hermes/.env not found — nothing to sync"
[ -f "$HOME/.hermes/config.yaml" ] || die "~/.hermes/config.yaml not found (carries model.default + bedrock.region)"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)" \
  || die "no working AWS credentials"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_IMAGE="${REGISTRY}/${ECR_REPO}"

on_box true || die "cannot SSH to ${REMOTE} — set EC2_USER / SSH_KEY"
on_box "docker info >/dev/null 2>&1" \
  || die "docker not usable by ${EC2_USER} on the box (add the user to the 'docker' group?)"
echo "account=${ACCOUNT_ID} image=${ECR_IMAGE}:${IMAGE_TAG} box=${REMOTE}"

if [ "$CONFIG_ONLY" -eq 0 ]; then
  # Build needs ~20GB; warn early rather than dying 20 minutes in.
  AVAIL_GB="$(on_box "df -BG --output=avail / | tail -1 | tr -dc '0-9'")"
  if [ "${AVAIL_GB:-0}" -lt 20 ]; then
    echo "WARNING: only ${AVAIL_GB}GB free on / — the build may fail"
  fi

  # --- ECR repo (idempotent) ----------------------------------------------
  say "Ensuring ECR repo ${ECR_REPO}"
  aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$ECR_REPO" >/dev/null 2>&1 \
    || aws ecr create-repository --region "$AWS_REGION" --repository-name "$ECR_REPO" \
         --image-scanning-configuration scanOnPush=true >/dev/null

  # --- source -> box -------------------------------------------------------
  # rsync rather than `git clone` on the box: no GitHub credentials needed
  # there, and the tree is already identical to the pushed branch.
  say "Syncing source to ${REMOTE_DIR}"
  on_box "mkdir -p '${REMOTE_DIR}'"
  rsync -az --delete \
    --exclude '.git' --exclude 'node_modules' --exclude '**/node_modules' \
    --exclude '.venv' --exclude '__pycache__' --exclude '**/__pycache__' \
    -e "ssh ${SSH_OPTS[*]}" \
    ./ "${REMOTE}:${REMOTE_DIR}/"

  # --- ECR login on the box, using LOCAL creds ------------------------------
  say "Logging the box into ECR"
  aws ecr get-login-password --region "$AWS_REGION" \
    | on_box "docker login --username AWS --password-stdin '${REGISTRY}'"

  # --- build + push --------------------------------------------------------
  say "Building on the box (this is the slow part)"
  on_box "cd '${REMOTE_DIR}' && docker build \
    --build-arg HERMES_GIT_SHA='${GIT_SHA}' \
    -t '${ECR_IMAGE}:${IMAGE_TAG}' -t '${ECR_IMAGE}:latest' ."

  say "Pushing to ECR"
  on_box "docker push '${ECR_IMAGE}:${IMAGE_TAG}' && docker push '${ECR_IMAGE}:latest'"
fi

# --- config sync -----------------------------------------------------------
# ONLY .env and config.yaml. Never skills/: they self-seed from the image on
# startup, and sync_skills() skips anything it detects as user-modified, so
# copying a local skills/ would pin staging to local versions of the skills.
say "Syncing ~/.hermes config (.env + config.yaml only)"
on_box "mkdir -p ~/.hermes"
rsync -az -e "ssh ${SSH_OPTS[*]}" \
  "$HOME/.hermes/.env" "$HOME/.hermes/config.yaml" "${REMOTE}:.hermes/"
on_box "chmod 600 ~/.hermes/.env"

# --- deploy ----------------------------------------------------------------
say "Deploying gateway"
scp "${SSH_OPTS[@]}" -q "$COMPOSE_FILE" "${REMOTE}:${REMOTE_DIR}/${COMPOSE_FILE}"
on_box "cd '${REMOTE_DIR}' && ECR_IMAGE='${ECR_IMAGE}' IMAGE_TAG='${IMAGE_TAG}' \
  HERMES_UID=\$(id -u) HERMES_GID=\$(id -g) \
  docker compose -f '${COMPOSE_FILE}' up -d --pull always"

# --- report ----------------------------------------------------------------
say "Status"
on_box "docker ps --filter name=hermes --format '{{.Names}}  {{.Status}}  {{.Image}}'"

# Bedrock resolves via IMDS (instance role) or keys in .env; say which.
if on_box "curl -sf -m 2 -X PUT http://169.254.169.254/latest/api/token \
     -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' >/dev/null 2>&1 && \
   TOKEN=\$(curl -s -X PUT http://169.254.169.254/latest/api/token \
     -H 'X-aws-ec2-metadata-token-ttl-seconds: 60') && \
   curl -sf -m 2 -H \"X-aws-ec2-metadata-token: \$TOKEN\" \
     http://169.254.169.254/latest/meta-data/iam/security-credentials/ >/dev/null 2>&1"; then
  echo "Bedrock: instance role present — boto3 will use it."
else
  echo "Bedrock: NO instance role. Add AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY to ~/.hermes/.env on the box, then re-run with --config-only."
fi

say "Logs (last 40 lines)"
on_box "docker logs --tail 40 hermes 2>&1" || true

cat <<EOF

Deployed ${ECR_IMAGE}:${IMAGE_TAG}

  Follow logs:  ssh ${SSH_OPTS[*]} ${REMOTE} 'docker logs -f hermes'
  Verify skills: ssh ${SSH_OPTS[*]} ${REMOTE} 'docker exec hermes ls /opt/data/skills/crwd'
  Rollback:      IMAGE_TAG=<older-sha> ./scripts/deploy-staging.sh --config-only

Now send a real message in a Chatwoot conversation to confirm the bot replies.
EOF
