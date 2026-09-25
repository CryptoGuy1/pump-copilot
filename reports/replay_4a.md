# Step 4a: replay worker, persisted scores and cases

Detector: the frozen `revision_3a3` configuration, unchanged. All numbers are from the local
Timescale 2.30.1 database on 2026-09-25. Latency runs used the real wall clock at 60x, with two
`pumpcopilot worker` processes taking turns (claims use `FOR UPDATE SKIP LOCKED`).

## Storage (migration 0005)

Telemetry is compressed, segmented by asset and signal and ordered by time; the 3 loaded
chunks were compressed by the migration. A compression policy handles later loads and a
refresh policy keeps `telemetry_1m` current.

| | before | after |
|---|---|---|
| telemetry (1,449,270 rows) | 766.0 MB (table 351.4, index 414.5) | 108.1 MB (7.1x smaller) |
| readings (63,238 rows, not compressed) | 46.0 MB | 46.0 MB |
| telemetry_1m | 7.5 MB | 7.5 MB |
| database | 830.1 MB | 172.4 MB |
| `db perf`: B June, day at 1 min, median | 15.8 ms | 16.0 ms |
| `db perf`: B June, one hour raw, median | 117.1 ms | 70.7 ms |

Loading a file again still inserts nothing into compressed chunks (tested).

## REAL: replay equals batch 3a-3

`pumpcopilot replay verify <id>` compares a session's stored ScoredEvidence with batch 3a-3
scoring of the same day. The comparison covers every field, not only the keys.

| session | day | rows stored / batch | identical | cases (3a-3 batch) |
|---|---|---|---|---|
| 3 | B 2024-10-30 | 2752 / 2752 | yes | 1 (1) |
| 4 | A 2024-10-30 | 677 / 677 | yes | 2 (2) |
| 1 | B 2024-10-30, failed, then rewound | 2752 / 2752 | yes | 1 (1) |
| 2 | A 2024-10-30, before the reason-text fix | 677 / 677 | yes after the fix | 2 (2) |

The B case runs 09:08–15:10 over 36 episodes, the same as the 3a-3 report.

## REAL: latency at 60x

Latency is measured per reading. It runs from the wall time at which the cursor passes the
reading to the time the first stored score whose window contains it was written. At 60x a
window can end up to one source minute (1 s of wall time) after a reading, so about 1 s of
p50 is structural.

| session | readings | p50 | p95 | max | steady p50 / p95 (n) | waiting for baselines p50 / p95 (n) |
|---|---|---|---|---|---|---|
| 3: B October | 2687 | 1.02 s | 2.12 s | 23.2 s | 1.00 / 1.88 s (2608) | 8.2 / 19.3 s (79) |
| 4: A October | 707 | 1.09 s | 13.2 s | 24.1 s | 1.07 / 1.40 s (604) | 10.1 / 19.2 s (103) |

- **Steady:** readings after the run's first persisted score.
- **Waiting for baselines:** readings in scored windows from before every signal's baseline
  in the run was final. A run's scores are held until then, so each row carries batch 3a-3's
  run-level model_version. That holds back up to about 25 source minutes, 25 s at 60x.
- **Left out:** readings in no scored window (baseline periods, before the run): B 302, A 352.

## SYNTHETIC: scenario B_stuck_pressure (session 5)

Outlet pressure is held stuck for 20 min, 90 min after B's run starts. The fault is applied
in memory only.

- All 2752 rows and every case event are flagged synthetic. The database's composite foreign
  key refuses a non-synthetic row for a synthetic session.
- 33 pressure windows are insufficient_evidence with a stale_suspected reason.
- There are 2 cases instead of 1: the stale windows break the review chain for more than
  15 min.
- Stored rows are identical to batch 3a-3 on the injected day.
- Telemetry and readings are unchanged. Their row counts and md5 over every row were compared
  before and after (telemetry 1,449,270 rows `f05c0aa8…`, readings 63,238 rows `70312c65…`).

## Found while running on real data

1. **Session 1 failed: stale worker cache.**
   - Cause: two workers took turns on one session. Each kept an in-memory cache (stored score
     keys and case-tracker state). When worker 1 claimed the session again, its cache did not
     include what worker 2 had just stored. It re-sent evidence for windows that already had
     case events, and the unique index `case_events_evidence_once` refused it.
   - Effect: the step rolled back, so the stored rows and events stayed a consistent prefix.
     The session was marked failed.
   - Fix: a worker reuses its cache only if it was also the last to step the session (same
     `claimed_by` and `heartbeat_at`); otherwise it reloads from the database.
   - Test: `test_workers_taking_turns_on_one_session_equal_batch` fails with that exact
     UniqueViolation without the fix and passes with it.
   - Recovery: `pumpcopilot replay rewind 1` completed the session with the same 917
     evidence events and 1 case as a fresh session; nothing was duplicated.
2. **Batch 3a-3 reason text looked ahead.**
   - For stale windows, the reason said "reading held N s" with the full hold, which is only
     known once the hold ends. Replay reported the hold known at the time: 2 of 677 rows
     differed on A October.
   - Batch now reports the hold as known at the window end. States are unchanged, and the
     committed 3a-3 report regenerates identically from its stored results.
3. **Batch 3a-3 crash on a run with no baseline yet.** `score_within_run_steady` raised when
   no signal in a run had a baseline; replay reaches that state at early cursors. It is now
   handled. Outputs where batch already worked are unchanged.

## Also in this step

- **Baseline progress** (`replay_sessions.baseline_progress`, migration 0007). Updated at each
  scoring step, per run and signal, so a UI can show "baseline forming". Each signal carries
  its status (`waiting_for_reading`, `settling`, `forming` with the fraction collected,
  `formed`, or `abstained` with the reason), settling time, first reading and baseline
  window.
- **Related cases.** Evidence that would have extended a case someone has closed opens a new
  case with `related_case_id` pointing to the closed one. The database checks that the
  related case exists and is closed, and allows the field only on `opened` events.
- **`pumpcopilot db reset --yes-i-mean-it`.** Drops and recreates the database, then migrates.
  It refuses unless every host in `DATABASE_URL` (or `PGHOST`/`PGHOSTADDR`) is loopback or a
  unix socket, and refuses without the flag.

## Declared inputs (A8)

Replay scores only readings at or before the cursor. Two things come from the whole stored
day, as in batch:

- each signal's median reading interval (sets the window length), recorded on the session;
- the ingest annotations: operating state, stale and spike flags, and segments.

Both are declared in `docs/ASSUMPTIONS.md` A8, with the evidence that the interval is a real
lookahead on these days.
