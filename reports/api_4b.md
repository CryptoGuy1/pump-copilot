# Step 4b: API and live stream

`pumpcopilot api` serves FastAPI on 127.0.0.1 only (there is no `--host` option), with no
auth. CORS allows the local frontend dev servers (ports 5173 and 3000 on localhost). The
contract is in `api/openapi.json`, and a test fails if the app's schema differs from it.
Measured on 2026-09-25 against the local Timescale database.

## Screens and endpoints

| screen | endpoints |
|---|---|
| health | `GET /api/health`: database (latency, pending migrations) and workers (heartbeats, alive if seen in the last 10 s) |
| fleet overview | `GET /api/fleet`: per pump, its state (one of the four presentation states), open cases, data quality, and real or synthetic data |
| asset-day | `GET /api/assets`, `…/days/{day}`, `…/signals?resolution=1m\|raw`, `…/segments`, `…/scores`, `…/bands` |
| cases | `GET /api/cases` (`real_count` and `synthetic_count`, plus a flag on each case), `GET /api/cases/{id}` (timeline, per-signal summary, downsampled band chart, related cases, allowed actions), `GET /api/cases/{id}/evidence?offset&limit&signal` (every evidence window, paginated), `POST …/acknowledge\|notes\|disposition\|close`, `GET …/export?format=json\|markdown` |
| replay control | `GET\|POST /api/replay/sessions`, `POST …/{id}/start\|pause\|rewind`, `PUT …/{id}/speed`, `GET …/{id}/baseline`, `GET /api/replay/scenarios` |
| evaluation | `GET /api/evaluation` (3a, 3a-2, 3a-3 results and cases for every mode), `GET /api/assumptions` (the register as data) |
| data quality | `GET /api/data-quality`, `GET /api/assets/{a}/days/{d}/data-quality` (audit findings, stale and spike counts, gaps) |
| live stream | `GET /api/stream`: server-sent events `replay.progress`, `score.batch`, `case.event` |

## Rules for every endpoint

- **Scoping.** Telemetry, readings and segments are read only through `db.QUERY_FUNCTIONS`,
  all scoped by (asset_id, source_day), and a test checks the API source for direct table
  access. Raw resolution needs a window of at most 3 h.
- **Provenance.** Responses carrying scores or cases include `synthetic`, `model_version`
  (the distinct versions) and `assumptions`. A case list can mix real and synthetic cases,
  so it reports `real_count` and `synthetic_count` instead of one flag, and each case keeps
  its own `synthetic`. A1, A3 and A5 always apply. A2 applies for
  pressure and casing temperature, A6 for B 2024-10-30, and A7 and A8 wherever there are
  scores.
- **Case actions.** They use the 4a rules, enforced in the database. An invalid transition
  returns 409 `invalid_transition`; a missing reason or an unknown disposition returns 422.
- **Errors.** Every error has the same shape, `{"error": {"status", "code", "message",
  "details"}}`, including 404s, validation errors and 500s.
- **Export.** JSON, or a Markdown evidence pack. The pack has a SYNTHETIC banner where it
  applies, the assumptions, the actions and notes, and the evidence table. Escalation stays a
  disposition plus an export; nothing is sent.

## Guardrails

- **Database role.** The API connects as `pumpcopilot_api` (migration 0008). It can read
  everything and write only replay control, case events and stream events. Writing
  telemetry, readings, segments or scores fails with a permission error (tested).
- **Route sweep.** A test calls every route with outbound sockets patched to fail. Telemetry,
  readings and segments are unchanged afterwards (row counts and md5), no outbound
  connection was attempted, and the escalation only recorded a disposition.
- **Static check.** The API, cases and events modules import no network, mail or process
  libraries, and no route path mentions escalate, notify, email, webhook or send.

## Case detail

The case detail no longer carries every evidence window. It has:

- the timeline of human and worker actions (not the per-window evidence events);
- per signal, a summary (windows, episodes, first and last window, highest score), the
  signal's baseline band, and a band chart;
- the band chart is downsampled to at most `max_points` time bins (default 100, from 2 to
  1000). It covers the case plus 30 min on either side. Each bin gives the median, min and
  max of the window medians, and whether any window in it was reviewed. The series are
  columnar arrays.

Every evidence window is on `GET /api/cases/{id}/evidence`, 100 per page by default (at most
500), with `total` and `next_offset`.

## Live stream

- **Event log.** Events go to the `stream_events` table, and NOTIFY wakes listeners. Ids are
  taken under a transaction-level advisory lock, so they become visible in commit order. A
  test shows that a writer which took its id later waits for the earlier commit.
- **Resuming.** `Last-Event-ID` (or `?last_event_id=` on a first connection) resumes without
  gaps. That is tested over a real socket: two events read, connection dropped, three more
  emitted, then reconnect with exactly those three delivered.
- **Test/diagnostic parameters.** `limit` (1–10,000; close after that many events) and
  `poll_s` (0.1–60 s, default 15; how long to wait for a NOTIFY before re-checking the log
  and sending a keep-alive) are bounded, and documented as test/diagnostic parameters in the
  schema. Browsers omit both.
- **Who emits.** The worker emits `replay.progress` (with baseline status counts) each time
  it scores, `score.batch` for stored scores, and one `case.event` per case it touches. Case
  actions and replay control emit from the API or CLI, in the same transaction as the change.

## Performance (REAL data, in-process, 100 requests after 5 warm-ups)

Measured after `db reset`, reload and replay (2026-09-28), with an explicit VACUUM ANALYZE
first; the run just before it overlapped an autovacuum.

| request | p50 | p95 | max | size |
|---|---|---|---|---|
| fleet overview (3 pumps) | 43.5 ms | 75.0 ms | 118.3 ms | 2.5 KiB |
| asset-day B 2024-10-30, all 10 signals at 1 min | 88.7 ms | 121.6 ms | 154.6 ms | 587 KiB |
| case detail, B October case (917 evidence windows) | 32.3 ms | 49.7 ms | 85.8 ms | **46 KiB** |
| case detail, A October case (1 evidence window) | 13.5 ms | 23.4 ms | 38.4 ms | 4.2 KiB |
| case evidence, B October case, one page (100 of 917) | 16.7 ms | 22.6 ms | 30.2 ms | 32 KiB |

Before the split, the B October case detail was 999 KiB with a p95 of 105.5 ms. The asset-day
p95 was 72.6 ms in the run before the reset; the database query is 5 ms of it (EXPLAIN
ANALYZE). The rest is building and serialising 587 KiB, which varies between runs. These are
measured through the ASGI test client, so they don't include HTTP transport.

## Re-run after `db reset` (2026-09-28)

`pumpcopilot db reset --yes-i-mean-it` dropped and recreated the database, and
`db load cira` reloaded it. The reload matched the counts from before the reset: 1,449,270
telemetry rows, 63,238 readings and 47 segments. The compression policy was run once by hand
(`CALL run_job`), compressing 3 of 3 chunks to 108.1 MB. Three sessions then replayed at 60x
with three workers:

| session | day | stored / batch 3a-3 | identical | cases |
|---|---|---|---|---|
| 1 | B 2024-10-30 | 2752 / 2752 | yes | 1 (09:08–15:10, 36 episodes) |
| 2 | A 2024-10-30 | 677 / 677 | yes | 2 |
| 3 | B 2024-10-30, SYNTHETIC B_stuck_pressure | 2752 / 2752, all synthetic | yes | 2 |

Timescale telemetry is off on the server (`SHOW timescaledb.telemetry_level` gives `off`;
committed in 5b82ced).
