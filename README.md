# Industrial Asset Intelligence and Operations Copilot

Read-only decision support prototype for a refinery-style pump service. Portfolio demonstration
on public data; no refinery data, no plant alarms, no control actions.

**Status: Step 2b of the build (CIRA loaded into Timescale).**

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                          # 72 tests on synthetic fixtures; 7 need the db (skipped if down)
pumpcopilot acquire             # downloads ZeMA (UCI) and CIRA (Zenodo API) into data/raw/
pumpcopilot audit zema          # -> reports/zema_audit.json
pumpcopilot audit cira          # -> reports/cira_audit.json
pumpcopilot rules cira          # -> data/operating_rules.yaml, reports/cira_operating_rules.md
pumpcopilot state cira          # -> reports/cira_state.json (state time, changes, stale/spike)
docker compose up -d db         # Timescale; DATABASE_URL overrides the local default
pumpcopilot db migrate          # applies migrations/NNNN_*.sql in order
pumpcopilot db load cira        # idempotent: a second run inserts 0 rows
pumpcopilot db perf             # -> reports/db_perf.json
```

## Step 1 exit criteria (scope Week 1 stop rule)
- ZeMA: every channel is 2,205 x (Hz x 60); five label columns within documented values.
- ZeMA: `split_preflight` shows whether an ordered split with gaps keeps all leakage classes.
- CIRA: 8 files, only C / 2024-10-30 absent; cadence, gaps, duplicates and placeholders measured.
- CIRA: record version and license printed by `acquire` match the scope, or are escalated.
- `data/cira_columns.yaml` written from the audit headers and the data descriptor.

## Layout
```
src/pumpcopilot/schema.py      canonical contract; scope guardrails enforced as validation
src/pumpcopilot/provenance.py  file digests and deterministic record hashes (idempotency keys)
src/pumpcopilot/acquire.py     manifest-driven download, hash verify, lock file
src/pumpcopilot/zema.py        ZeMA loader, audit, confounding tables, split preflight
src/pumpcopilot/cira.py        CIRA per-file audit, canonical event conversion
src/pumpcopilot/operating.py   CIRA operating state rules, stale/spike flags, state report
src/pumpcopilot/db.py          migration runner, COPY + ON CONFLICT loader, per-asset-day queries
migrations/                    numbered SQL: telemetry/readings hypertables, segments, runs, 1-min view
docs/adr/                      decisions that adjust the original scope
docs/ASSUMPTIONS.md            assumptions register: evidence, impact if wrong, how to revisit
```

## Data
Raw data is never committed. See `data/manifest.yaml` for sources, citations and license status.
CIRA is CC BY 4.0 (from Zenodo metadata, retrieved 2026-09-24; the data descriptor states the
same). We use Zenodo record 18479728, version 2 of 2. Any reuse must credit the authors
(citation in the manifest). Assumptions about the data that are not yet confirmed (timezone,
column units, version choice) are tracked in [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).
