#!/usr/bin/env bash
# CI (and a local check): the real CIRA data that the real-data contract test needs.
# CIRA is downloaded from Zenodo (CC BY 4.0) into data/raw/, which is never committed or
# redistributed; it is loaded and pump B's 30 October day is replayed once at 60x.
# Uses DATABASE_URL (or the local default).
set -eu
cd "$(dirname "$0")/.."

pumpcopilot acquire --only cira
pumpcopilot db migrate
pumpcopilot db load cira
pumpcopilot replay create cira-pump-B 2024-10-30 --speed 60
pumpcopilot worker --until-idle --max-seconds 1500
pumpcopilot replay verify 1   # the replayed session equals batch 3a-3 scoring, field by field
