#!/usr/bin/env bash
# The end-to-end backend: its own database (pumpcopilot_e2e, created and loaded once, then
# reused), one replay worker and the API on 127.0.0.1:8001. Never touches the dev database.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$PWD/.venv/bin:$PATH"
base="${DATABASE_URL:-postgresql://pump:${POSTGRES_PASSWORD:-pump_dev_only}@localhost:5432/pumpcopilot}"
export DATABASE_URL="${base%/*}/pumpcopilot_e2e"
unset ANTHROPIC_API_KEY  # the end-to-end test never calls a real model

state=$(python - <<'PY'
import psycopg
from pumpcopilot import db
try:
    with db.connect() as c:
        print("ready" if c.execute("SELECT count(*) FROM readings").fetchone()[0] else "empty")
except psycopg.Error:
    print("missing")
PY
)
if [ "$state" != "ready" ]; then
  pumpcopilot db reset --yes-i-mean-it
  pumpcopilot db load cira
fi
pumpcopilot db migrate
# leftovers from an interrupted run must not compete with this one's session
python - <<'PY'
from pumpcopilot import db
with db.connect() as c:
    c.execute("UPDATE replay_sessions SET status = 'paused' WHERE status IN ('pending', 'running')")
PY

pumpcopilot worker --poll 0.05 &
worker=$!
trap 'kill $worker 2>/dev/null || true' EXIT INT TERM
pumpcopilot api --port 8001 --no-dotenv
