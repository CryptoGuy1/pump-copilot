"""Step 3a-2 (post-hoc revision after 3a results): per-signal windows, stale-flag stuck
detection, within-run baseline mode."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pumpcopilot import scoring
from pumpcopilot.schema import PresentationState

P = PresentationState
T0 = pd.Timestamp("2024-06-11 08:00:00", tz="UTC")
LIMITS = {"outlet_pressure": 150.0, "motor_casing_temperature": 30.0}
REV = scoring.merge_config({
    "features": {"window_readings": 6, "min_fresh_readings": 5, "step_s": 60,
                 "window_floor_s": 60, "stale_via_flag": True},
    "baseline": {"k": 4.0, "floor_fraction": 0.001, "min_fit_running_s": 3600,
                 "min_fit_readings": 50},
    "review": {"consecutive_windows": 3},
})
WR = scoring.merge_config(REV, {"within_run": {"baseline_s": 1800, "min_baseline_s": 900,
                                               "min_baseline_readings": 15}})


def _sig(name, unit, every, mean, sd, start, hours, rng):
    t = start + pd.to_timedelta(np.arange(0, hours * 3600, every), unit="s")
    return pd.DataFrame({"observed_at": t, "signal_name": name, "unit": unit,
                         "value": rng.normal(mean, sd, len(t)), "held_s": float(every),
                         "operating_state": "running", "quality_flags": [()] * len(t),
                         "provenance_hash": [f"{name}:{start}:{i}" for i in range(len(t))]})


def make_day(runs=((0.0, 4.0, 41.0),), temp_every=8, seed=0, day=dt.date(2024, 6, 11)):
    """runs: (start hour, length h, pressure level). Pressure every 60 s, casing temperature
    every `temp_every` s, ambient every 8 s."""
    rng = np.random.default_rng(seed)
    parts, running = [], []
    for h0, length, level in runs:
        start = T0 + pd.Timedelta(hours=h0)
        parts += [_sig("outlet_pressure", "bar", 60, level, 0.3, start, length, rng),
                  _sig("motor_casing_temperature", "degC", temp_every, 35.0, 0.2, start, length,
                       rng),
                  _sig("ambient_temperature", "degC", 8, 20.0, 0.1, start, length, rng)]
        running.append((start, start + pd.Timedelta(hours=length)))
    r = pd.concat(parts, ignore_index=True)
    minutes = pd.date_range(running[0][0], running[-1][1], freq="60s", inclusive="left")
    s = pd.DataFrame([(m, sig, 60) for m in minutes for sig in r.signal_name.unique()],
                     columns=["bucket", "signal_name", "sample_count"])
    return scoring.DayData("cira-pump-B", day, r, s, running, stale_limits=dict(LIMITS))


# --- per-signal windows ------------------------------------------------------------------

def test_window_length_follows_each_signals_update_interval():
    prep = scoring.prepare(make_day(), REV)
    assert scoring.signal_window_s(prep, "outlet_pressure", REV) == 360  # 6 x 60 s
    assert scoring.signal_window_s(prep, "motor_casing_temperature_rel_ambient", REV) == 60
    f = scoring.features(prep, REV)
    for sig, w in (("outlet_pressure", 360), ("motor_casing_temperature_rel_ambient", 60)):
        g = f[f.signal_name == sig]
        assert ((g.end - g.start).dt.total_seconds() == w).all()
        assert (g.fresh >= 5).all()


def test_statistical_minimum_replaces_the_fit_day_minimum():
    # fit day: temperature every 8 s; scored day: every 60 s (like B June -> October)
    fit = scoring.prepare(make_day(seed=1), REV)
    base = scoring.fit_baseline(fit, REV)
    frame = scoring.score_frame(scoring.prepare(make_day(temp_every=60, seed=2), REV), base, REV)
    t = frame[frame.signal_name == "motor_casing_temperature_rel_ambient"]
    assert base.bands["motor_casing_temperature_rel_ambient"]["min_fresh"] == 5
    assert (t.state != P.INSUFFICIENT_EVIDENCE).mean() > 0.9


# --- stuck sensors through the stale flag ------------------------------------------------

def test_stuck_injection_sets_the_stale_flag_like_the_pipeline():
    day = make_day()
    t = T0 + pd.Timedelta(hours=2)
    out = scoring.inject(day, "outlet_pressure", "stuck", 1200, t, sigma=0.3, cfg=REV)
    p = out.readings[out.readings.signal_name == "outlet_pressure"]
    held = p[p.observed_at < t].iloc[-1]
    assert held.held_s == pytest.approx(1200 + 60)  # held from its time until the next reading
    assert "stale_suspected" in held.quality_flags
    short = scoring.inject(day, "outlet_pressure", "stuck", 20, t, sigma=0.3, cfg=REV)
    q = short.readings[short.readings.signal_name == "outlet_pressure"]
    # the reading at t is gone, so the previous one is held 120 s < 150 s: not stale
    assert "stale_suspected" not in q[q.observed_at < t].iloc[-1].quality_flags


def test_stuck_sensor_is_detected_via_stale_not_via_reading_count():
    day = make_day(seed=3)
    base = scoring.fit_baseline(scoring.prepare(make_day(seed=1), REV), REV)
    t = T0 + pd.Timedelta(hours=2)
    res = scoring.detect(day, base, REV, "outlet_pressure", "stuck", 1200, t, sigma=0.3,
                         horizon_s=1800)
    assert res["detected"] and any("stale_suspected" in r for r in res["reasons"])
    frame = scoring.score_frame(
        scoring.prepare(scoring.inject(day, "outlet_pressure", "stuck", 1200, t, 0.3, REV), REV),
        base, REV)
    stale = frame[frame.reason.fillna("").str.startswith("stale_suspected")]
    assert len(stale) and (stale.state == P.INSUFFICIENT_EVIDENCE).all()


# --- within-run mode ---------------------------------------------------------------------

def test_within_run_baseline_comes_from_the_start_of_each_run():
    # two runs at different pressure levels: each run is judged against its own start
    day = make_day(runs=((0.0, 2.0, 41.0), (3.0, 2.0, 45.0)), seed=4)
    frame, bases = scoring.score_within_run(scoring.prepare(day, WR), WR)
    assert len(bases) == 2 and bases[0].model_version != bases[1].model_version
    assert bases[0].model_id == bases[1].model_id
    assert bases[1].bands["outlet_pressure"]["center"] == pytest.approx(45.0, abs=0.3)
    for k, (a, _) in enumerate(day.running):
        g = frame[frame.stretch == k]
        assert (g.start >= a + pd.Timedelta(seconds=1800)).all()  # baseline period not scored
    p = frame[frame.signal_name == "outlet_pressure"]
    assert (p.state == P.REVIEW_SUGGESTED).mean() < 0.05


def test_within_run_abstains_for_runs_shorter_than_the_baseline():
    day = make_day(runs=((0.0, 0.4, 41.0), (1.0, 2.0, 41.0)))
    frame, bases = scoring.score_within_run(scoring.prepare(day, WR), WR)
    assert bases[0].abstained and "shorter" in bases[0].abstained
    assert bases[1].abstained is None


def test_within_run_baseline_has_a_minimum():
    bad = scoring.merge_config(WR, {"within_run": {"baseline_s": 600}})
    with pytest.raises(ValueError, match="minimum"):
        scoring.score_within_run(scoring.prepare(make_day(), bad), bad)


# --- protocol ----------------------------------------------------------------------------

def _june_only_loader(calls):
    def loader(asset_id, source_day):
        calls.append((asset_id, source_day))
        return make_day(runs=((0.0, 6.0, 41.0),), seed=5, day=source_day)
    return loader


def test_revised_tuning_and_within_run_tuning_load_only_b_june():
    for fn, grid in ((scoring.tune, {"k": [4.0], "consecutive_windows": [2, 3],
                                     "window_readings": [6, 10], "step_s": [60]}),
                     (scoring.tune_within_run, {"k": [4.0], "consecutive_windows": [2, 3],
                                                "window_readings": [6],
                                                "baseline_s": [1200, 1800]})):
        calls = []
        frozen, table = fn(_june_only_loader(calls), REV if fn is scoring.tune else WR, grid,
                           starts_per_fault=1)
        assert calls == [("cira-pump-B", dt.date(2024, 6, 11))]
        assert len(table) == 4
        assert frozen["frozen"]["label"] == "post-hoc revision after 3a results"
    assert frozen["within_run"]["baseline_s"] in (1200, 1800)


def test_revision_report_is_labelled_and_side_by_side():
    from pumpcopilot import scoring_report

    def loader(asset_id, source_day):
        return make_day(runs=((0.0, 5.0, 41.0),), seed=source_day.month, day=source_day)

    across = scoring.merge_config(REV, {"injection": {"signals": ["outlet_pressure"]}})
    within = scoring.merge_config(WR, {"injection": {"signals": ["outlet_pressure"]}})
    for c in (across, within):
        c["frozen"] = {"label": "post-hoc revision after 3a results", "grid": {},
                       "selected": {}, "objective": "x"}
    res = scoring.evaluate_revision(loader, across, within, starts_per_fault=1)
    assert set(res) >= {"across_day", "within_run"}
    wr_b = res["within_run"]["pumps"]["B"]
    assert "unlabelled_reviews_per_running_hour" in wr_b
    md = scoring_report.revision_markdown(across, within, res, baseline_3a=None)
    assert md.count("post-hoc revision after 3a results") >= 3
    assert "| fault | size | across-day" in md and "within-run" in md


def test_within_run_band_needs_a_minimum_number_of_baseline_windows():
    # 20 readings x 60 s = 20 min pressure windows cannot fit a 20 min baseline 5 times
    cfg = scoring.merge_config(WR, {"features": {"window_readings": 20},
                                    "within_run": {"baseline_s": 1200,
                                                   "min_baseline_windows": 5}})
    _, bases = scoring.score_within_run(scoring.prepare(make_day(), cfg), cfg)
    assert "outlet_pressure" not in bases[0].bands
    assert "fit windows < 5" in bases[0].signal_abstentions["outlet_pressure"]


def test_tuning_never_selects_a_setting_that_scores_nothing():
    calls = []
    grid = {"k": [4.0], "consecutive_windows": [3], "window_readings": [6, 200],
            "baseline_s": [1800]}
    frozen, table = scoring.tune_within_run(_june_only_loader(calls), WR, grid,
                                            starts_per_fault=1)
    empty = [r for r in table if r["window_readings"] == 200]
    assert empty and empty[0]["evaluable_windows"] == 0 and empty[0]["unlabelled_reviews"] == 0
    assert frozen["frozen"]["selected"]["window_readings"] == 6


def test_stale_evidence_is_causal():
    # a reading can only be known stale once it has been held past its limit
    day = make_day(seed=3)
    t = T0 + pd.Timedelta(hours=2)
    out = scoring.inject(day, "outlet_pressure", "stuck", 1200, t, sigma=0.3, cfg=REV)
    prep = scoring.prepare(out, REV)
    st = prep.stale[prep.stale.signal_name == "outlet_pressure"].iloc[0]
    held_at = t - pd.Timedelta(seconds=60)  # the last reading before the fault
    assert st.start == held_at + pd.Timedelta(seconds=LIMITS["outlet_pressure"])
    base = scoring.fit_baseline(scoring.prepare(make_day(seed=1), REV), REV)
    res = scoring.detect(day, base, REV, "outlet_pressure", "stuck", 1200, t, sigma=0.3,
                         horizon_s=1800)
    assert res["delay_s"] >= LIMITS["outlet_pressure"] - 60  # not before the limit elapsed
