#!/usr/bin/env bash
# The static snapshot's source state, from scratch: reset a dedicated local database, load the
# real CIRA data, and replay three sessions to completion (sessions 1-3, in this order):
#   1  pump B, 30 October 2024 (real)
#   2  pump A, 30 October 2024 (real)
#   3  pump B, 30 October 2024, scenario B_stuck_pressure (SYNTHETIC)
# Then each session is verified against batch scoring. The development database is not
# touched: SNAPSHOT_DATABASE_URL defaults to the local "pumpcopilot_snapshot" database.
set -eu
cd "$(dirname "$0")/.."

export DATABASE_URL="${SNAPSHOT_DATABASE_URL:-postgresql://pump:${POSTGRES_PASSWORD:-pump_dev_only}@localhost:5432/pumpcopilot_snapshot}"

pumpcopilot db reset --yes-i-mean-it
pumpcopilot db load cira
pumpcopilot replay create cira-pump-B 2024-10-30 --speed 60
pumpcopilot replay create cira-pump-A 2024-10-30 --speed 60
pumpcopilot replay create cira-pump-B 2024-10-30 --speed 60 --scenario B_stuck_pressure
pumpcopilot worker --until-idle --max-seconds 3600
for s in 1 2 3; do
  pumpcopilot replay verify "$s"
done
