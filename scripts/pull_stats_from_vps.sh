#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -f deploy/vps.env ]]; then
  # shellcheck disable=SC1091
  source deploy/vps.env
fi

HOST="${1:-${HTX_VPS_HOST:-}}"
VPS_PATH="${HTX_VPS_PATH:-/srv/bots/htx}"
LOCAL_DB="${HTX_LOCAL_DB:-data/eth_live_simulator.db}"
VPS_DB="${HTX_VPS_DB:-$VPS_PATH/data/eth_live_simulator.db}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_TMP="/tmp/eth_live_simulator.pull.$STAMP.db"

if [[ -z "$HOST" ]]; then
  echo "Usage: scripts/pull_stats_from_vps.sh nikoshot@SERVER_IP"
  echo "Or copy deploy/vps.env.example to deploy/vps.env and set HTX_VPS_HOST."
  exit 2
fi

mkdir -p data backups
if [[ -f "$LOCAL_DB" ]]; then
  cp "$LOCAL_DB" "backups/eth_live_simulator.before-pull.$STAMP.db"
fi

echo "Creating a consistent SQLite backup on VPS..."
ssh "$HOST" \
  "HTX_VPS_DB=$(printf '%q' "$VPS_DB") HTX_REMOTE_TMP=$(printf '%q' "$REMOTE_TMP") bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

python3 - "$HTX_VPS_DB" "$HTX_REMOTE_TMP" <<'PY'
import sqlite3
import sys

source_path, backup_path = sys.argv[1], sys.argv[2]
source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
target = sqlite3.connect(backup_path)
with target:
    source.backup(target)
target.close()
source.close()
PY
REMOTE_SCRIPT

echo "Downloading VPS database to $LOCAL_DB..."
scp "$HOST:$REMOTE_TMP" "$LOCAL_DB"
ssh "$HOST" "rm -f $(printf '%q' "$REMOTE_TMP")"
chmod 600 "$LOCAL_DB"

python3 - "$LOCAL_DB" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
connection = sqlite3.connect(db_path)
trades, profit = connection.execute(
    "SELECT COUNT(*), COALESCE(SUM(net_profit), 0) FROM trades"
).fetchone()
positions = connection.execute("SELECT COUNT(*) FROM position").fetchone()[0]
connection.close()
print(f"Local database: trades={trades}, profit={profit:.8f}, positions={positions}")
PY

echo "Pull finished."
