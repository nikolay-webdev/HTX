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
LOCAL_DB="${HTX_LOCAL_DB:-data/eth_live_simulator.db}"
VPS_DB="${HTX_VPS_DB:-$VPS_PATH/data/eth_live_simulator.db}"
LOCAL_HEAD="$(git rev-parse --short HEAD)"
LOCAL_BRANCH="$(git branch --show-current)"

if [[ -z "$HOST" ]]; then
  echo "Usage: scripts/check_vps_sync.sh nikoshot@SERVER_IP"
  echo "Or set HTX_VPS_HOST in deploy/vps.env."
  exit 2
fi

echo "LOCAL"
echo "  branch=$LOCAL_BRANCH"
echo "  head=$LOCAL_HEAD"
echo "  dirty_files=$(git status --short | wc -l | tr -d ' ')"
python3 - "$LOCAL_DB" <<'PY'
import sqlite3
import sys
from pathlib import Path

db_path = Path(sys.argv[1])
if not db_path.exists():
    print(f"  db={db_path} missing")
    raise SystemExit(0)

connection = sqlite3.connect(db_path)
trades, profit = connection.execute(
    "SELECT COUNT(*), COALESCE(SUM(net_profit), 0) FROM trades"
).fetchone()
positions = connection.execute("SELECT COUNT(*) FROM position").fetchone()[0]
last_price_row = connection.execute("SELECT value FROM state WHERE key = 'last_price'").fetchone()
last_candle_row = connection.execute("SELECT value FROM state WHERE key = 'last_candle_ts'").fetchone()
connection.close()
last_price = last_price_row[0] if last_price_row else "none"
last_candle = last_candle_row[0] if last_candle_row else "none"
print(f"  db={db_path}")
print(f"  trades={trades}")
print(f"  profit={profit:.8f}")
print(f"  positions={positions}")
print(f"  last_price={last_price}")
print(f"  last_candle_ts={last_candle}")
PY

echo
echo "VPS"
ssh "$HOST" \
  "HTX_VPS_PATH=$(printf '%q' "$VPS_PATH") HTX_SERVICE=$(printf '%q' "$SERVICE") HTX_VPS_DB=$(printf '%q' "$VPS_DB") bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

cd "$HTX_VPS_PATH"
echo "  branch=$(git branch --show-current)"
echo "  head=$(git rev-parse --short HEAD)"
echo "  dirty_files=$(git status --short | wc -l | tr -d ' ')"

python3 - "$HTX_VPS_DB" <<'PY'
import sqlite3
import sys
from pathlib import Path

db_path = Path(sys.argv[1])
if not db_path.exists():
    print(f"  db={db_path} missing")
    raise SystemExit(0)

connection = sqlite3.connect(db_path)
trades, profit = connection.execute(
    "SELECT COUNT(*), COALESCE(SUM(net_profit), 0) FROM trades"
).fetchone()
positions = connection.execute("SELECT COUNT(*) FROM position").fetchone()[0]
last_price_row = connection.execute("SELECT value FROM state WHERE key = 'last_price'").fetchone()
last_candle_row = connection.execute("SELECT value FROM state WHERE key = 'last_candle_ts'").fetchone()
connection.close()
last_price = last_price_row[0] if last_price_row else "none"
last_candle = last_candle_row[0] if last_candle_row else "none"
print(f"  db={db_path}")
print(f"  trades={trades}")
print(f"  profit={profit:.8f}")
print(f"  positions={positions}")
print(f"  last_price={last_price}")
print(f"  last_candle_ts={last_candle}")
PY

echo "  service_enabled=$(systemctl is-enabled "$HTX_SERVICE" 2>/dev/null || true)"
echo "  service_active=$(systemctl is-active "$HTX_SERVICE" 2>/dev/null || true)"
echo "  service_restart=$(systemctl show "$HTX_SERVICE" -p Restart --value 2>/dev/null || true)"
echo "  service_main_pid=$(systemctl show "$HTX_SERVICE" -p MainPID --value 2>/dev/null || true)"
REMOTE_SCRIPT

echo
echo "EXPECTED"
echo "  Code is synced when LOCAL head equals VPS head and both dirty_files are 0."
echo "  The bot survives terminal close when VPS service_enabled=enabled and service_active=active."
echo "  Server DB is the live source. Run scripts/pull_stats_from_vps.sh when you want the local DB copy refreshed."
