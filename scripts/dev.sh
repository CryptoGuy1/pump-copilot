#!/usr/bin/env bash
# make dev: the database, one replay worker, the API (127.0.0.1:8000) and the web dev server
# (http://127.0.0.1:5173). Ctrl-C stops all of them.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.venv/bin:$PATH"

# The API (8000) and the web dev server (5173) need their ports. If something already listens on
# one (often an earlier `make dev`), say what it is and how to stop it, and start nothing.
busy=0
for port in 8000 5173; do
  pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  for pid in $pids; do
    # the command, each part shortened to its file name: "Python pumpcopilot api", "node vite"
    name=$(ps -o command= -p "$pid" 2>/dev/null \
           | awk '{for (i = 1; i <= NF; i++) { n = split($i, a, "/"); $i = a[n] } print}')
    echo "port $port is in use by process $pid: $name" >&2
    echo "  stop it with Ctrl-C in the terminal that started it, or: kill $pid" >&2
    busy=1
  done
done
if [ "$busy" = 1 ]; then
  echo "make dev: not started (ports 8000 and 5173 must be free)" >&2
  exit 1
fi

docker compose up -d db
until docker compose exec -T db pg_isready -U pump -d pumpcopilot >/dev/null 2>&1; do sleep 1; done
pumpcopilot db migrate
rows=$(python -c "from pumpcopilot import db
with db.connect() as c: print(c.execute('SELECT count(*) FROM readings').fetchone()[0])")
if [ "$rows" = "0" ]; then
  echo "note: the database has no CIRA data yet: run 'pumpcopilot acquire' and"
  echo "      'pumpcopilot db load cira' (raw data is not in the repository)"
fi
[ -d web/node_modules ] || (cd web && npm ci)

pids=()
cleanup() {
  trap - EXIT INT TERM
  kill "${pids[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM
pumpcopilot worker & pids+=($!)
pumpcopilot api & pids+=($!)  # the API loads .env (ANTHROPIC_API_KEY) itself
(cd web && exec npm run dev) & pids+=($!)
echo "web: http://127.0.0.1:5173   api: http://127.0.0.1:8000/docs   (Ctrl-C to stop)"
# bash 3.2 (macOS) has no `wait -n`: stop everything as soon as one of them exits
while true; do
  for p in "${pids[@]}"; do
    if ! kill -0 "$p" 2>/dev/null; then
      echo "a process exited; stopping the rest"
      exit 1
    fi
  done
  sleep 1
done
