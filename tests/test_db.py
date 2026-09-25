import datetime as dt
import inspect
import re
from pathlib import Path

import numpy as np
import pytest
import yaml

from pumpcopilot import cira, db, operating

ROOT = Path(__file__).resolve().parents[1]
COLUMN_MAP = yaml.safe_load((ROOT / "data" / "cira_columns.yaml").read_text())
DAY = dt.date(2024, 4, 10)


# --- no database needed ------------------------------------------------------------------

def test_migrations_are_numbered_uniquely_and_in_order():
    files = sorted(p.name for p in db.MIGRATIONS.glob("*.sql"))
    assert files, "no migrations found"
    assert all(re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", f) for f in files)
    numbers = [int(f[:4]) for f in files]
    assert numbers == list(range(1, len(files) + 1))


def test_every_query_function_is_scoped_to_one_asset_day():
    assert db.QUERY_FUNCTIONS
    for fn in db.QUERY_FUNCTIONS:
        params = list(inspect.signature(fn).parameters)
        assert params[1:3] == ["asset_id", "source_day"], fn.__name__


def test_database_url_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.database_url() == db.DEFAULT_DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@example/y")
    assert db.database_url() == "postgresql://x@example/y"


# --- database ----------------------------------------------------------------------------

@pytest.mark.db
def test_migrate_is_idempotent_and_creates_hypertables(db_url):
    with db.connect(db_url) as c:
        first = db.migrate(c)
        assert first == sorted(p.name for p in db.MIGRATIONS.glob("*.sql"))
        assert db.migrate(c) == []
        hyper = {r[0] for r in c.execute(
            "SELECT hypertable_name FROM timescaledb_information.hypertables")}
        assert {"telemetry", "readings"} <= hyper
        caggs = {r[0] for r in c.execute(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates")}
        assert "telemetry_1m" in caggs


@pytest.mark.db
def test_loading_twice_inserts_nothing_the_second_time(loaded):
    conn, cira_dir, _, rules_path, first = loaded
    n_events = sum(1 for f in sorted(cira_dir.glob("*.csv"))
                   for _ in cira.to_events(f, COLUMN_MAP))
    assert sum(r["telemetry_inserted"] for r in first) == n_events
    assert all(r["telemetry_skipped"] == 0 for r in first)

    second = db.load_cira(conn, cira_dir, COLUMN_MAP, rules_path)
    for key in ("telemetry", "readings", "segments"):
        assert sum(r[f"{key}_inserted"] for r in second) == 0, key
    assert sum(r["telemetry_skipped"] for r in second) == n_events
    assert conn.execute("SELECT count(*) FROM telemetry").fetchone()[0] == n_events

    runs = conn.execute("SELECT source_file, source_file_sha256, rules_sha256, telemetry_inserted,"
                        " telemetry_skipped, started_at <= finished_at FROM ingest_runs").fetchall()
    assert len(runs) == 2 * len(first)
    assert all(r[5] for r in runs)
    assert {r[2] for r in runs} == {db.file_sha256(rules_path)}


@pytest.mark.db
def test_telemetry_rows_carry_state_and_flags(loaded):
    conn = loaded[0]
    states = {r[0] for r in conn.execute("SELECT DISTINCT operating_state FROM telemetry")}
    assert {"running", "off"} <= states
    flags = conn.execute("SELECT quality_flags FROM telemetry WHERE signal_name = %s LIMIT 1",
                         ["outlet_pressure"]).fetchone()[0]
    assert "unit_unverified" in flags  # Pres.PV is unit-unverified (A2)


@pytest.mark.db
def test_readings_hold_value_changes_only(loaded):
    conn, cira_dir, rules, _, _ = loaded
    path = cira_dir / "A_2024-04-10.csv"
    ann = operating.annotate(path, rules)
    expected = sum(len(operating.reading_events(ann.t, operating._num(ann.df[c]))[0])
                   for c in ann.df.columns if c != ann.ts_col)
    rows = db.fetch_readings(conn, "cira-pump-A", DAY)
    assert len(rows) == expected
    tel = conn.execute("SELECT count(*) FROM telemetry WHERE asset_id = %s AND source_day = %s"
                       " AND is_reading", ["cira-pump-A", DAY]).fetchone()[0]
    assert tel == expected


@pytest.mark.db
def test_queries_never_cross_days(loaded):
    conn = loaded[0]
    raw = db.fetch_raw(conn, "cira-pump-A", DAY)
    assert raw and {r["source_day"] for r in raw} == {DAY}
    assert db.fetch_raw(conn, "cira-pump-A", dt.date(2024, 4, 11)) == []
    start = dt.datetime(2024, 4, 10, 8, 0, tzinfo=dt.UTC)  # fixture starts 08:00 UTC
    window = db.fetch_raw(conn, "cira-pump-A", DAY, start, start + dt.timedelta(minutes=2),
                          signals=["outlet_pressure"])
    assert window and all(start <= r["observed_at"] < start + dt.timedelta(minutes=2)
                          for r in window)
    assert {r["signal_name"] for r in window} == {"outlet_pressure"}
    # a window outside the day is refused rather than silently reaching into another day
    with pytest.raises(ValueError, match="outside"):
        db.fetch_raw(conn, "cira-pump-A", DAY, start + dt.timedelta(days=1),
                     start + dt.timedelta(days=1, hours=1))


@pytest.mark.db
def test_state_segments_are_stored_with_rules_version(loaded):
    conn, cira_dir, rules, rules_path, _ = loaded
    segs = db.fetch_segments(conn, "cira-pump-A", DAY)
    ann = operating.annotate(cira_dir / "A_2024-04-10.csv", rules)
    assert [s["state"] for s in segs] == [str(g["state"]) for g in ann.result.segments]
    assert {s["rules_version"] for s in segs} == {db.file_sha256(rules_path)}
    running = [s for s in segs if s["state"] == "running"]
    assert running and all(s["motor_unconfirmed"] is not None for s in running)
    assert all(s["start_at"] <= s["end_at"] for s in segs)


@pytest.mark.db
def test_one_minute_aggregate_matches_raw(loaded):
    conn = loaded[0]
    agg = db.fetch_day_1m(conn, "cira-pump-A", DAY, signals=["outlet_pressure"])
    raw = [r for r in db.fetch_raw(conn, "cira-pump-A", DAY, signals=["outlet_pressure"])
           if r["value"] is not None]
    assert agg
    assert sum(a["sample_count"] for a in agg) == len(raw)
    assert sum(a["reading_count"] for a in agg) == sum(r["is_reading"] for r in raw)
    assert all(a["min_value"] <= a["mean_value"] <= a["max_value"] for a in agg)
    assert max(a["max_value"] for a in agg) == pytest.approx(max(r["value"] for r in raw))
    assert np.isclose(sum(a["mean_value"] * a["sample_count"] for a in agg),
                      sum(r["value"] for r in raw))
