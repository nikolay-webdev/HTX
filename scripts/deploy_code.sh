#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -f deploy/vps.env ]]; then
  # shellcheck disable=SC1091
  source deploy/vps.env
fi

HOST="${1:-${HTX_VPS_HOST:-}}"
VPS_PATH="${HTX_VPS_PATH:-/srv/bots/htx}"
SERVICE="${HTX_SERVICE:-htx-bot}"
REMOTE="${HTX_REMOTE:-origin}"
BRANCH="${HTX_BRANCH:-$(git branch --show-current)}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_SCRIPT="/tmp/htx_deploy_code.$STAMP.sh"

if [[ -z "$HOST" ]]; then
  echo "Usage: scripts/deploy_code.sh nikoshot@SERVER_IP"
  echo "Or copy deploy/vps.env.example to deploy/vps.env and set HTX_VPS_HOST."
  exit 2
fi

if [[ -z "$BRANCH" ]]; then
  echo "Cannot detect current Git branch."
  exit 2
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "There are uncommitted local changes. Commit them before deploy:"
  git status --short
  exit 1
fi

echo "Running local tests..."
python3 -m unittest discover -s tests -v

echo "Pushing $BRANCH to $REMOTE..."
git push "$REMOTE" "$BRANCH"

LOCAL_REMOTE_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/htx_deploy_code.XXXXXX")"
trap 'rm -f "$LOCAL_REMOTE_SCRIPT"' EXIT
chmod 600 "$LOCAL_REMOTE_SCRIPT"
cat > "$LOCAL_REMOTE_SCRIPT" <<'REMOTE_SCRIPT_BODY'
set -euo pipefail
trap 'rm -f "$HTX_REMOTE_SCRIPT"' EXIT

cd "$HTX_VPS_PATH"

git fetch "$HTX_REMOTE" "$HTX_BRANCH"
if git show-ref --verify --quiet "refs/heads/$HTX_BRANCH"; then
  git checkout "$HTX_BRANCH"
else
  git checkout -b "$HTX_BRANCH" "$HTX_REMOTE/$HTX_BRANCH"
fi
git pull --ff-only "$HTX_REMOTE" "$HTX_BRANCH"

mkdir -p data
chmod 700 data

python3 -m unittest discover -s tests -v

sudo -v
sudo cp deploy/htx-bot.service /etc/systemd/system/htx-bot.service
sudo systemctl daemon-reload
sudo systemctl enable "$HTX_SERVICE"
sudo systemctl restart "$HTX_SERVICE"
sudo systemctl --no-pager --full status "$HTX_SERVICE"
REMOTE_SCRIPT_BODY

echo "Deploying on $HOST..."
scp "$LOCAL_REMOTE_SCRIPT" "$HOST:$REMOTE_SCRIPT"
ssh -t "$HOST" \
  "HTX_VPS_PATH=$(printf '%q' "$VPS_PATH") HTX_SERVICE=$(printf '%q' "$SERVICE") HTX_REMOTE=$(printf '%q' "$REMOTE") HTX_BRANCH=$(printf '%q' "$BRANCH") HTX_REMOTE_SCRIPT=$(printf '%q' "$REMOTE_SCRIPT") bash $(printf '%q' "$REMOTE_SCRIPT")"

echo "Deploy finished."
