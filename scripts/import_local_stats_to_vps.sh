#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -f deploy/vps.env ]]; then
  # shellcheck disable=SC1091
  source deploy/vps.env
fi

HOST="${HTX_VPS_HOST:-}"
YES=false
VPS_PATH="${HTX_VPS_PATH:-/srv/bots/htx}"
SERVICE="${HTX_SERVICE:-htx-bot}"
OWNER="${HTX_VPS_OWNER:-nikoshot:nikoshot}"
LOCAL_DB="${HTX_LOCAL_DB:-data/eth_live_simulator.db}"
VPS_DB="${HTX_VPS_DB:-$VPS_PATH/data/eth_live_simulator.db}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOCAL_TMP="/tmp/eth_live_simulator.import.$STAMP.db"
REMOTE_TMP="/tmp/eth_live_simulator.import.$STAMP.db"

for arg in "$@"; do
  case "$arg" in
    --yes)
      YES=true
      ;;
    *)
      if [[ -z "$HOST" ]]; then
        HOST="$arg"
      else
        echo "Unexpected argument: $arg"
        exit 2
      fi
      ;;
  esac
done

if [[ -z "$HOST" ]]; then
  echo "Usage: scripts/import_local_stats_to_vps.sh nikoshot@SERVER_IP --yes"
  echo "This replaces the VPS SQLite database with the local database."
  exit 2
fi

if [[ "$YES" != true ]]; then
  echo "Refusing to replace the VPS database without --yes."
  echo "Usage: scripts/import_local_stats_to_vps.sh $HOST --yes"
  exit 2
fi

if [[ ! -f "$LOCAL_DB" ]]; then
  echo "Local database not found: $LOCAL_DB"
  exit 1
fi

echo "Creating a consistent local SQLite backup..."
python3 - "$LOCAL_DB" "$LOCAL_TMP" <<'PY'
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

python3 - "$LOCAL_TMP" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
connection = sqlite3.connect(db_path)
trades, profit = connection.execute(
    "SELECT COUNT(*), COALESCE(SUM(net_profit), 0) FROM trades"
).fetchone()
positions = connection.execute("SELECT COUNT(*) FROM position").fetchone()[0]
connection.close()
print(f"Local backup: trades={trades}, profit={profit:.8f}, positions={positions}")
PY

echo "Uploading database backup to $HOST..."
scp "$LOCAL_TMP" "$HOST:$REMOTE_TMP"

echo "Replacing VPS database. A VPS backup will be kept in $VPS_PATH/backups."
ssh -tt "$HOST" \
  "HTX_VPS_PATH=$(printf '%q' "$VPS_PATH") HTX_SERVICE=$(printf '%q' "$SERVICE") HTX_OWNER=$(printf '%q' "$OWNER") HTX_VPS_DB=$(printf '%q' "$VPS_DB") HTX_REMOTE_TMP=$(printf '%q' "$REMOTE_TMP") HTX_STAMP=$(printf '%q' "$STAMP") bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

cd "$HTX_VPS_PATH"
sudo -v
sudo systemctl stop "$HTX_SERVICE"

mkdir -p data backups
if [[ -f "$HTX_VPS_DB" ]]; then
  cp "$HTX_VPS_DB" "backups/eth_live_simulator.before-import.$HTX_STAMP.db"
fi

cp "$HTX_REMOTE_TMP" "$HTX_VPS_DB"
sudo chown "$HTX_OWNER" "$HTX_VPS_DB"
chmod 600 "$HTX_VPS_DB"

python3 - "$HTX_VPS_DB" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
connection = sqlite3.connect(db_path)
trades, profit = connection.execute(
    "SELECT COUNT(*), COALESCE(SUM(net_profit), 0) FROM trades"
).fetchone()
positions = connection.execute("SELECT COUNT(*) FROM position").fetchone()[0]
connection.close()
print(f"VPS database: trades={trades}, profit={profit:.8f}, positions={positions}")
PY

sudo systemctl start "$HTX_SERVICE"
sudo systemctl --no-pager --full status "$HTX_SERVICE"
rm -f "$HTX_REMOTE_TMP"
REMOTE_SCRIPT

echo "Import finished."
