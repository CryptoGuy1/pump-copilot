"""Step 4a: replay a stored CIRA pump-day through the frozen 3a-3 detector.

A replay session copies no telemetry. It moves a source-time cursor through the stored day,
paced to wall-clock time by its speed (1x, 10x, 60x). At each step the worker scores the day
as it was visible at the cursor, with the unchanged 3a-3 functions:

* readings: only those observed at or before the cursor. The hold of each signal's latest
  reading is known only up to the cursor (min(stored hold, cursor - reading time)).
* per-minute sample counts: only complete minutes before the cursor.
* running stretches: only the part of each running segment seen so far. A run counts as
  closed once it has stopped and the 10 s merge gap of `scoring.load_day` has passed.

Declared inputs that are not limited to the cursor (recorded on the session):

* day constants: each signal's median reading interval over the stored day (it sets the 3a-3
  window length), the day's scored signals and the stale limits. Batch 3a-3 uses these from
  the whole day, so replay uses the same values; this is what makes replay equal batch.
* ingest annotations stored with each reading: operating state, the stale and spike flags,
  and the operating-state segments. They were derived at load time and carry that
  derivation's bounded lookahead (minimum state duration, spike revert window, stale limit).

A stretch's scores are persisted once every signal's baseline in it is final, so each row
carries the same run-level model_version as batch. Cases follow the 3a-3 merge rule and are
decided only when every window that starts earlier has been scored.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import socket
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import scoring

ROOT = Path(__file__).resolve().parents[2]
SPEEDS = (1, 10, 60)
MERGE_GAP_S = 10.0  # as scoring.load_day: motor_unconfirmed splits are attributes, not stops
CONFIG_NAME = "revision_3a3"
END_OF_TIME = pd.Timestamp.max.tz_localize("UTC")
REVIEW = str(scoring.P.REVIEW_SUGGESTED)

# In-memory scenarios from the 3a injection code (scoring.inject). t0 = first run start +
# offset_s; offsets (step) are size x the signal's 3a-3 baseline sigma in that run.
SCENARIOS = {
    "B_stuck_pressure": {"asset_id": "cira-pump-B", "signal": "outlet_pressure",
                         "fault": "stuck", "size": 1200.0, "offset_s": 5400.0},
    "B_dropout_pump_vibration": {"asset_id": "cira-pump-B", "signal": "pump_vibration_velocity",
                                 "fault": "dropout", "size": 1200.0, "offset_s": 5400.0},
    "B_step_casing_temperature": {"asset_id": "cira-pump-B",
                                  "signal": "motor_casing_temperature",
                                  "fault": "step", "size": 6.0, "offset_s": 5400.0},
}


# --- inputs ------------------------------------------------------------------------------

def frozen_config(path: Path | None = None, name: str = CONFIG_NAME) -> dict:
    import yaml

    doc = yaml.safe_load((path or ROOT / "data" / "scoring_config.yaml").read_text())
    return scoring.merge_config(doc[name])


def stale_limits(rules: dict, column_map: dict) -> dict[str, float]:
    """Per-signal stale limits from the operating rules, keyed by canonical signal name."""
    per = rules["stale"]["per_signal"]
    return {spec["signal"]: float(per.get(short, rules["stale"]["default_s"]))
            for short, spec in column_map.items()}


def default_stale_limits() -> dict[str, float]:
    import yaml

    rules = yaml.safe_load((ROOT / "data" / "operating_rules.yaml").read_text())
    return stale_limits(rules, yaml.safe_load((ROOT / "data" / "cira_columns.yaml").read_text()))


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def apply_scenario(day: scoring.DayData, name: str, cfg: dict) -> scoring.DayData:
    """The day with the scenario's fault injected, in memory only (the input is untouched)."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; known: {', '.join(sorted(SCENARIOS))}")
    sc = SCENARIOS[name]
    if day.asset_id != sc["asset_id"]:
        raise ValueError(f"scenario {name} is for asset {sc['asset_id']}, not {day.asset_id}")
    if not day.running:
        raise ValueError(f"scenario {name}: {day.asset_id} {day.source_day} has no run")
    a = day.running[0][0]
    t0 = (a + pd.Timedelta(seconds=sc["offset_s"])).floor("s")
    sigma = 0.0
    if sc["fault"] in ("step", "ramp", "drift"):
        prep = scoring.prepare(day, cfg)
        _, bases = scoring.score_within_run_steady(prep, cfg)
        band = bases[0].bands.get(scoring.scored_name(sc["signal"], prep))
        if band is None or t0 < band["baseline_end"]:
            raise ValueError(f"scenario {name}: no 3a-3 baseline for {sc['signal']} before t0")
        sigma = band["sigma"]
    out = scoring.inject(day, sc["signal"], sc["fault"], sc["size"], t0, sigma, cfg)
    out.injections[-1]["scenario"] = name
    return out


def source_span(day: scoring.DayData) -> tuple[pd.Timestamp, pd.Timestamp]:
    t = day.readings.observed_at
    return t.min(), t.max() + pd.Timedelta(seconds=1)


def day_constants(day: scoring.DayData, cfg: dict,
                  source_end: pd.Timestamp | None = None) -> dict:
    """Declared per-day inputs, the same values batch 3a-3 derives from the whole day."""
    prep = scoring.prepare(day, cfg)
    return {"intervals": dict(prep.intervals), "signals": list(prep.signals),
            "max_window_s": max((scoring.signal_window_s(prep, s, cfg) for s in prep.signals),
                                default=int(cfg["features"]["window_s"])),
            "stale_limits": dict(day.stale_limits),
            "source_end": str(source_end if source_end is not None else source_span(day)[1])}


# --- what is visible at the cursor -------------------------------------------------------

Segment = tuple  # (state, start_at, end_at), end inclusive, as in state_segments


def segments_from_running(running, start: pd.Timestamp, end: pd.Timestamp) -> list[Segment]:
    """Off/running segments for a day whose running stretches are [a, b)."""
    one = pd.Timedelta(seconds=1)
    out, t = [], start
    for a, b in running:
        if a > t:
            out.append(("off", t, a - one))
        out.append(("running", a, b - one))
        t = b
    if end > t:
        out.append(("off", t, end - one))
    return out


def segments_from_db(conn, asset_id: str, source_day: dt.date) -> list[Segment]:
    from . import db

    return [(s["state"], pd.Timestamp(s["start_at"]), pd.Timestamp(s["end_at"]))
            for s in db.fetch_segments(conn, asset_id, source_day)]


def running_at(segments: list[Segment], cursor: pd.Timestamp,
               gap: float = MERGE_GAP_S) -> list[tuple[pd.Timestamp, pd.Timestamp, bool]]:
    """Running stretches as known at the cursor: (start, end so far, closed). Merged like
    scoring.load_day (end = last second + 1 s; gaps up to `gap` s join)."""
    one = pd.Timedelta(seconds=1)
    runs: list[list] = []
    for state, a, e in segments:
        if state != "running" or a > cursor:
            continue
        b = e + one
        if runs and (a - runs[-1][1]).total_seconds() <= gap:
            runs[-1][1] = b
        else:
            runs.append([a, b])
    out = []
    for a, b in runs:
        ended = b <= cursor
        out.append((a, min(b, cursor), ended and (cursor - b).total_seconds() >= gap))
    return out


def visible_day(day: scoring.DayData, segments: list[Segment], cursor: pd.Timestamp
                ) -> tuple[scoring.DayData, list]:
    runs = running_at(segments, cursor)
    r = day.readings[day.readings.observed_at <= cursor].copy()
    so_far = (cursor - r.observed_at).dt.total_seconds()
    r["held_s"] = np.fmin(r.held_s.to_numpy(dtype=float), so_far.to_numpy())
    s = day.samples_1m[day.samples_1m.bucket + pd.Timedelta(minutes=1) <= cursor]
    vis = scoring.DayData(day.asset_id, day.source_day, r, s.copy(),
                          [(a, b) for a, b, _ in runs if b > a], day.synthetic,
                          list(day.injections), dict(day.stale_limits))
    return vis, [x for x in runs if x[1] > x[0]]


def prepared_at(day, segments, consts: dict, cfg: dict, cursor: pd.Timestamp):
    vis, runs = visible_day(day, segments, cursor)
    prep = scoring.prepare(vis, cfg)
    return replace(prep, intervals=dict(consts["intervals"])), runs


# --- scores ------------------------------------------------------------------------------

def _utc(t) -> pd.Timestamp:
    return pd.Timestamp(t).tz_convert("UTC")


def row_key(r: dict) -> tuple:
    return (r["signal_name"], str(_utc(r["window_end"])), r["model_version"])


def _num(x) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def _rows(frame: pd.DataFrame, base: scoring.Baseline, cfg: dict) -> list[dict]:
    out = []
    for sig, g in frame.groupby("signal_name", sort=False):
        for row, se in zip(g.itertuples(), scoring.to_scored_evidence(g, base, cfg),
                           strict=True):
            out.append({
                "signal_name": sig, "stretch": int(row.stretch),
                "window_start": row.start, "window_end": row.end,
                "state": str(se.presentation_state), "score": se.score,
                "reason": se.abstention_reason, "model_id": se.model_id,
                "model_version": se.model_version,
                "confidence_calibration_status": str(se.confidence_calibration_status),
                "scored_evidence": se.model_dump(mode="json"),
                "median": _num(row.median), "band_low": _num(row.low),
                "band_high": _num(row.high)})
    return out


def batch_rows(day: scoring.DayData, cfg: dict) -> list[dict]:
    """Reference: batch 3a-3 scoring of the whole day, as persisted rows."""
    prep = scoring.prepare(day, cfg)
    frame, bases = scoring.score_within_run_steady(prep, cfg)
    return [r for k, base in enumerate(bases)
            for r in _rows(frame[frame.stretch == k], base, cfg)]


def _signal_final(prep, sig: str, a, b, cfg: dict) -> bool:
    """Is this signal's 3a-3 baseline outcome in an open run already what batch would get?"""
    if sig not in prep.signals:
        return False
    onset = scoring.settled_at(sig, a, cfg)
    if onset is None:
        return True  # abstains on configuration alone
    g = prep.readings[(prep.readings.signal_name == sig) & (prep.readings.observed_at >= a)
                      & (prep.readings.observed_at < b)]
    if g.empty:
        return False  # a first reading may still come
    start = max(g.observed_at.min(), onset)
    return start + pd.Timedelta(seconds=cfg["within_run"]["baseline_s"]) <= b


def step_rows(day, segments, consts: dict, cfg: dict, cursor: pd.Timestamp
              ) -> tuple[list[dict], pd.Timestamp]:
    """Rows final at the cursor, and the case watermark: every window starting at or before
    it has been scored (or will never be)."""
    rows, watermark, _ = step(day, segments, consts, cfg, cursor)
    return rows, watermark


def step(day, segments, consts: dict, cfg: dict, cursor: pd.Timestamp
         ) -> tuple[list[dict], pd.Timestamp, dict]:
    """step_rows plus each signal's baseline progress at the cursor."""
    at_end = cursor >= pd.Timestamp(consts["source_end"])
    prep, runs = prepared_at(day, segments, consts, cfg, cursor)
    watermark = END_OF_TIME if at_end else cursor - pd.Timedelta(seconds=consts["max_window_s"])
    if not runs:
        return [], watermark, {"cursor": str(cursor), "runs": []}
    frame, bases = scoring.score_within_run_steady(prep, cfg)
    rows = []
    for k, ((a, b, closed), base) in enumerate(zip(runs, bases, strict=True)):
        final = at_end or closed or all(_signal_final(prep, s, a, b, cfg)
                                        for s in consts["signals"])
        if not final:
            watermark = min(watermark, a - pd.Timedelta(microseconds=1))
            continue
        rows += _rows(frame[frame.stretch == k], base, cfg)
    return rows, watermark, baseline_progress(prep, runs, bases, cfg, consts, cursor, at_end)


BASELINE_STATUSES = ("waiting_for_reading", "settling", "forming", "formed", "abstained")


def baseline_progress(prep, runs, bases, cfg: dict, consts: dict, cursor: pd.Timestamp,
                      at_end: bool = False) -> dict:
    """Per run and signal, where its 3a-3 baseline stands at the cursor:
    waiting_for_reading (no fresh reading in the run yet), settling (before its settling time,
    A7), forming (fraction of baseline_s collected), formed, or abstained (with the reason)."""
    x = cfg["within_run"]["baseline_s"]
    out = []
    for k, ((a, b, closed), base) in enumerate(zip(runs, bases, strict=True)):
        ended = closed or at_end
        signals = {}
        for sig in consts["signals"]:
            onset = scoring.settled_at(sig, a, cfg)
            d = {"status": None, "fraction": 0.0,
                 "settled_at": None if onset is None else str(onset),
                 "first_reading": None, "baseline_start": None, "baseline_end": None,
                 "reason": None}
            g = prep.readings[(prep.readings.signal_name == sig)
                              & (prep.readings.observed_at >= a) & (prep.readings.observed_at < b)]
            if onset is None:
                d.update(status="abstained", reason=base.signal_abstentions.get(
                    sig, f"no settling time for signal type {scoring.signal_type(sig)}"))
            elif g.empty:
                d.update(status="abstained" if ended else "waiting_for_reading",
                         reason="no fresh reading in this run" if ended else None)
            else:
                first = g.observed_at.min()
                start = max(first, onset)
                end = start + pd.Timedelta(seconds=x)
                d.update(first_reading=str(first), baseline_start=str(start),
                         baseline_end=str(end))
                if end <= b and sig in base.bands:
                    band = base.bands[sig]
                    d.update(status="formed", fraction=1.0, band={
                        k: band[k] for k in ("center", "low", "high", "unit")})
                elif end <= b or ended:
                    d.update(status="abstained", reason=base.signal_abstentions.get(sig))
                elif b < start:
                    d.update(status="settling")
                else:
                    d.update(status="forming",
                             fraction=min(1.0, max(0.0, (b - start).total_seconds() / x)))
            signals[sig] = d
        out.append({"run": k, "start": str(a), "end": str(b), "closed": bool(ended),
                    "signals": signals})
    return {"cursor": str(cursor), "runs": out}


def target_cursor(anchor_cursor, anchor_wall, speed: int, now, source_end):
    """Source time reached at wall time `now`: anchor_cursor + speed x elapsed wall time."""
    elapsed = (pd.Timestamp(now) - pd.Timestamp(anchor_wall)).total_seconds()
    t = pd.Timestamp(anchor_cursor) + pd.Timedelta(seconds=max(0.0, elapsed) * speed)
    return min(t, pd.Timestamp(source_end)).floor("us")


# --- streaming cases (3a-3 merge rule) ---------------------------------------------------

@dataclass
class CaseTracker:
    """Opens and extends cases from review_suggested rows, in (run, window start, signal)
    order, once the watermark says every earlier-starting window is scored. Equivalent to
    scoring.cases_from_episodes over the final episodes."""
    cfg: dict
    consts: dict
    _cases: list = field(default_factory=list)
    _by_window: dict = field(default_factory=dict)
    _done: set = field(default_factory=set)
    _last: tuple | None = None
    _events: list = field(default_factory=list)

    def feed(self, rows: list[dict], watermark: pd.Timestamp) -> list[dict]:
        todo = sorted((r for r in rows if r["state"] == REVIEW and row_key(r) not in self._done
                       and pd.Timestamp(r["window_start"]) <= watermark),
                      key=self._order)
        new = []
        for r in todo:
            if self._last is not None and self._order(r) < self._last:
                raise ValueError(f"case rows out of order: {self._order(r)} after {self._last}")
            new += self._add(r)
            self._last = self._order(r)
            self._done.add(row_key(r))
        self._events += new
        return new

    @staticmethod
    def _order(r: dict) -> tuple:
        return (int(r["stretch"]), pd.Timestamp(r["window_start"]), r["signal_name"])

    def _add(self, r: dict) -> list[dict]:
        gap, step = self.cfg["cases"]["gap_s"], pd.Timedelta(seconds=self.cfg["features"]["step_s"])
        sig, k = r["signal_name"], int(r["stretch"])
        s, e = pd.Timestamp(r["window_start"]), pd.Timestamp(r["window_end"])
        events = []
        prev = self._by_window.get((sig, k, s - step))
        if prev is not None and not self._cases[prev]["closed"]:
            idx, episode_start = prev, False
        else:
            # evidence that would have extended a closed case opens a new, related one
            related = prev
            last = self._cases[-1] if self._cases else None
            within = (last is not None and last["run"] == k
                      and (s - last["end"]).total_seconds() < gap)
            if within and not last["closed"]:
                idx = len(self._cases) - 1
            else:
                if within and related is None:
                    related = len(self._cases) - 1
                idx = len(self._cases)
                self._cases.append({"id": None, "run": k, "start": s, "end": e,
                                    "signals": set(), "episodes": 0, "closed": False})
                events.append({"type": "opened", "case": idx, "row": r,
                               "related_case": related})
            episode_start = True
        c = self._cases[idx]
        c["end"] = max(c["end"], e)
        c["signals"].add(sig)
        c["episodes"] += int(episode_start)
        self._by_window[(sig, k, s)] = idx
        events.append({"type": "evidence_added", "case": idx, "row": r,
                       "episode_start": episode_start})
        return events

    def cases(self) -> list[dict]:
        return [{"id": c["id"], "run": c["run"], "start": str(c["start"]), "end": str(c["end"]),
                 "signals": sorted(c["signals"]), "episodes": c["episodes"]}
                for c in self._cases]

    def events(self) -> list[dict]:
        return list(self._events)

    def set_id(self, idx: int, case_id: int) -> None:
        self._cases[idx]["id"] = case_id

    def id_of(self, idx: int) -> int | None:
        return self._cases[idx]["id"]

    def mark_closed(self, case_ids) -> None:
        ids = set(case_ids)
        for c in self._cases:
            if c["id"] in ids:
                c["closed"] = True

    @classmethod
    def from_events(cls, cfg: dict, consts: dict, evidence: list[dict],
                    closed_ids=()) -> CaseTracker:
        """Rebuild from stored evidence_added events (ordered by event_id)."""
        tr = cls(cfg, consts)
        index: dict[int, int] = {}
        for ev in evidence:
            cid = ev["case_id"]
            if cid not in index:
                index[cid] = len(tr._cases)
                tr._cases.append({"id": cid, "run": int(ev["stretch"]),
                                  "start": pd.Timestamp(ev["window_start"]),
                                  "end": pd.Timestamp(ev["window_end"]), "signals": set(),
                                  "episodes": 0, "closed": False})
            c = tr._cases[index[cid]]
            s, e = pd.Timestamp(ev["window_start"]), pd.Timestamp(ev["window_end"])
            c["start"], c["end"] = min(c["start"], s), max(c["end"], e)
            c["signals"].add(ev["signal_name"])
            c["episodes"] += int(bool(ev["episode_start"]))
            tr._by_window[(ev["signal_name"], int(ev["stretch"]), s)] = index[cid]
            row = {"signal_name": ev["signal_name"], "stretch": ev["stretch"],
                   "window_start": s, "window_end": e, "model_version": ev["model_version"]}
            tr._done.add(row_key(row))
            tr._last = max(tr._last, cls._order(row)) if tr._last else cls._order(row)
        tr.mark_closed(closed_ids)
        return tr


# --- sessions in the database ------------------------------------------------------------

def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def create_session(conn, asset_id: str, source_day: dt.date, speed: int,
                   scenario: str | None = None, cfg: dict | None = None,
                   stale_limits: dict | None = None, config_name: str = CONFIG_NAME) -> int:
    from psycopg.types.json import Jsonb

    if speed not in SPEEDS:
        raise ValueError(f"speed must be one of {SPEEDS}, not {speed}")
    if scenario is not None and scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; known: {', '.join(sorted(SCENARIOS))}")
    cfg = cfg or frozen_config(name=config_name)
    limits = stale_limits if stale_limits is not None else default_stale_limits()
    lo, hi = conn.execute("SELECT min(observed_at), max(observed_at) FROM telemetry WHERE "
                          "asset_id = %s AND source_day = %s", [asset_id, source_day]).fetchone()
    if lo is None:
        raise ValueError(f"no telemetry stored for {asset_id} {source_day}")
    source_end = pd.Timestamp(hi) + pd.Timedelta(seconds=1)
    day = scoring.load_day(conn, asset_id, source_day, stale_limits=limits)
    if scenario:
        day = apply_scenario(day, scenario, cfg)
    consts = day_constants(day, cfg, source_end)
    cfg_json = json.loads(json.dumps(cfg, default=str))
    with conn.transaction():
        sid = conn.execute(
            "INSERT INTO replay_sessions (asset_id, source_day, speed, source_start, source_end,"
            " scenario, config_name, config, config_sha256, day_constants) VALUES"
            " (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING session_id",
            [asset_id, source_day, speed, lo, source_end.to_pydatetime(), scenario,
             config_name, Jsonb(cfg_json), _sha(cfg_json), Jsonb(consts)]).fetchone()[0]
        _emit_progress(conn, sid)
    return sid


def get_session(conn, session_id: int) -> dict:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        s = cur.execute("SELECT * FROM replay_sessions WHERE session_id = %s",
                        [session_id]).fetchone()
    if s is None:
        raise ValueError(f"no replay session {session_id}")
    return s


def list_sessions(conn) -> list[dict]:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("SELECT session_id, asset_id, source_day, speed, status, cursor_at,"
                           " scenario, synthetic, claimed_by FROM replay_sessions"
                           " ORDER BY session_id").fetchall()


def claim(conn, worker_id: str | None = None) -> dict | None:
    """Lock one pending or running session (call inside a transaction). Sessions another
    worker holds are skipped; the least recently stepped comes first. NO KEY UPDATE, not
    UPDATE: it still excludes other workers, but lets foreign-key checks (case events written
    by the API on this session) through, so a case action never waits for a step."""
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT * FROM replay_sessions WHERE status IN ('pending', 'running')"
            " ORDER BY heartbeat_at NULLS FIRST, session_id LIMIT 1"
            " FOR NO KEY UPDATE SKIP LOCKED"
        ).fetchone()


def _set_status(conn, session_id: int, sql: str, allowed: tuple, args=()) -> None:
    cur = conn.execute(f"UPDATE replay_sessions SET {sql} WHERE session_id = %s AND status ="
                       f" ANY(%s)", [*args, session_id, list(allowed)])
    if cur.rowcount != 1:
        s = get_session(conn, session_id)
        raise ValueError(f"session {session_id} is {s['status']}; needs one of {allowed}")


def progress_payload(s: dict, progress: dict | None = None) -> dict:
    """The replay.progress event body: where a session is, and its baseline status counts."""
    progress = progress if progress is not None else (s.get("baseline_progress") or {})
    counts: dict[str, int] = {}
    for run in progress.get("runs", []):
        for v in run["signals"].values():
            counts[v["status"]] = counts.get(v["status"], 0) + 1
    return {"session_id": s["session_id"], "asset_id": s["asset_id"],
            "source_day": str(s["source_day"]), "status": s["status"],
            "cursor_at": None if s["cursor_at"] is None else str(s["cursor_at"]),
            "speed": s["speed"], "synthetic": s["synthetic"], "scenario": s["scenario"],
            "baseline": counts}


def _emit_progress(conn, session_id: int) -> None:
    from . import events

    events.emit(conn, "replay.progress", progress_payload(get_session(conn, session_id)))


def pause_session(conn, session_id: int) -> None:
    with conn.transaction():
        _set_status(conn, session_id, "status = 'paused'", ("pending", "running"))
        _emit_progress(conn, session_id)


def resume_session(conn, session_id: int, now: dt.datetime | None = None) -> None:
    """Continue from the stored cursor; pacing restarts from now."""
    with conn.transaction():
        _set_status(conn, session_id,
                    "status = CASE WHEN cursor_at IS NULL THEN 'pending' ELSE 'running' END,"
                    " anchor_cursor = cursor_at, anchor_wall = %s", ("paused",),
                    [now or _now()])
        _emit_progress(conn, session_id)


def rewind_session(conn, session_id: int) -> None:
    """Replay again from the start. Stored scores and case events stay; nothing duplicates."""
    with conn.transaction():
        _set_status(conn, session_id, "status = 'pending', cursor_at = NULL,"
                    " anchor_cursor = NULL, anchor_wall = NULL, error = NULL",
                    ("pending", "running", "paused", "completed", "failed"))
        _emit_progress(conn, session_id)


def set_speed(conn, session_id: int, speed: int, now: dt.datetime | None = None) -> None:
    """Change the pace from here on: pacing is re-anchored at the stored cursor."""
    if speed not in SPEEDS:
        raise ValueError(f"speed must be one of {SPEEDS}, not {speed}")
    with conn.transaction():
        _set_status(conn, session_id, "speed = %s, anchor_cursor = cursor_at,"
                    " anchor_wall = CASE WHEN cursor_at IS NULL THEN NULL ELSE %s END",
                    ("pending", "running", "paused", "completed"), [speed, now or _now()])
        _emit_progress(conn, session_id)


@dataclass
class _Cache:
    day: scoring.DayData
    segments: list
    consts: dict
    cfg: dict
    seen: set
    review_rows: list
    tracker: CaseTracker
    computed_minute: pd.Timestamp | None = None
    stepped_at: dt.datetime | None = None  # heartbeat this worker wrote on its last step
    last_progress: dict | None = None


SCORE_COLS = ("session_id", "synthetic", "asset_id", "source_day", "signal_name", "stretch",
              "window_start", "window_end", "model_id", "model_version", "presentation_state",
              "score", "abstention_reason", "confidence_calibration_status", "scored_evidence",
              "cursor_at", "stored_at", "median", "band_low", "band_high")


class Worker:
    """`pumpcopilot worker`: claims sessions with FOR UPDATE SKIP LOCKED and advances them."""

    HEARTBEAT_S = 1.0

    def __init__(self, conn, worker_id: str | None = None, clock=None):
        self.conn = conn
        self.id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.clock = clock or _now  # pacing clock (simulated in tests)
        self._cache: dict[int, _Cache] = {}
        self._started = _now()
        self._beat_at = -math.inf
        self._stepped = 0
        self._last_session = None

    def heartbeat(self, status: str = "running") -> None:
        """Record that this worker is alive (real wall time, whatever the pacing clock)."""
        self._beat_at = time.monotonic()
        self.conn.execute(
            "INSERT INTO worker_heartbeats (worker_id, host, pid, started_at, last_seen, status,"
            " sessions_stepped, last_session_id) VALUES (%s, %s, %s, %s, clock_timestamp(),"
            " %s, %s, %s) ON CONFLICT (worker_id) DO UPDATE SET last_seen = clock_timestamp(),"
            " status = EXCLUDED.status, sessions_stepped = EXCLUDED.sessions_stepped,"
            " last_session_id = EXCLUDED.last_session_id",
            [self.id, socket.gethostname(), os.getpid(), self._started, status, self._stepped,
             self._last_session])

    def tick(self) -> dict | None:
        """One step of one due session; None when nothing is claimable."""
        if time.monotonic() - self._beat_at >= self.HEARTBEAT_S:
            self.heartbeat()
        try:
            with self.conn.transaction():
                s = claim(self.conn, self.id)
                if s is None:
                    return None
                return self._step(s)
        except Exception as e:
            if "s" in locals() and s is not None:
                self.conn.execute("UPDATE replay_sessions SET status = 'failed', error = %s"
                                  " WHERE session_id = %s", [repr(e)[:2000], s["session_id"]])
                self._cache.pop(s["session_id"], None)
            raise

    def run(self, poll_s: float = 0.1, until_idle: bool = False,
            max_s: float | None = None) -> None:
        t_end = time.monotonic() + max_s if max_s else math.inf
        try:
            while time.monotonic() < t_end:
                out = self.tick()
                if out is None:
                    if until_idle and not self.conn.execute(
                            "SELECT 1 FROM replay_sessions WHERE status IN ('pending',"
                            " 'running') LIMIT 1").fetchone():
                        return
                    time.sleep(poll_s)
                elif not out.get("advanced"):
                    time.sleep(poll_s)
        finally:
            self.heartbeat("stopped")

    # -- internals --

    def _load(self, s: dict) -> _Cache:
        from psycopg.rows import dict_row

        conn, sid = self.conn, s["session_id"]
        cfg, consts = s["config"], s["day_constants"]
        day = scoring.load_day(conn, s["asset_id"], s["source_day"],
                               stale_limits=consts["stale_limits"])
        if s["scenario"]:
            day = apply_scenario(day, s["scenario"], cfg)
        with conn.cursor(row_factory=dict_row) as cur:
            stored = cur.execute(
                "SELECT signal_name, stretch, window_start, window_end, model_version,"
                " presentation_state AS state, score FROM scores WHERE session_id = %s",
                [sid]).fetchall()
            evidence = cur.execute(
                "SELECT case_id, stretch, signal_name, window_start, window_end, model_version,"
                " episode_start FROM case_events WHERE session_id = %s AND"
                " event_type = 'evidence_added' ORDER BY event_id", [sid]).fetchall()
        closed = [r[0] for r in conn.execute(
            "SELECT case_id FROM case_events WHERE session_id = %s AND event_type = 'closed'",
            [sid])]
        return _Cache(day, segments_from_db(conn, s["asset_id"], s["source_day"]), consts, cfg,
                      {row_key(r) for r in stored},
                      [r for r in stored if r["state"] == REVIEW],
                      CaseTracker.from_events(cfg, consts, evidence, closed))

    def _step(self, s: dict) -> dict:
        conn, sid = self.conn, s["session_id"]
        c = self._cache.get(sid)
        if c is not None and (s["claimed_by"] != self.id or s["heartbeat_at"] != c.stepped_at):
            c = None  # another worker stepped this session since: our cache is stale
        if c is None:
            c = self._cache[sid] = self._load(s)
        now = self.clock()
        was = s["status"]
        if s["status"] == "pending":
            c.computed_minute = None
            s = {**s, "status": "running", "cursor_at": s["source_start"],
                 "anchor_cursor": s["source_start"], "anchor_wall": now}
            conn.execute("UPDATE replay_sessions SET status = 'running', cursor_at = %s,"
                         " anchor_cursor = %s, anchor_wall = %s WHERE session_id = %s",
                         [s["source_start"], s["source_start"], now, sid])
        end = pd.Timestamp(s["source_end"])
        cursor = pd.Timestamp(s["cursor_at"])
        target = target_cursor(s["anchor_cursor"], s["anchor_wall"], s["speed"], now, end)
        minute = target.floor("min")
        due = target >= end or c.computed_minute is None or minute > c.computed_minute
        out = {"session_id": sid, "cursor": target, "advanced": target > cursor, "stored": 0,
               "case_events": 0}
        stored, case_evs, progress = [], [], None
        if due and target > cursor or (target >= end and s["status"] != "completed"):
            rows, watermark, progress = step(c.day, c.segments, c.consts, c.cfg, target)
            c.last_progress = progress
            new = [r for r in rows if row_key(r) not in c.seen]
            out["stored"] = self._store(s, new, target)
            stored = new
            c.seen |= {row_key(r) for r in new}
            c.review_rows += [r for r in new if r["state"] == REVIEW]
            c.tracker.mark_closed(r[0] for r in conn.execute(
                "SELECT case_id FROM case_events WHERE session_id = %s AND"
                " event_type = 'closed'", [sid]))
            case_evs = c.tracker.feed(c.review_rows, watermark)
            out["case_events"] = self._cases(s, case_evs, c)
            c.computed_minute = minute
            through = end if watermark >= end else watermark
            from psycopg.types.json import Jsonb

            conn.execute("UPDATE replay_sessions SET cases_through = %s, baseline_progress = %s"
                         " WHERE session_id = %s",
                         [through.to_pydatetime(), Jsonb(progress), sid])
        status = "completed" if target >= end else "running"
        conn.execute("UPDATE replay_sessions SET cursor_at = %s, status = %s, claimed_by = %s,"
                     " heartbeat_at = %s WHERE session_id = %s",
                     [target.to_pydatetime(), status, self.id, now, sid])
        c.stepped_at = now
        self._stepped += 1
        self._last_session = sid
        self._emit(s, target, status, was, stored, case_evs, progress, c)
        if status == "completed":
            self._cache.pop(sid, None)
        return out

    def _emit(self, s, target, status, was, stored, case_evs, progress, c) -> None:
        """Stream events for this step, at its end (the event lock is held until commit)."""
        from . import events

        if stored:
            ends = [str(_utc(r["window_end"])) for r in stored]
            states: dict[str, int] = {}
            for r in stored:
                states[r["state"]] = states.get(r["state"], 0) + 1
            events.emit(self.conn, "score.batch", {
                "session_id": s["session_id"], "asset_id": s["asset_id"],
                "source_day": str(s["source_day"]), "synthetic": s["synthetic"],
                "scenario": s["scenario"], "count": len(stored), "window_end_first": min(ends),
                "window_end_last": max(ends), "states": states,
                "model_version": sorted({r["model_version"] for r in stored})})
        touched: dict[int, dict] = {}
        for ev in case_evs:
            cid = c.tracker.id_of(ev["case"])
            t = touched.setdefault(cid, {"opened": 0, "evidence_added": 0,
                                         "related_case_id": None})
            t[ev["type"]] += 1
            if ev["type"] == "opened" and ev.get("related_case") is not None:
                t["related_case_id"] = c.tracker.id_of(ev["related_case"])
        for cid, t in touched.items():
            events.emit(self.conn, "case.event", {
                "case_id": cid, "session_id": s["session_id"], "asset_id": s["asset_id"],
                "source_day": str(s["source_day"]), "synthetic": s["synthetic"],
                "actor": "worker", "event_type": "opened" if t["opened"] else "evidence_added",
                "events": {k: t[k] for k in ("opened", "evidence_added")},
                "related_case_id": t["related_case_id"]})
        if progress is not None or status != was:
            events.emit(self.conn, "replay.progress", progress_payload(
                {**s, "status": status, "cursor_at": target}, progress or c.last_progress))

    def _store(self, s: dict, rows: list[dict], cursor: pd.Timestamp) -> int:
        if not rows:
            return 0
        from psycopg.types.json import Jsonb

        stored_at = self.clock()
        cols = ", ".join(SCORE_COLS)
        with self.conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO scores ({cols}) VALUES ({', '.join(['%s'] * len(SCORE_COLS))})"
                " ON CONFLICT ON CONSTRAINT scores_idempotency DO NOTHING",
                [(s["session_id"], s["synthetic"], s["asset_id"], s["source_day"],
                  r["signal_name"], r["stretch"], r["window_start"].to_pydatetime(),
                  r["window_end"].to_pydatetime(), r["model_id"], r["model_version"],
                  r["state"], r["score"], r["reason"], r["confidence_calibration_status"],
                  Jsonb(r["scored_evidence"]), cursor.to_pydatetime(), stored_at,
                  r["median"], r["band_low"], r["band_high"])
                 for r in rows])
        return len(rows)

    def _cases(self, s: dict, events: list[dict], c: _Cache) -> int:
        base = [s["session_id"], s["synthetic"], s["asset_id"], s["source_day"]]
        for ev in events:
            r = ev["row"]
            if ev["type"] == "opened":
                cid = self.conn.execute("SELECT nextval('case_ids')").fetchone()[0]
                c.tracker.set_id(ev["case"], cid)
                rel = ev.get("related_case")
                self.conn.execute(
                    "INSERT INTO case_events (case_id, event_type, session_id, synthetic,"
                    " asset_id, source_day, stretch, actor, related_case_id) VALUES (%s,"
                    " 'opened', %s, %s, %s, %s, %s, 'worker', %s)",
                    [cid, *base, int(r["stretch"]),
                     None if rel is None else c.tracker.id_of(rel)])
                continue
            self.conn.execute(
                "INSERT INTO case_events (case_id, event_type, session_id, synthetic, asset_id,"
                " source_day, stretch, actor, signal_name, window_start, window_end,"
                " model_version, score, episode_start) VALUES (%s, 'evidence_added', %s, %s,"
                " %s, %s, %s, 'worker', %s, %s, %s, %s, %s, %s)",
                [c.tracker.id_of(ev["case"]), *base, int(r["stretch"]), r["signal_name"],
                 pd.Timestamp(r["window_start"]).to_pydatetime(),
                 pd.Timestamp(r["window_end"]).to_pydatetime(), r["model_version"],
                 r["score"], ev["episode_start"]])
        return len(events)


# --- latency -----------------------------------------------------------------------------

def _pct(x: list[float]) -> dict:
    if not x:
        return {"n": 0, "p50_s": None, "p95_s": None, "max_s": None}
    a = np.asarray(x)
    return {"n": len(a), "p50_s": float(np.percentile(a, 50)),
            "p95_s": float(np.percentile(a, 95)), "max_s": float(a.max())}


def latency(conn, session_id: int) -> dict:
    """Wall time from the cursor passing a reading to the first stored score whose window
    contains it. The cursor passes source time t at anchor_wall + (t - anchor_cursor)/speed;
    readings before the last anchor (a pause) are left out. 'steady' covers readings after the
    run's first persisted score; 'backlog' those that waited for the run's baselines."""
    from psycopg.rows import dict_row

    s = get_session(conn, session_id)
    cfg, consts = s["config"], s["day_constants"]
    day = scoring.load_day(conn, s["asset_id"], s["source_day"],
                           stale_limits=consts["stale_limits"])
    if s["scenario"]:
        day = apply_scenario(day, s["scenario"], cfg)
    prep, _ = prepared_at(day, segments_from_db(conn, s["asset_id"], s["source_day"]), consts,
                          cfg, pd.Timestamp(s["source_end"]))
    with conn.cursor(row_factory=dict_row) as cur:
        sc = pd.DataFrame(cur.execute(
            "SELECT signal_name, stretch, window_start, window_end, stored_at, cursor_at"
            " FROM scores WHERE session_id = %s", [session_id]).fetchall())
    a_c, a_w = pd.Timestamp(s["anchor_cursor"]), pd.Timestamp(s["anchor_wall"])
    speed = s["speed"]
    first_by_run = sc.groupby("stretch").cursor_at.min().map(pd.Timestamp).to_dict()
    steady, backlog, unscored = [], [], 0
    for sig, g in prep.readings.groupby("signal_name"):
        w = sc[sc.signal_name == sig].sort_values("window_start")
        ws, we = w.window_start.map(pd.Timestamp).to_numpy(), w.window_end.map(pd.Timestamp)
        we = we.to_numpy()
        stored = w.stored_at.map(pd.Timestamp).to_numpy()
        stretch = w.stretch.to_numpy()
        for t in g.observed_at[g.observed_at >= a_c]:
            m = (ws <= t) & (we > t)
            if not m.any():
                unscored += 1
                continue
            i = np.flatnonzero(m)[np.argmin(stored[m])]
            passed = a_w + (t - a_c) / speed
            lat = (pd.Timestamp(stored[i]) - passed).total_seconds()
            (steady if t >= first_by_run[stretch[i]] else backlog).append(lat)
    allx = steady + backlog
    return {"session_id": session_id, "asset_id": s["asset_id"],
            "source_day": str(s["source_day"]), "speed": speed, "readings": len(allx),
            "readings_in_no_scored_window": unscored, **_pct(allx),
            "steady": _pct(steady), "backlog": _pct(backlog)}


def verify(conn, session_id: int) -> dict:
    """Compare a session's stored scores with batch 3a-3 scoring of the same day."""
    s = get_session(conn, session_id)
    cfg, consts = s["config"], s["day_constants"]
    day = scoring.load_day(conn, s["asset_id"], s["source_day"],
                           stale_limits=consts["stale_limits"])
    if s["scenario"]:
        day = apply_scenario(day, s["scenario"], cfg)
    batch = {row_key(r): r["scored_evidence"] for r in batch_rows(day, cfg)}
    got = {(r[0], str(_utc(r[1])), r[2]): r[3] for r in conn.execute(
        "SELECT signal_name, window_end, model_version, scored_evidence FROM scores"
        " WHERE session_id = %s", [session_id])}
    differ = [k for k in batch.keys() & got.keys() if batch[k] != got[k]]
    return {"session_id": session_id, "batch_rows": len(batch), "stored_rows": len(got),
            "missing": len(batch.keys() - got.keys()), "extra": len(got.keys() - batch.keys()),
            "different": len(differ), "identical": batch == got}
