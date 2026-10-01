#!/usr/bin/env bash
# CI: start TimescaleDB from docker-compose.yml and wait until it accepts connections over
# TCP at DATABASE_URL (so the image's first-start initialisation has finished).
set -eu
cd "$(dirname "$0")/.."

docker compose up -d --wait db
for _ in $(seq 60); do
  if python -c "import os, psycopg; psycopg.connect(os.environ['DATABASE_URL']).close()" 2>/dev/null; then
    exit 0
  fi
  sleep 2
done
echo "TimescaleDB did not accept connections at DATABASE_URL" >&2
exit 1
