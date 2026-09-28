# Step 5a: bare frontend

`web/` is Vite + React + TypeScript, plain and functional (no design work). It uses
TanStack Query for data and uPlot for time series. `make dev` starts the database, one
worker, the API (127.0.0.1:8000) and the web dev server (127.0.0.1:5173), which proxies
`/api`. Ctrl-C stops all of them (checked).

## Typed client

- `openapi-typescript` generates `web/src/api/schema.d.ts` from `api/openapi.json`, and
  `openapi-fetch` uses it. Paths, parameters, request bodies and responses are all typed.
- `npm run build` runs `check:api` first, and fails if the generated file differs from what
  the committed schema produces (checked by editing it: the build exits 1).
- TypeScript is pinned to 5.x, because openapi-typescript needs the TypeScript 5 compiler
  API and TypeScript 7 does not provide it.

### 5a-2: contract hardening

- **Response models.** Every endpoint declares one (`src/pumpcopilot/api_models.py`), and
  each forbids undeclared fields. `/api/stream` documents its `text/event-stream` payloads as
  a union discriminated on the event type: `StreamEvent`, over `ReplayProgressEvent`,
  `ScoreBatchEvent` and `CaseEvent`. The JSON export reuses the `ScoredEvidence` contract.
- **Web types.** The handwritten `web/src/api/types.ts` is deleted. Screens use the generated
  types directly: `call()` infers them from the client, and `Schema<"CaseDetail">` names one.
  `useStream` types each event's payload from the generated event models. The generated
  types exposed three things the handwritten ones had hidden:
  - a case's `signals` can be null;
  - the signals endpoint returns 1-minute points or raw points depending on resolution;
  - the stored evaluation JSON is free-form.
- **Contract tests.**
  - Every `/api` route declares a model that forbids extra fields.
  - The route sweep on the test database validates each actual response against its model,
    including the case actions and replay controls.
  - A read-only test on the real, reset local database validates every GET endpoint, for
    every asset-day, session and case. It also covers raw resolution, and validates each
    stored stream event against its payload model.

## Screens

| screen | what it shows |
|---|---|
| Fleet | per pump: state, latest replay (SYNTHETIC label), open cases (real and synthetic), data quality, provenance |
| Asset-day | replay session picker (SYNTHETIC label); per scored signal, window medians against the band, with reviewed windows marked and running segments shaded; raw 1-minute signals |
| Cases | real and synthetic counts, a SYNTHETIC label per row, filters |
| Case detail | SYNTHETIC label, provenance, the A8 notice, related cases, actions (errors shown as returned), a summary per signal, band charts, paginated evidence, timeline, export (JSON, Markdown, preview) |
| Replay | the A8 notice; new replay (asset-day, speed, scenario marked SYNTHETIC); start, pause, rewind and speed per session; per-signal baseline progress |
| Evaluation | the three modes, cases for every mode, and the assumptions register |
| Data quality | audit status and issues; readings, stale, spike and gap counts per asset-day |

The live stream (`useStream`) marks the queries each event affects as stale. The header shows
the connection state and the last event id.

## Tests

- **Vitest (7):** the stream hook delivers typed events and tracks the last id; it
  reconnects after an error, resuming with `last_event_id`; it drops repeats; it backs off,
  and resets once connected; it applies filters; and it cleans up on unmount.
- **Playwright end to end (1):** start B_stuck_pressure at 60x, wait for the synthetic case
  to appear live (no reload), acknowledge it, set the escalate disposition with a reason,
  close it, and check the Markdown and JSON exports. It passes in 1.3 min.
  - It runs on its own database (`pumpcopilot_e2e`, loaded once, then reused), with its own
    API (8001) and web server (5174).
  - It also checks that no error alert appears after each action.

## Found by the end-to-end test: a deadlock between the API and the worker

The first run's disposition got a 500 with `DeadlockDetected`.

- **Cause:** the worker's claim locked the session row `FOR UPDATE` for the whole step. A
  case action takes the case's lock (in the transition trigger), and its foreign-key check
  then needs `FOR KEY SHARE` on that session row, so it waited for the step. The step, adding
  evidence to the same case, then waited for the case's lock.
- **Fix:** the claim uses `FOR NO KEY UPDATE SKIP LOCKED`. That still excludes other workers,
  but no longer blocks foreign-key checks. A case action also retries up to three times on a
  deadlock.
- **Regression test:** `test_a_case_action_is_not_blocked_by_a_worker_holding_its_session`.
  It failed with a lock timeout on exactly that `FOR KEY SHARE`, and passes now.

## Timing (measured once per page, cold browser context, over HTTP)

Measured through the web server and the API, on the e2e database. Page load runs from
navigation start to the load event. The first chart is when uPlot first renders.

| page | dev server: load | dev: first chart | production build: load | production: first chart |
|---|---|---|---|---|
| fleet overview | 313 ms | - | 77 ms | - |
| asset-day B 2024-10-30 | 302 ms | 552 ms | 53 ms | 216 ms |
| case detail | 214 ms | 295 ms | 95 ms | 192 ms |

The production bundle is 382 KiB (125 KiB gzipped). Rerun with `make e2e-timing`
(production build) or `cd web && npx playwright test timing` (dev server).
