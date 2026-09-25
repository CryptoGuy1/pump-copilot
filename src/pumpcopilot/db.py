"""Timescale storage for CIRA: migrations, idempotent loading, per-asset-day queries.

* Migrations are numbered SQL files in migrations/, applied in order by `migrate` and
  recorded with their sha256 in schema_migrations. An applied file that changes is an error.
* Loading goes through the canonical contract (`cira.to_events`), COPYs into a staging table
  and then INSERTs ... ON CONFLICT DO NOTHING on (observed_at, provenance_hash). Loading the
  same file twice inserts nothing the second time; every run is recorded in ingest_runs.
* CIRA days are separate recordings. Every query function takes (conn, asset_id, source_day)
  and stays inside that day; nothing joins across days by time.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import statistics
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row

from . import cira, operating
from .provenance import file_digest

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
DEFAULT_DATABASE_URL = "postgresql://pump:pump_dev_only@localhost:5432/pumpcopilot"
MIGRATION_NAME = re.compile(r"(\d{4})_[a-z0-9_]+\.sql")
DAY_MARGIN = dt.timedelta(hours=3)  # the audit's span margin around the filename day
_LOCK = 72_616_001  # advisory lock id: one migrator at a time


class MigrationError(RuntimeError):
    pass


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def connect(url: str | None = None) -> psycopg.Connection:
    """Autocommit connection; writes use explicit `conn.transaction()` blocks."""
    return psycopg.connect(url or database_url(), autocommit=True)


def file_sha256(path: Path) -> str:
    return file_digest(path)


def _json_sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


# --- migrations --------------------------------------------------------------------------

def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS) -> list[str]:
    """Apply pending migrations in order, each in its own transaction. Returns their names."""
    conn.execute("SELECT pg_advisory_lock(%s)", [_LOCK])
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version int PRIMARY KEY, filename text NOT NULL, sha256 text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())""")
        applied = {v: sha for v, sha in conn.execute(
            "SELECT version, sha256 FROM schema_migrations")}
        done = []
        for f in sorted(directory.glob("*.sql")):
            m = MIGRATION_NAME.fullmatch(f.name)
            if not m:
                raise MigrationError(f"unexpected migration file name: {f.name}")
            version, sha = int(m[1]), file_sha256(f)
            if version in applied:
                if applied[version] != sha:
                    raise MigrationError(f"{f.name} changed after it was applied")
                continue
            with conn.transaction():
                conn.execute(f.read_text())
                conn.execute("INSERT INTO schema_migrations (version, filename, sha256) "
                             "VALUES (%s, %s, %s)", [version, f.name, sha])
            done.append(f.name)
        return done
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", [_LOCK])


# --- loading -----------------------------------------------------------------------------

TELEMETRY_COLS = ("observed_at", "asset_id", "source_day", "source_file", "sample_id",
                  "signal_name", "value", "unit", "operating_state", "quality_flags",
                  "is_reading", "provenance_hash", "ingest_run_id")
SEGMENT_COLS = ("asset_id", "source_day", "state", "start_at", "end_at", "motor_unconfirmed",
                "pressurized_while_stopped", "rules_version", "ingest_run_id")


def _telemetry_rows(path: Path, ann: operating.Annotation, column_map: dict, rules: dict,
                    run_id: int) -> Iterator[tuple]:
    """Canonical events plus is_reading/held_s for the staging table."""
    held: dict[str, dict[int, float | None]] = {}  # signal -> row label -> seconds held
    for c in ann.df.columns:
        if c == ann.ts_col:
            continue
        signal = column_map[operating._short(str(c), ann.pump)]["signal"]
        idx, hold = operating.reading_events(ann.t, operating._num(ann.df[c]))
        held[signal] = {int(lbl): (float(h) if np.isfinite(h) else None)
                        for lbl, h in zip(ann.df.index[idx], hold, strict=True)}
    day = dt.date.fromisoformat(ann.day)
    for e in cira.to_events(path, column_map, rules=rules):
        label = int(e.sample_id.rsplit(":", 1)[1])
        sig = held[e.signal_name]
        yield (e.observed_at, e.asset_id, day, e.source_file, e.sample_id, e.signal_name,
               e.value, e.unit, e.operating_state.value, [f.value for f in e.quality_flags],
               label in sig, e.provenance_hash, run_id, sig.get(label))


def load_cira_file(conn: psycopg.Connection, path: Path, column_map: dict, rules: dict,
                   rules_sha: str, map_sha: str, asset_prefix: str = "cira-pump-") -> dict:
    ann = operating.annotate(path, rules)
    asset_id, day = f"{asset_prefix}{ann.pump}", dt.date.fromisoformat(ann.day)
    with conn.transaction(), conn.cursor() as cur:
        run_id = cur.execute(
            "INSERT INTO ingest_runs (source, source_file, source_file_sha256, rules_sha256,"
            " column_map_sha256) VALUES ('cira', %s, %s, %s, %s) RETURNING run_id",
            [path.name, file_sha256(path), rules_sha, map_sha]).fetchone()[0]

        cur.execute("CREATE TEMP TABLE stage_telemetry (LIKE telemetry INCLUDING DEFAULTS,"
                    " held_s double precision) ON COMMIT DROP")
        with cur.copy(f"COPY stage_telemetry ({', '.join(TELEMETRY_COLS)}, held_s) "
                      "FROM STDIN") as copy:
            for row in _telemetry_rows(path, ann, column_map, rules, run_id):
                copy.write_row(row)
        staged = cur.execute("SELECT count(*) FROM stage_telemetry").fetchone()[0]
        cols = ", ".join(TELEMETRY_COLS)
        cur.execute(f"INSERT INTO telemetry ({cols}) SELECT {cols} FROM stage_telemetry"
                    " ON CONFLICT (observed_at, provenance_hash) DO NOTHING")
        tel_in = cur.rowcount
        n_read = cur.execute("SELECT count(*) FROM stage_telemetry WHERE is_reading").fetchone()[0]
        cur.execute(
            "INSERT INTO readings (observed_at, asset_id, source_day, signal_name, value, unit,"
            " held_s, operating_state, quality_flags, provenance_hash, ingest_run_id)"
            " SELECT observed_at, asset_id, source_day, signal_name, value, unit, held_s,"
            " operating_state, quality_flags, provenance_hash, ingest_run_id FROM stage_telemetry"
            " WHERE is_reading ON CONFLICT (observed_at, provenance_hash) DO NOTHING")
        read_in = cur.rowcount

        segs = [(asset_id, day, str(g["state"]),
                 (ann.t0 + dt.timedelta(seconds=g["start_s"])).to_pydatetime(),
                 (ann.t0 + dt.timedelta(seconds=g["end_s"])).to_pydatetime(),
                 g.get("motor_unconfirmed"), g.get("pressurized_while_stopped"), rules_sha,
                 run_id) for g in ann.result.segments]
        cur.execute("CREATE TEMP TABLE stage_segments (LIKE state_segments) ON COMMIT DROP")
        with cur.copy(f"COPY stage_segments ({', '.join(SEGMENT_COLS)}) FROM STDIN") as copy:
            for row in segs:
                copy.write_row(row)
        scols = ", ".join(SEGMENT_COLS)
        cur.execute(f"INSERT INTO state_segments ({scols}) SELECT {scols} FROM stage_segments"
                    " ON CONFLICT DO NOTHING")
        seg_in = cur.rowcount

        counts = {"rows_staged": staged, "telemetry_inserted": tel_in,
                  "telemetry_skipped": staged - tel_in, "readings_inserted": read_in,
                  "readings_skipped": n_read - read_in, "segments_inserted": seg_in,
                  "segments_skipped": len(segs) - seg_in}
        cur.execute("UPDATE ingest_runs SET finished_at = clock_timestamp(), "
                    + ", ".join(f"{k} = %({k})s" for k in counts) + " WHERE run_id = %(id)s",
                    {**counts, "id": run_id})
        lo, hi = cur.execute("SELECT min(observed_at), max(observed_at) FROM stage_telemetry"
                             ).fetchone()
    if tel_in and lo is not None:  # outside the transaction: refresh cannot run inside one
        conn.execute("CALL refresh_continuous_aggregate('telemetry_1m', %s, %s)",
                     [lo - dt.timedelta(minutes=1), hi + dt.timedelta(minutes=1)])
    return {"file": path.name, "run_id": run_id, **counts}


def load_cira(conn: psycopg.Connection, raw_dir: Path, column_map: dict,
              rules_path: Path) -> list[dict]:
    rules = operating.load_rules(rules_path)
    rules_sha, map_sha = file_sha256(rules_path), _json_sha256(column_map)
    files = sorted(p for p in raw_dir.rglob("*.csv") if cira.FILENAME.match(p.name))
    return [load_cira_file(conn, f, column_map, rules, rules_sha, map_sha) for f in files]


# --- queries: always one asset, one day --------------------------------------------------

def _day_window(source_day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    lo = dt.datetime.combine(source_day, dt.time(), dt.UTC) - DAY_MARGIN
    return lo, lo + dt.timedelta(days=1) + 2 * DAY_MARGIN


def fetch_raw(conn: psycopg.Connection, asset_id: str, source_day: dt.date,
              start: dt.datetime | None = None, end: dt.datetime | None = None,
              signals: list[str] | None = None) -> list[dict]:
    """Full-resolution telemetry for one asset-day, optionally a [start, end) window of it."""
    lo, hi = _day_window(source_day)
    start, end = start or lo, end or hi
    if start < lo or end > hi:
        raise ValueError(f"window {start}..{end} is outside {source_day} (+/- {DAY_MARGIN})")
    q = ("SELECT observed_at, source_day, signal_name, value, unit, operating_state,"
         " quality_flags, is_reading FROM telemetry WHERE asset_id = %s AND source_day = %s"
         " AND observed_at >= %s AND observed_at < %s")
    args: list = [asset_id, source_day, start, end]
    if signals:
        q += " AND signal_name = ANY(%s)"
        args.append(signals)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(q + " ORDER BY signal_name, observed_at", args).fetchall()


def fetch_day_1m(conn: psycopg.Connection, asset_id: str, source_day: dt.date,
                 signals: list[str] | None = None) -> list[dict]:
    """One asset-day at 1-minute resolution from the continuous aggregate."""
    q = ("SELECT bucket, signal_name, min_value, max_value, mean_value, sample_count,"
         " reading_count FROM telemetry_1m WHERE asset_id = %s AND source_day = %s")
    args: list = [asset_id, source_day]
    if signals:
        q += " AND signal_name = ANY(%s)"
        args.append(signals)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(q + " ORDER BY signal_name, bucket", args).fetchall()


def fetch_readings(conn: psycopg.Connection, asset_id: str, source_day: dt.date,
                   signals: list[str] | None = None) -> list[dict]:
    """Reading events (value changes) for one asset-day."""
    q = ("SELECT observed_at, signal_name, value, unit, held_s, operating_state, quality_flags,"
         " provenance_hash FROM readings WHERE asset_id = %s AND source_day = %s")
    args: list = [asset_id, source_day]
    if signals:
        q += " AND signal_name = ANY(%s)"
        args.append(signals)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(q + " ORDER BY signal_name, observed_at", args).fetchall()


def fetch_segments(conn: psycopg.Connection, asset_id: str, source_day: dt.date,
                   rules_version: str | None = None) -> list[dict]:
    """Operating-state segments for one asset-day (latest rules version unless given)."""
    with conn.cursor(row_factory=dict_row) as cur:
        if rules_version is None:
            row = cur.execute(
                "SELECT s.rules_version FROM state_segments s JOIN ingest_runs r"
                " ON r.run_id = s.ingest_run_id WHERE s.asset_id = %s AND s.source_day = %s"
                " ORDER BY r.started_at DESC LIMIT 1", [asset_id, source_day]).fetchone()
            if row is None:
                return []
            rules_version = row["rules_version"]
        return cur.execute(
            "SELECT state, start_at, end_at, motor_unconfirmed, pressurized_while_stopped,"
            " rules_version FROM state_segments WHERE asset_id = %s AND source_day = %s"
            " AND rules_version = %s ORDER BY start_at",
            [asset_id, source_day, rules_version]).fetchall()


QUERY_FUNCTIONS = (fetch_raw, fetch_day_1m, fetch_readings, fetch_segments)


# --- performance check -------------------------------------------------------------------

def perf_check(conn: psycopg.Connection, asset_id: str, source_day: dt.date,
               hour_start: dt.datetime, repeats: int = 5) -> dict:
    """Median and best wall time to fetch the day at 1 minute and one hour at full resolution."""
    cases = {
        "day_1m": lambda: fetch_day_1m(conn, asset_id, source_day),
        "hour_raw": lambda: fetch_raw(conn, asset_id, source_day, hour_start,
                                      hour_start + dt.timedelta(hours=1)),
    }
    out = {"asset_id": asset_id, "source_day": str(source_day),
           "hour_start": str(hour_start), "repeats": repeats}
    for name, fn in cases.items():
        fn()  # warm-up
        times, rows = [], 0
        for _ in range(repeats):
            t0 = time.perf_counter()
            rows = len(fn())
            times.append((time.perf_counter() - t0) * 1000)
        out[name] = {"rows": rows, "median_ms": round(statistics.median(times), 1),
                     "best_ms": round(min(times), 1)}
    return out
