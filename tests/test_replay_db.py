"""Step 4a with a database: compression, replay sessions, the worker, persisted scores, cases
and synthetic scenarios. Marked db; skipped when DATABASE_URL is unreachable."""
import datetime as dt
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest
import yaml
from psycopg.types.json import Jsonb

from pumpcopilot import cases, db, operating, replay, scoring

ROOT = Path(__file__).resolve().parents[1]
COLUMN_MAP = yaml.safe_load((ROOT / "data" / "cira_columns.yaml").read_text())
DAY = dt.date(2024, 6, 11)
ASSET = "cira-pump-B"
START = pd.Timestamp("2024-06-11 07:00:00", tz="UTC")
WALL0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
pytestmark = pytest.mark.db


def _held(rng, n, every, fn):
    """1 Hz rows whose value changes every `every` seconds (sample-and-hold, A5)."""
    return np.repeat(fn(np.arange(0, n, every), rng), every)[:n]


def replay_csv(path: Path, hours=3.0, run=(20, 170), bump_min=120):
    """B_2024-06-11-shaped day: idle, a 2.5 h run with a pressure shift after 2 h, idle."""
    rng = np.random.default_rng(3)
    n = int(hours * 3600)
    ts = START + pd.to_timedelta(np.arange(n), unit="s")
    on = lambda s: (s >= run[0] * 60) & (s < run[1] * 60)  # noqa: E731
    cols = {
        "Pres.PV": lambda s, r: np.where(on(s), 41 + 0.8 * (s >= bump_min * 60), 0.5)
        + r.normal(0, 0.05, len(s)),
        "ACR_Mot.SV": lambda s, r: np.where(on(s), 20.0, 0.47) + r.normal(0, 0.01, len(s)),
        "ACR_Mot.PV": lambda s, r: r.normal(0.002, 0.00005, len(s)),
        "ACR_Mot.TV": lambda s, r: r.normal(30, 0.05, len(s)),
        "ACR_Pmp.PV": lambda s, r: r.normal(0.004, 0.0001, len(s)),
        "ACR_Pmp.SV": lambda s, r: r.normal(25, 0.5, len(s)),
        "ACR_Pmp.TV": lambda s, r: r.normal(30, 0.05, len(s)),
        "Temp.PV": lambda s, r: r.normal(35, 0.05, len(s)),
    }
    frame = {"Timestamp": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in ts]}
    for h, fn in cols.items():
        frame[f"B_{h}"] = _held(rng, n, 10, fn)
    frame["Barometer"] = _held(rng, n, 10, lambda s, r: r.normal(1018, 0.1, len(s)))
    frame["Temperature"] = _held(rng, n, 10, lambda s, r: r.normal(20, 0.02, len(s)))
    pd.DataFrame(frame).to_csv(path, index=False)


@pytest.fixture
def replay_db(conn, tmp_path):
    raw = tmp_path / "replay_raw"
    raw.mkdir()
    replay_csv(raw / "B_2024-06-11.csv")
    rules, _ = operating.derive_rules(raw)
    rules_path = tmp_path / "operating_rules.yaml"
    operating.write_rules(rules, rules_path)
    db.load_cira(conn, raw, COLUMN_MAP, rules_path)
    return conn, replay.stale_limits(rules, COLUMN_MAP)


class Clock:
    def __init__(self, t=WALL0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += dt.timedelta(seconds=s)


def run_to_end(conn, sid, clock, worker=None, max_ticks=100_000, stop_at=None, wall_s=10.0):
    """Tick a worker, advancing the simulated wall clock wall_s per tick (at 60x, 1 s of
    wall time is one minute of source time)."""
    w = worker or replay.Worker(conn, "w1", clock=clock)
    for _ in range(max_ticks):
        st = replay.get_session(conn, sid)
        if st["status"] == "completed" or (stop_at and st["cursor_at"] and
                                           st["cursor_at"] >= stop_at):
            return w
        w.tick()
        clock.advance(wall_s)
    raise AssertionError("session did not complete")


def stored_rows(conn, sid):
    return conn.execute("SELECT signal_name, window_end, model_version, scored_evidence, synthetic"
                        " FROM scores WHERE session_id = %s", [sid]).fetchall()


def batch_for(conn, limits, scenario=None):
    cfg = replay.frozen_config()
    day = scoring.load_day(conn, ASSET, DAY, stale_limits=limits)
    if scenario:
        day = replay.apply_scenario(day, scenario, cfg)
    return replay.batch_rows(day, cfg), day, cfg


def _key(sig, end, ver):
    return (sig, str(pd.Timestamp(end).tz_convert("UTC")), ver)


# --- housekeeping: compression and the refresh policy ------------------------------------

def test_compression_migration_compresses_already_loaded_chunks(db_url, tmp_path, cira_dir):
    early = tmp_path / "migrations_0001_0004"
    early.mkdir()
    for f in sorted(db.MIGRATIONS.glob("*.sql"))[:4]:
        shutil.copy(f, early / f.name)
    rules, _ = operating.derive_rules(cira_dir)
    rules_path = tmp_path / "operating_rules.yaml"
    operating.write_rules(rules, rules_path)
    with db.connect(db_url) as c:
        db.migrate(c, early)
        db.load_cira(c, cira_dir, COLUMN_MAP, rules_path)
        n = c.execute("SELECT count(*) FROM telemetry").fetchone()[0]
        before = c.execute("SELECT count(*) FROM timescaledb_information.chunks WHERE "
                           "hypertable_name = 'telemetry' AND is_compressed").fetchone()[0]
        assert before == 0
        db.migrate(c)
        chunks = c.execute("SELECT is_compressed FROM timescaledb_information.chunks WHERE "
                           "hypertable_name = 'telemetry'").fetchall()
        assert chunks and all(r[0] for r in chunks)
        seg = {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT attname, segmentby_column_index, orderby_column_index FROM "
            "timescaledb_information.compression_settings WHERE hypertable_name = 'telemetry'")}
        assert seg["asset_id"][0] is not None and seg["signal_name"][0] is not None
        assert seg["observed_at"][1] is not None
        jobs = set(c.execute("SELECT proc_name, hypertable_name FROM "
                             "timescaledb_information.jobs").fetchall())
        assert {("policy_compression", "telemetry"),
                ("policy_refresh_continuous_aggregate", "telemetry_1m")} <= jobs
        # still idempotent and queryable once compressed
        again = db.load_cira(c, cira_dir, COLUMN_MAP, rules_path)
        assert sum(r["telemetry_inserted"] for r in again) == 0
        assert c.execute("SELECT count(*) FROM telemetry").fetchone()[0] == n
        assert db.fetch_raw(c, "cira-pump-A", dt.date(2024, 4, 10))


def test_storage_report_lists_tables(conn):
    rep = db.storage_report(conn)
    assert {"telemetry", "readings", "telemetry_1m", "database"} <= set(rep)
    assert rep["telemetry"]["total_bytes"] >= 0


# --- sessions and claiming ---------------------------------------------------------------

def test_session_records_asset_day_speed_status_cursor_and_scenario(replay_db):
    conn, limits = replay_db
    sid = replay.create_session(conn, ASSET, DAY, speed=10, stale_limits=limits)
    s = replay.get_session(conn, sid)
    assert (s["asset_id"], s["source_day"], s["speed"], s["status"]) == (ASSET, DAY, 10,
                                                                       "pending")
    assert s["cursor_at"] is None and s["scenario"] is None and s["synthetic"] is False
    assert s["config_name"] == "revision_3a3"
    assert set(s["day_constants"]) >= {"intervals", "signals", "max_window_s", "stale_limits"}
    with pytest.raises(ValueError, match="speed"):
        replay.create_session(conn, ASSET, DAY, speed=5)
    with pytest.raises(ValueError, match="unknown scenario"):
        replay.create_session(conn, ASSET, DAY, speed=60, scenario="B_meteor_strike")


def test_claim_uses_skip_locked(replay_db, db_url):
    conn, limits = replay_db
    s1 = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    s2 = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    with db.connect(db_url) as other, db.connect(db_url) as third_conn, conn.transaction():
        first = replay.claim(conn)
        with other.transaction():
            second = replay.claim(other)
            with third_conn.transaction():
                third = replay.claim(third_conn)
    assert {first["session_id"], second["session_id"]} == {s1, s2}
    assert third is None


# --- the key equivalence test, persisted -------------------------------------------------

def test_worker_replay_equals_batch_3a3_and_cases(replay_db):
    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    run_to_end(conn, sid, clock, wall_s=1.0)  # step by step: one source minute per tick
    batch, day, cfg = batch_for(conn, limits)
    got = {_key(*r[:3]): r[3] for r in stored_rows(conn, sid)}
    assert len(batch) > 50 and len(got) == len(batch)
    assert got == {_key(r["signal_name"], r["window_end"], r["model_version"]):
                   r["scored_evidence"] for r in batch}
    assert any(r["state"] == "review_suggested" for r in batch)
    frame, _ = scoring.score_within_run_steady(scoring.prepare(day, cfg), cfg)
    eps = [{**e, "start": str(e["start"]), "end": str(e["end"])}
           for e in scoring.review_episodes(frame)]
    expect = scoring.cases_from_episodes(eps, day.running, cfg["cases"]["gap_s"])
    state = cases.list_cases(conn, session_id=sid)
    assert expect and [(c["stretch"], pd.Timestamp(c["evidence_start"]),
                        pd.Timestamp(c["evidence_end"]), sorted(c["signals"]), c["episodes"])
                       for c in state] == \
        [(c["run"], pd.Timestamp(c["start"]), pd.Timestamp(c["end"]), c["signals"],
          c["episodes"]) for c in expect]
    assert all(c["status"] == "open" and not c["synthetic"] for c in state)


def test_pause_resume_and_worker_restart_continue_from_the_stored_cursor(replay_db):
    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    mid = START + pd.Timedelta(hours=1, minutes=40)
    run_to_end(conn, sid, clock, stop_at=mid)
    replay.pause_session(conn, sid)
    paused_at = replay.get_session(conn, sid)["cursor_at"]
    n_paused = len(stored_rows(conn, sid))
    w = replay.Worker(conn, "w1", clock=clock)
    for _ in range(30):
        w.tick()
        clock.advance(60)  # an hour of wall time passes while paused
    s = replay.get_session(conn, sid)
    assert s["status"] == "paused" and s["cursor_at"] == paused_at
    assert len(stored_rows(conn, sid)) == n_paused
    replay.resume_session(conn, sid, now=clock())
    w.tick()
    s = replay.get_session(conn, sid)
    assert paused_at <= s["cursor_at"] <= paused_at + dt.timedelta(seconds=120)
    # restart: a new worker with an empty cache picks up from the stored cursor
    run_to_end(conn, sid, clock, worker=replay.Worker(conn, "w2", clock=clock))
    batch, _, _ = batch_for(conn, limits)
    got = {_key(*r[:3]): r[3] for r in stored_rows(conn, sid)}
    assert got == {_key(r["signal_name"], r["window_end"], r["model_version"]):
                   r["scored_evidence"] for r in batch}


def test_workers_taking_turns_on_one_session_equal_batch(replay_db):
    # each tick claims the least recently stepped session, so two workers alternate; neither
    # may act on a cache the other has made stale
    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    w1, w2 = replay.Worker(conn, "w1", clock=clock), replay.Worker(conn, "w2", clock=clock)
    for i in range(10_000):
        if replay.get_session(conn, sid)["status"] == "completed":
            break
        (w1 if i % 2 else w2).tick()
        clock.advance(5.0)
    batch, _, _ = batch_for(conn, limits)
    got = {_key(*r[:3]): r[3] for r in stored_rows(conn, sid)}
    assert got == {_key(r["signal_name"], r["window_end"], r["model_version"]):
                   r["scored_evidence"] for r in batch}
    assert cases.list_cases(conn, session_id=sid)


def _counts(conn, sid):
    return {"scores": len(stored_rows(conn, sid)),
            **dict(conn.execute("SELECT event_type, count(*) FROM case_events WHERE"
                                " session_id = %s GROUP BY 1", [sid]).fetchall())}


@pytest.mark.parametrize("same_worker", [True, False])
def test_rewind_and_rerun_appends_no_duplicate_case_events(replay_db, same_worker):
    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    w = run_to_end(conn, sid, clock)
    first = _counts(conn, sid)
    assert first["opened"] >= 1 and first["evidence_added"] >= first["opened"]
    replay.rewind_session(conn, sid)
    s = replay.get_session(conn, sid)
    assert (s["status"], s["cursor_at"]) == ("pending", None)
    run_to_end(conn, sid, clock, worker=w if same_worker else replay.Worker(conn, "w9",
                                                                             clock=clock))
    assert replay.get_session(conn, sid)["status"] == "completed"
    assert _counts(conn, sid) == first
    # and the database refuses a duplicate score outright
    with pytest.raises(psycopg.errors.UniqueViolation):
        r = conn.execute("SELECT * FROM scores WHERE session_id = %s LIMIT 1", [sid]).fetchone()
        cols = [d.name for d in conn.execute("SELECT * FROM scores LIMIT 0").description]
        conn.execute(f"INSERT INTO scores ({', '.join(cols)}) VALUES "
                     f"({', '.join(['%s'] * len(cols))})",
                     [Jsonb(v) if isinstance(v, dict) else v for v in r])


def test_rewind_is_a_cli_command(replay_db, db_url, monkeypatch, capsys):
    from pumpcopilot import cli

    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    run_to_end(conn, sid, clock)
    monkeypatch.setenv("DATABASE_URL", db_url)
    cli.main(["replay", "rewind", str(sid)])
    assert f"session {sid}: pending" in capsys.readouterr().out
    assert replay.get_session(conn, sid)["status"] == "pending"


def test_a_failed_session_recovers_by_rewind(replay_db):
    # session 1 on the real data failed mid-way (two workers, stale cache); its step rolled
    # back, so the stored rows and events are a consistent prefix. Rewinding completes it.
    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    run_to_end(conn, sid, clock, stop_at=START + pd.Timedelta(hours=2, minutes=10))
    conn.execute("UPDATE replay_sessions SET status = 'failed', error = 'simulated'"
                 " WHERE session_id = %s", [sid])
    w = replay.Worker(conn, "w1", clock=clock)
    assert w.tick() is None  # a failed session is not claimed
    replay.rewind_session(conn, sid)
    run_to_end(conn, sid, clock, worker=w)
    batch, day, cfg = batch_for(conn, limits)
    got = {_key(*r[:3]): r[3] for r in stored_rows(conn, sid)}
    assert got == {_key(r["signal_name"], r["window_end"], r["model_version"]):
                   r["scored_evidence"] for r in batch}
    n_review = sum(r["state"] == "review_suggested" for r in batch)
    assert _counts(conn, sid)["evidence_added"] == n_review  # each review window once
    assert replay.get_session(conn, sid)["error"] is None


# --- synthetic scenarios -----------------------------------------------------------------

def _fingerprint(conn):
    out = {}
    for t, cols in (("telemetry", "observed_at, provenance_hash, value, quality_flags"),
                    ("readings", "observed_at, provenance_hash, value, held_s, quality_flags")):
        out[t] = conn.execute(f"SELECT count(*), md5(string_agg(({cols})::text, '|' ORDER BY "
                              f"observed_at, provenance_hash)) FROM {t}").fetchone()
    return out


@pytest.mark.parametrize("scenario", sorted(replay.SCENARIOS))
def test_no_synthetic_scenario_changes_telemetry_or_readings(replay_db, scenario):
    conn, limits = replay_db
    before = _fingerprint(conn)
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, scenario=scenario,
                                stale_limits=limits)
    run_to_end(conn, sid, clock)
    assert _fingerprint(conn) == before
    rows = stored_rows(conn, sid)
    assert rows and all(r[4] is True for r in rows)
    ev = conn.execute("SELECT DISTINCT synthetic FROM case_events WHERE session_id = %s",
                      [sid]).fetchall()
    assert ev in ([], [(True,)])
    assert all(c["synthetic"] for c in cases.list_cases(conn, session_id=sid))
    batch, _, _ = batch_for(conn, limits, scenario)
    assert {_key(*r[:3]): r[3] for r in rows} == {
        _key(r["signal_name"], r["window_end"], r["model_version"]): r["scored_evidence"]
        for r in batch}


def test_database_refuses_a_real_score_for_a_synthetic_session(replay_db):
    conn, limits = replay_db
    sid = replay.create_session(conn, ASSET, DAY, speed=60, scenario="B_stuck_pressure",
                                stale_limits=limits)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO scores (session_id, synthetic, asset_id, source_day, signal_name, "
            "stretch, window_start, window_end, model_id, model_version, presentation_state, "
            "confidence_calibration_status, scored_evidence, cursor_at) VALUES (%s, false, %s, "
            "%s, 'x', 0, now(), now() + interval '1 min', 'm', 'v', 'normal', 'not_applicable',"
            " '{}', now())", [sid, ASSET, DAY])


# --- cases -------------------------------------------------------------------------------

@pytest.fixture
def replayed(replay_db):
    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    run_to_end(conn, sid, clock)
    ids = [c["case_id"] for c in cases.list_cases(conn, session_id=sid)]
    assert ids
    return conn, sid, ids[0]


def test_case_lifecycle_and_derived_state(replayed):
    conn, _, cid = replayed
    assert cases.get_case(conn, cid)["status"] == "open"
    cases.acknowledge(conn, cid, actor="operator1")
    assert cases.get_case(conn, cid)["status"] == "acknowledged"
    cases.note(conn, cid, actor="operator1", text="pressure up 0.8 bar after 09:00")
    cases.dispose(conn, cid, actor="engineer1", disposition="monitor",
                  reason="shift is small and steady")
    st = cases.get_case(conn, cid)
    assert (st["status"], st["disposition"], st["disposition_reason"], st["notes"]) == \
        ("dispositioned", "monitor", "shift is small and steady", 1)
    cases.dispose(conn, cid, actor="engineer1",
                  disposition="escalate to reliability engineer (export only)",
                  reason="needs a second look")
    cases.close(conn, cid, actor="engineer1")
    st = cases.get_case(conn, cid)
    assert st["status"] == "closed" and st["disposition"].startswith("escalate")
    exported = cases.export(conn, cid)
    assert exported["case"]["case_id"] == cid and exported["evidence"]
    assert [e["event_type"] for e in exported["events"]][-1] == "closed"


def test_dispositions_are_the_four_allowed_and_need_a_reason(replayed):
    conn, _, cid = replayed
    assert cases.DISPOSITIONS == ("monitor", "known condition, no action",
                                  "data quality issue",
                                  "escalate to reliability engineer (export only)")
    cases.acknowledge(conn, cid, actor="op")
    with pytest.raises(ValueError, match="reason"):
        cases.dispose(conn, cid, actor="op", disposition="monitor", reason="  ")
    with pytest.raises(ValueError, match="disposition"):
        cases.dispose(conn, cid, actor="op", disposition="shut the pump down", reason="x")
    # the database enforces both too, whatever the client
    for disp, why in (("monitor", None), ("monitor", ""), ("replace bearing", "worn")):
        with pytest.raises(psycopg.errors.CheckViolation):
            cases.append_raw(conn, cid, "disposition", actor="op", disposition=disp, reason=why)


@pytest.mark.parametrize("steps,bad", [
    ([], ("disposition", {"disposition": "monitor", "reason": "r"})),   # not acknowledged
    ([], ("closed", {})),                                               # no disposition
    ([("acknowledged", {})], ("acknowledged", {})),                     # twice
    ([("acknowledged", {}), ("disposition", {"disposition": "monitor", "reason": "r"}),
      ("closed", {})], ("note", {"note": "after close"})),              # closed is final
    ([], ("opened", {})),                                               # opened twice
])
def test_invalid_transitions_raise(replayed, steps, bad):
    conn, _, cid = replayed
    for kind, fields in steps:
        cases.append_raw(conn, cid, kind, actor="op", **fields)
    with pytest.raises(cases.InvalidTransition):
        cases.append(conn, cid, bad[0], actor="op", **bad[1])


def test_case_events_are_append_only(replayed):
    conn, _, cid = replayed
    for sql in ("UPDATE case_events SET actor = 'x' WHERE case_id = %s",
                "DELETE FROM case_events WHERE case_id = %s"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute(sql, [cid])


def test_unknown_case_raises(replayed):
    conn, _, _ = replayed
    with pytest.raises(cases.InvalidTransition, match="does not exist"):
        cases.acknowledge(conn, 10**9, actor="op")


# --- latency -----------------------------------------------------------------------------

def test_latency_is_measured_from_the_cursor_passing_a_reading(replay_db):
    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    run_to_end(conn, sid, clock, wall_s=1.0)
    lat = replay.latency(conn, sid)
    assert lat["speed"] == 60 and lat["readings"] > 100
    assert 0 <= lat["p50_s"] <= lat["p95_s"]
    # with a simulated clock ticking 1 s per step, a reading waits for its window to end
    # (at most one window at 60x) plus the step, and nothing more once baselines are final
    assert lat["steady"]["p95_s"] <= 7 * 60 / 60 + 1.0 + 1e-6


# --- baseline progress on the session ----------------------------------------------------

def test_session_records_baseline_progress_per_signal(replay_db):
    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    run_to_end(conn, sid, clock, stop_at=START + pd.Timedelta(minutes=30), wall_s=1.0)
    p = replay.get_session(conn, sid)["baseline_progress"]
    sig = p["runs"][0]["signals"]
    assert sig["outlet_pressure"]["status"] == "forming"  # run 07:20, settles 07:25
    assert 0 < sig["outlet_pressure"]["fraction"] < 1
    assert {v["status"] for v in sig.values()} <= {"settling", "forming", "waiting_for_reading"}
    run_to_end(conn, sid, clock)
    sig = replay.get_session(conn, sid)["baseline_progress"]["runs"][0]["signals"]
    assert {v["status"] for v in sig.values()} <= {"formed", "abstained"}


# --- related cases -----------------------------------------------------------------------

def test_evidence_after_a_close_opens_a_related_case(replay_db):
    conn, limits = replay_db
    clock = Clock()
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    run_to_end(conn, sid, clock, stop_at=START + pd.Timedelta(hours=2, minutes=20), wall_s=1.0)
    first = cases.list_cases(conn, session_id=sid)
    assert len(first) == 1 and first[0]["related_case_id"] is None
    cid = first[0]["case_id"]
    cases.acknowledge(conn, cid, actor="op")
    cases.dispose(conn, cid, actor="op", disposition="monitor", reason="watching the shift")
    cases.close(conn, cid, actor="op")
    run_to_end(conn, sid, clock)
    after = cases.list_cases(conn, session_id=sid)
    assert len(after) == 2
    assert after[0]["status"] == "closed"
    assert after[1]["status"] == "open" and after[1]["related_case_id"] == cid
    with pytest.raises(psycopg.errors.CheckViolation):  # only an opened event carries it
        cases.append_raw(conn, after[1]["case_id"], "note", actor="op", note="x",
                         related_case_id=cid)


# --- db reset ----------------------------------------------------------------------------

@pytest.mark.parametrize("url,local", [
    ("postgresql://pump:x@localhost:5432/pumpcopilot", True),
    ("postgresql://pump:x@127.0.0.1/pumpcopilot", True),
    ("postgresql://pump@[::1]:5432/pumpcopilot", True),
    ("postgresql:///pumpcopilot", True),                      # unix socket
    ("host=/tmp dbname=pumpcopilot", True),
    ("postgresql://pump:x@db.example.com/pumpcopilot", False),
    ("postgresql://pump:x@10.0.0.5/pumpcopilot", False),
    ("postgresql://pump:x@localhost,db.example.com/pumpcopilot", False),
    ("host=localhost hostaddr=203.0.113.9 dbname=pumpcopilot", False),
])
def test_reset_only_accepts_local_databases(url, local):
    assert db.is_local_url(url) is local


def test_reset_refuses_without_confirmation_or_off_localhost(monkeypatch):
    from pumpcopilot import cli

    monkeypatch.setenv("DATABASE_URL", "postgresql://pump:x@db.example.com/pumpcopilot")
    with pytest.raises(SystemExit, match="not a local"):
        cli.main(["db", "reset", "--yes-i-mean-it"])
    monkeypatch.setenv("DATABASE_URL", "postgresql://pump:x@localhost/pumpcopilot")
    with pytest.raises(SystemExit, match="--yes-i-mean-it"):
        cli.main(["db", "reset"])
    with pytest.raises(ValueError, match="not a local"):
        db.reset_database("postgresql://pump:x@db.example.com/p", confirm=True)
    with pytest.raises(ValueError, match="confirm"):
        db.reset_database("postgresql://pump:x@localhost/p", confirm=False)


def test_reset_drops_everything_and_migrates_again(db_url, monkeypatch, capsys):
    from pumpcopilot import cli

    with db.connect(db_url) as c:
        db.migrate(c)
        c.execute("CREATE TABLE leftover (x int)")
    monkeypatch.setenv("DATABASE_URL", db_url)
    cli.main(["db", "reset", "--yes-i-mean-it"])
    assert "reset" in capsys.readouterr().out
    with db.connect(db_url) as c:
        tables = {r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        assert "leftover" not in tables and {"telemetry", "replay_sessions"} <= tables
        assert c.execute("SELECT count(*) FROM telemetry").fetchone()[0] == 0
        assert db.migrate(c) == []
