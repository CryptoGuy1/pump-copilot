"""Step 4a replay engine without a database: visibility, finality, equivalence with batch 3a-3,
causality, pacing, streaming cases and synthetic scenarios."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pumpcopilot import replay, scoring

T0 = pd.Timestamp("2024-06-11 07:00:00", tz="UTC")
CFG = scoring.merge_config({
    "features": {"window_readings": 6, "min_fresh_readings": 5, "step_s": 60,
                 "window_floor_s": 60, "stale_via_flag": True},
    "baseline": {"k": 4.0, "floor_fraction": 0.001},
    "review": {"consecutive_windows": 2},
    "within_run": {"baseline_s": 1200, "min_baseline_s": 900, "min_baseline_readings": 15,
                   "min_baseline_windows": 5},
    "onset": {"settling_s": {"pressure": 300, "vibration": 600, "temperature": 1800}},
    "cases": {"gap_s": 900},
})
TEMP = "motor_casing_temperature_rel_ambient"


def _sig(name, unit, every, fn, lo, hi, rng, day_start):
    t = pd.date_range(lo, hi, freq=f"{every}s", inclusive="left")
    secs = (t - day_start).total_seconds().to_numpy()
    return pd.DataFrame({"observed_at": t, "signal_name": name, "unit": unit,
                         "value": fn(secs, rng), "held_s": float(every),
                         "quality_flags": [()] * len(t),
                         "provenance_hash": [f"{name}:{x}" for x in t]})


def make_day(runs=((0.5, 2.5), (3.0, 4.75)), seed=0, bump_at_h=1.5):
    """A recorded day 07:00-12:00 with two runs; pressure shifts up after bump_at_h, and the
    pump vibration sensor reports only from 40 min into the first run."""
    rng = np.random.default_rng(seed)
    start, end = T0, T0 + pd.Timedelta(hours=5)
    running = [(T0 + pd.Timedelta(hours=a), T0 + pd.Timedelta(hours=b)) for a, b in runs]

    def pres(secs, r):
        on = np.zeros(len(secs), dtype=bool)
        for a, b in runs:
            on |= (secs >= a * 3600) & (secs < b * 3600)
        return np.where(on, 41.0 + 0.8 * (secs > bump_at_h * 3600), 0.5) + r.normal(0, .05,
                                                                                 len(secs))

    def temp(secs, r):
        return 35.0 + 5 * np.clip((secs - 1800) / 1500, 0, 1) + r.normal(0, 0.05, len(secs))

    vib_from = running[0][0] + pd.Timedelta(minutes=40)
    parts = [_sig("outlet_pressure", "bar", 60, pres, start, end, rng, T0),
             _sig("motor_casing_temperature", "degC", 8, temp, start, end, rng, T0),
             _sig("ambient_temperature", "degC", 8, lambda s, r: r.normal(20, .02, len(s)),
                  start, end, rng, T0),
             _sig("pump_vibration_velocity", "m/s", 8,
                  lambda s, r: r.normal(0.004, 0.0001, len(s)), vib_from, end, rng, T0)]
    r = pd.concat(parts, ignore_index=True)
    r["operating_state"] = np.where(scoring._in_running(r.observed_at, running), "running",
                                    "off")
    r = r.sort_values(["signal_name", "observed_at"]).reset_index(drop=True)
    r["held_s"] = (r.groupby("signal_name").observed_at.shift(-1) - r.observed_at
                   ).dt.total_seconds()
    minutes = pd.date_range(start, end, freq="60s", inclusive="left")
    s = pd.DataFrame([(m, g, 60) for m in minutes for g in r.signal_name.unique()],
                     columns=["bucket", "signal_name", "sample_count"])
    day = scoring.DayData("cira-pump-B", dt.date(2024, 6, 11), r, s, running,
                          stale_limits={"outlet_pressure": 150.0,
                                        "motor_casing_temperature": 30.0,
                                        "pump_vibration_velocity": 30.0})
    segments = replay.segments_from_running(running, start, end)
    return day, segments


def replay_all(day, segments, cursors, cfg=CFG):
    consts = replay.day_constants(day, cfg)
    seen, rows, tracker = set(), [], replay.CaseTracker(cfg, consts)
    for c in cursors:
        out, watermark = replay.step_rows(day, segments, consts, cfg, c)
        new = [r for r in out if replay.row_key(r) not in seen]
        seen |= {replay.row_key(r) for r in new}
        rows += new
        tracker.feed(rows, watermark)
    return rows, tracker


def cursors_every(day, seconds, jitter=0):
    lo, hi = replay.source_span(day)
    out = list(pd.date_range(lo, hi, freq=f"{seconds}s"))
    if jitter:
        out = [t + pd.Timedelta(seconds=jitter) for t in out]
    return [t for t in out if t <= hi] + [hi]


# --- visibility ---------------------------------------------------------------------------

def test_segments_merge_like_load_day_and_close_only_after_the_merge_gap():
    a, b = T0, T0 + pd.Timedelta(hours=1)
    segs = [("off", T0 - pd.Timedelta(hours=1), a - pd.Timedelta(seconds=1)),
            ("running", a, a + pd.Timedelta(minutes=10)),        # 1 s gap: one run
            ("running", a + pd.Timedelta(minutes=10, seconds=1), b - pd.Timedelta(seconds=1)),
            ("off", b, b + pd.Timedelta(hours=1))]
    full = replay.running_at(segs, b + pd.Timedelta(hours=1))
    assert full == [(a, b, True)]
    mid = a + pd.Timedelta(minutes=10, seconds=0.5)  # next segment not started: open so far
    assert replay.running_at(segs, mid) == [(a, mid, False)]
    assert replay.running_at(segs, a + pd.Timedelta(minutes=30)) == [
        (a, a + pd.Timedelta(minutes=30), False)]
    assert replay.running_at(segs, b + pd.Timedelta(seconds=5))[0][2] is False  # < gap
    assert replay.running_at(segs, b + pd.Timedelta(seconds=10))[0][2] is True


def test_visible_day_holds_only_data_at_or_before_the_cursor():
    day, segs = make_day()
    c = T0 + pd.Timedelta(hours=1, minutes=17, seconds=30)
    vis, runs = replay.visible_day(day, segs, c)
    assert (vis.readings.observed_at <= c).all()
    assert (vis.samples_1m.bucket + pd.Timedelta(minutes=1) <= c).all()
    assert all(b <= c for _, b in vis.running)
    # the hold of each signal's latest reading is only known up to the cursor
    last = vis.readings.sort_values("observed_at").groupby("signal_name").tail(1)
    assert np.allclose(last.held_s, (c - last.observed_at).dt.total_seconds())
    assert day.readings.observed_at.max() > c  # the input day is not truncated in place


# --- the key equivalence test -------------------------------------------------------------

@pytest.mark.parametrize("step_s,jitter", [(60, 0), (97, 11), (600, 0)])
def test_replay_step_by_step_equals_batch_3a3_scores(step_s, jitter):
    day, segs = make_day()
    batch = replay.batch_rows(day, CFG)
    rows, _ = replay_all(day, segs, cursors_every(day, step_s, jitter))
    assert len(batch) > 100 and any(r["state"] == "review_suggested" for r in batch)
    assert sorted(map(replay.row_key, rows)) == sorted(map(replay.row_key, batch))
    by_key = {replay.row_key(r): r for r in batch}
    for r in rows:
        assert r["scored_evidence"] == by_key[replay.row_key(r)]["scored_evidence"]


def test_replay_cases_equal_the_3a3_merge_rule_on_batch_episodes():
    day, segs = make_day()
    _, tracker = replay_all(day, segs, cursors_every(day, 120, 7))
    frame, _ = scoring.score_within_run_steady(scoring.prepare(day, CFG), CFG)
    eps = [{**e, "start": str(e["start"]), "end": str(e["end"])}
           for e in scoring.review_episodes(frame)]
    batch = scoring.cases_from_episodes(eps, day.running, CFG["cases"]["gap_s"])
    assert batch
    got = [(c["run"], c["start"], c["end"], c["signals"], c["episodes"])
           for c in tracker.cases()]
    assert got == [(c["run"], c["start"], c["end"], c["signals"], c["episodes"])
                   for c in batch]


def test_no_row_is_emitted_before_its_run_baselines_are_final():
    day, segs = make_day()
    consts = replay.day_constants(day, CFG)
    a = day.running[0][0]
    # temperature settles 30 min after the start, then a 20 min baseline: nothing before 50 min
    rows, _ = replay.step_rows(day, segs, consts, CFG, a + pd.Timedelta(minutes=49))
    assert rows == []
    rows, _ = replay.step_rows(day, segs, consts, CFG, a + pd.Timedelta(minutes=70))
    assert rows and all(pd.Timestamp(r["window_end"]) <= a + pd.Timedelta(minutes=70)
                        for r in rows)


def test_scores_at_a_cursor_do_not_depend_on_later_data():
    day, segs = make_day()
    consts = replay.day_constants(day, CFG)
    c = day.running[0][0] + pd.Timedelta(hours=2)
    garbage = day.readings.copy()
    later = garbage.observed_at > c
    garbage.loc[later, "value"] = 1e6
    garbage.loc[later, "quality_flags"] = [("stale_suspected",)] * int(later.sum())
    other = scoring.DayData(day.asset_id, day.source_day, garbage, day.samples_1m.assign(
        sample_count=np.where(day.samples_1m.bucket >= c, 0, day.samples_1m.sample_count)),
        day.running, stale_limits=day.stale_limits)
    a, _ = replay.step_rows(day, segs, consts, CFG, c)
    b, _ = replay.step_rows(other, segs, consts, CFG, c)
    assert a and [r["scored_evidence"] for r in a] == [r["scored_evidence"] for r in b]


def test_day_constants_are_the_declared_per_signal_reading_intervals():
    day, _ = make_day()
    consts = replay.day_constants(day, CFG)
    prep = scoring.prepare(day, CFG)
    assert consts["intervals"] == prep.intervals
    assert consts["signals"] == prep.signals
    assert consts["max_window_s"] == max(scoring.signal_window_s(prep, s, CFG)
                                         for s in prep.signals)


# --- pacing ------------------------------------------------------------------------------

@pytest.mark.parametrize("speed", replay.SPEEDS)
def test_cursor_moves_in_source_time_at_the_speed_setting(speed):
    anchor_c, anchor_w = T0, pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
    end = T0 + pd.Timedelta(hours=8)
    now = anchor_w + pd.Timedelta(seconds=30)
    assert replay.target_cursor(anchor_c, anchor_w, speed, now, end) == \
        T0 + pd.Timedelta(seconds=30 * speed)
    assert replay.target_cursor(anchor_c, anchor_w, speed, anchor_w + pd.Timedelta(days=9),
                                end) == end
    assert replay.SPEEDS == (1, 10, 60)


# --- streaming cases ---------------------------------------------------------------------

def _row(sig, start_min, w_min=1, run=0, state="review_suggested"):
    s = T0 + pd.Timedelta(minutes=start_min)
    return {"signal_name": sig, "stretch": run, "window_start": s,
            "window_end": s + pd.Timedelta(minutes=w_min), "state": state, "score": 1.5,
            "model_version": "v"}


def test_case_tracker_waits_for_windows_that_start_earlier_to_end():
    # c reviews 07:07-07:08. b (7 min windows) starts reviewing at 07:20, a (1 min windows) at
    # 07:25. Batch sorts episodes by start: b joins c's case (12 min gap), then a joins too.
    # a's window ends first, so deciding on a before b's window has ended would open a case.
    tr = replay.CaseTracker(CFG, {"max_window_s": 420})
    c1, a1, b1 = _row("c", 7), _row("a", 25), _row("b", 20, w_min=7)
    tr.feed([c1], watermark=T0 + pd.Timedelta(minutes=9))
    tr.feed([c1, a1], watermark=T0 + pd.Timedelta(minutes=19))  # cursor 07:26: a waits
    tr.feed([c1, a1, b1], watermark=T0 + pd.Timedelta(minutes=33))
    assert [c["signals"] for c in tr.cases()] == [["a", "b", "c"]]
    assert [e["type"] for e in tr.events()] == ["opened", "evidence_added", "evidence_added",
                                                "evidence_added"]


def test_case_tracker_refuses_rows_older_than_its_watermark():
    tr = replay.CaseTracker(CFG, {"max_window_s": 60})
    tr.feed([_row("a", 25)], watermark=T0 + pd.Timedelta(minutes=30))
    with pytest.raises(ValueError, match="out of order"):
        tr.feed([_row("a", 25), _row("b", 20)], watermark=T0 + pd.Timedelta(minutes=40))


def test_case_tracker_extends_within_the_gap_and_opens_after_it():
    tr = replay.CaseTracker(CFG, {"max_window_s": 60})
    rows = [_row("a", 0), _row("a", 1), _row("b", 10), _row("a", 30), _row("a", 200, run=1)]
    tr.feed(rows, watermark=replay.END_OF_TIME)
    got = [(c["run"], c["signals"], c["episodes"]) for c in tr.cases()]
    assert got == [(0, ["a", "b"], 2), (0, ["a"], 1), (1, ["a"], 1)]


# --- synthetic scenarios -----------------------------------------------------------------

def test_scenarios_come_from_the_3a_injection_code():
    assert "B_stuck_pressure" in replay.SCENARIOS
    for name, sc in replay.SCENARIOS.items():
        assert sc["fault"] in scoring.FAULTS
        assert name.startswith(sc["asset_id"].rsplit("-", 1)[1] + "_")


def test_scenario_is_applied_in_memory_and_marks_the_day_synthetic():
    day, _ = make_day()
    before = day.readings.copy(deep=True)
    before_s = day.samples_1m.copy(deep=True)
    out = replay.apply_scenario(day, "B_stuck_pressure", CFG)
    assert out.synthetic and out.injections[0]["fault"] == "stuck"
    pd.testing.assert_frame_equal(day.readings, before)
    pd.testing.assert_frame_equal(day.samples_1m, before_s)
    assert not day.synthetic
    with pytest.raises(ValueError, match="asset"):
        replay.apply_scenario(scoring.DayData("cira-pump-A", day.source_day, day.readings,
                                              day.samples_1m, day.running), "B_stuck_pressure",
                              CFG)
    with pytest.raises(ValueError, match="unknown scenario"):
        replay.apply_scenario(day, "B_meteor_strike", CFG)


def test_stuck_scenario_replays_to_stale_evidence_and_equals_batch():
    day, segs = make_day()
    sday = replay.apply_scenario(day, "B_stuck_pressure", CFG)
    rows, _ = replay_all(sday, segs, cursors_every(sday, 180))
    batch = replay.batch_rows(sday, CFG)
    by_key = {replay.row_key(r): r["scored_evidence"] for r in batch}
    assert {replay.row_key(r): r["scored_evidence"] for r in rows} == by_key
    stale = [r for r in rows if r["signal_name"] == "outlet_pressure"
             and (r["reason"] or "").startswith("stale_suspected")]
    assert len(stale) > 1
    # the reason reports the hold as known at the window end, never the full hold
    held = [float(r["reason"].split("held ")[1].split(" s")[0]) for r in stale]
    assert held == sorted(held) and len(set(held)) > 1


# --- baseline progress (for a "baseline forming" view) -----------------------------------

def _progress(day, segs, minutes):
    consts = replay.day_constants(day, CFG)
    c = day.running[0][0] + pd.Timedelta(minutes=minutes)
    return replay.step(day, segs, consts, CFG, c)[2]


def test_baseline_progress_per_signal_moves_from_settling_to_formed():
    day, segs = make_day()
    a = day.running[0][0]
    p = _progress(day, segs, 2)["runs"][0]["signals"]
    assert p["outlet_pressure"]["status"] == "settling"  # settles 5 min after the start
    assert p[TEMP]["status"] == "settling"  # 30 min
    assert p["pump_vibration_velocity"]["status"] == "waiting_for_reading"  # from 40 min
    p = _progress(day, segs, 10)["runs"][0]["signals"]["outlet_pressure"]
    assert p["status"] == "forming" and p["fraction"] == pytest.approx(300 / 1200)
    assert pd.Timestamp(p["baseline_start"]) == a + pd.Timedelta(minutes=5)
    assert pd.Timestamp(p["baseline_end"]) == a + pd.Timedelta(minutes=25)
    p = _progress(day, segs, 26)["runs"][0]["signals"]
    assert p["outlet_pressure"]["status"] == "formed" and p["outlet_pressure"]["fraction"] == 1
    assert p[TEMP]["status"] == "settling"
    assert p["pump_vibration_velocity"]["status"] == "waiting_for_reading"
    p = _progress(day, segs, 45)["runs"][0]["signals"]
    assert p[TEMP]["status"] == "forming"  # 30-50 min
    assert p["pump_vibration_velocity"]["status"] == "forming"  # first reading 40, 40-60 min
    p = _progress(day, segs, 70)["runs"][0]["signals"]
    assert {v["status"] for v in p.values()} == {"formed"}


def test_baseline_progress_reports_abstention_with_its_reason():
    day, segs = make_day()
    cfg = scoring.merge_config(CFG, {"onset": {"settling_s": {"vibration": None}}})
    consts = replay.day_constants(day, cfg)
    c = day.running[0][0] + pd.Timedelta(minutes=70)
    p = replay.step(day, segs, consts, cfg, c)[2]["runs"][0]["signals"]
    assert p["pump_vibration_velocity"]["status"] == "abstained"
    assert "no settling time" in p["pump_vibration_velocity"]["reason"]


# --- evidence for a closed case ----------------------------------------------------------

def test_evidence_for_a_closed_case_opens_a_new_one_related_to_it():
    tr = replay.CaseTracker(CFG, {"max_window_s": 60})
    tr.feed([_row("a", 0), _row("a", 1)], watermark=T0 + pd.Timedelta(minutes=2))
    tr.set_id(0, 101)
    tr.mark_closed([101])
    # continues a's episode, and b would merge within the gap: both go to a new case
    ev = tr.feed([_row("a", 2), _row("b", 5)], watermark=T0 + pd.Timedelta(minutes=6))
    opened = [e for e in ev if e["type"] == "opened"]
    assert len(opened) == 1 and opened[0]["related_case"] == 0
    assert [c["signals"] for c in tr.cases()] == [["a"], ["a", "b"]]
    # an ordinary new case (after the gap) is related to nothing
    ev = tr.feed([_row("c", 60)], watermark=T0 + pd.Timedelta(minutes=61))
    assert [e.get("related_case") for e in ev if e["type"] == "opened"] == [None]
