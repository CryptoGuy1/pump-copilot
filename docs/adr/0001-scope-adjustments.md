# ADR-0001: Adjustments to the project scope

Status: proposed, 24 Sep 2026. Keeps the scope's claim boundaries intact; changes how we get there.

## 1. Modular monolith, not five services
One Python package, two processes (API + replay/scoring worker), Postgres/Timescale. Replay feeds
the worker through a Postgres table used as an outbox/queue (`SELECT ... FOR UPDATE SKIP LOCKED`),
which gives buffering during outages and idempotency via `provenance_hash` without adding a broker.
Module boundaries stay clean so a broker (MQTT/Event Hubs) can replace the outbox later.
Reason: ~80 hours total budget; five services would spend a large share on plumbing.

## 2. Two storage shapes
CIRA and all streamed events: narrow `TelemetryEvent` rows (Timescale hypertable).
ZeMA: one `CycleRecord` per 60 s cycle, arrays in Parquet. Exploding ZeMA into narrow rows
would be ~96M rows (2,205 cycles x ~43.7k values) with no analytical benefit.

## 3. ZeMA: make the split study the headline, not the F1
Published work on this rig commonly reports near-perfect pump leakage accuracy under random
splits. A macro-F1 gate of 0.80 is therefore not informative. The interesting, honest result is
the gap between a random split and a chronology/block-respecting split, and performance
stratified by the other four conditions. The preflight in `zema.ordered_split_feasibility`
decides which split is possible before any model is trained.
Verify from the rig documentation which pump type is fitted; a hydraulic rig pump is likely
not centrifugal, which further supports keeping it on a separate benchmark page.

## 4. CIRA: add a clearly labeled synthetic fault-injection track
The CIRA data descriptor describes normal operating conditions, so the anomaly detector has
nothing measurable to be evaluated on. Inject parametrised synthetic deviations (step, ramp,
sensor stuck, drift, dropout) into a held-out CIRA day, labelled `synthetic_injection` in
provenance, and report detection delay and false review rate per asset-day. This measures the
pipeline and persistence rule, not real fault detection, and is reported that way.
Also normalise temperatures against ambient before baselining; April vs June vs October
differences will otherwise dominate every "anomaly".

## 5. Evaluate a labeled real centrifugal pump dataset as an optional third source
The 4TU / Tata Steel dataset (vibration + motor current, 20 faults with severities, on a real
motor-driven centrifugal pump) is much closer to the target domain than ZeMA. Check its license
and size in Week 1; if suitable, it can become the centrifugal fault benchmark in a later phase.
Same separation rule: it never scores CIRA.

## 6. API contract and a walking-skeleton UI early; polish late
Generate the OpenAPI contract in step 3 and a thin React page in step 4, so the final
frontend is built against a stable, tested API instead of discovering gaps at the end.
Frontend target: Vite + React + TypeScript, TanStack Query, a typed client generated from
OpenAPI, SSE for live updates, uPlot or ECharts for dense time series. Streamlit is dropped.

## 7. Case log is append-only
Case state is derived from an append-only event log (opened, acknowledged, note, disposition,
exported). This gives the audit trail and "no duplicate cases" for free.
