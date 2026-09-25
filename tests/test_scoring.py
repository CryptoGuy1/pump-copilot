import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pumpcopilot import scoring
from pumpcopilot.schema import CalibrationStatus, PresentationState, ScoredEvidence, SourceDataset

P = PresentationState
T0 = pd.Timestamp("2024-06-11 08:00:00", tz="UTC")
CFG = scoring.merge_config({
    "features": {"window_s": 600, "step_s": 60, "min_fresh_readings": 3,
                 "min_fresh_fraction": 0.5},
    "baseline": {"k": 4.0, "floor_fraction": 0.001, "min_fit_running_s": 3600,
                 "min_fit_readings": 50},
    "review": {"consecutive_windows": 3},
})


def _signal(name, unit, every_s, mean, sd, hours, rng, start=T0):
    t = start + pd.to_timedelta(np.arange(0, hours * 3600, every_s), unit="s")
    return pd.DataFrame({"observed_at": t, "signal_name": name, "unit": unit,
                         "value": rng.normal(mean, sd, len(t)), "held_s": float(every_s),
                         "operating_state": "running", "quality_flags": [()] * len(t),
                         "provenance_hash": [f"{name}:{i}" for i in range(len(t))]})


def make_day(hours=4.0, ambient=True, seed=0, day=dt.date(2024, 6, 11)):
    """A running pump-day: pressure every 60 s, casing temperature and ambient every 8 s."""
    rng = np.random.default_rng(seed)
    parts = [_signal("outlet_pressure", "bar", 60, 41.0, 0.3, hours, rng),
             _signal("motor_casing_temperature", "degC", 8, 35.0, 0.2, hours, rng)]
    if ambient:
        parts.append(_signal("ambient_temperature", "degC", 8, 20.0, 0.1, hours, rng))
        parts.append(_signal("ambient_pressure", "mbar", 8, 1012.0, 0.1, hours, rng))
    r = pd.concat(parts, ignore_index=True)
    minutes = pd.date_range(T0, T0 + pd.Timedelta(hours=hours), freq="60s", inclusive="left")
    s = pd.DataFrame([(m, sig, 60) for m in minutes for sig in r.signal_name.unique()],
                     columns=["bucket", "signal_name", "sample_count"])
    return scoring.DayData("cira-pump-B", day, r, s, [(T0, T0 + pd.Timedelta(hours=hours))])


def _frame(day, fit_day=None, cfg=CFG):
    base = scoring.fit_baseline(scoring.prepare(fit_day or make_day(seed=1), cfg), cfg)
    return scoring.score_frame(scoring.prepare(day, cfg), base, cfg), base


# --- features ----------------------------------------------------------------------------

def test_features_use_only_eligible_running_readings():
    day = make_day()
    r = day.readings
    t = T0 + pd.Timedelta(minutes=30, seconds=5)
    bad = pd.DataFrame({
        "observed_at": [t] * 4, "signal_name": "outlet_pressure", "unit": "bar",
        "value": 999.0, "held_s": 1.0,
        "operating_state": ["transition", "running", "running", "off"],
        "quality_flags": [(), ("stale_suspected",), ("spike_suspected",), ()],
        "provenance_hash": ["x1", "x2", "x3", "x4"]})
    dirty = scoring.DayData(day.asset_id, day.source_day, pd.concat([r, bad]),
                            day.samples_1m, day.running)
    f_clean = scoring.features(scoring.prepare(day, CFG), CFG)
    f_dirty = scoring.features(scoring.prepare(dirty, CFG), CFG)
    pd.testing.assert_frame_equal(f_clean, f_dirty)
    row = f_clean[f_clean.signal_name == "outlet_pressure"].iloc[0]
    assert row["fresh"] == 10  # 600 s window, one reading per 60 s
    assert row["median"] == pytest.approx(41.0, abs=0.3)
    assert row["mad"] > 0


def test_temperatures_are_normalized_against_ambient_when_present():
    prep = scoring.prepare(make_day(), CFG)
    assert "motor_casing_temperature_rel_ambient" in prep.signals
    assert "motor_casing_temperature" not in prep.signals
    assert {"ambient_temperature", "ambient_pressure"}.isdisjoint(prep.signals)
    v = prep.readings[prep.readings.signal_name == "motor_casing_temperature_rel_ambient"]
    assert v.value.median() == pytest.approx(15.0, abs=0.3)
    assert set(v.unit) == {"K"}
    assert "ambient_temperature" in prep.normalization
    assert "ambient_pressure" in prep.normalization  # barometric signal found and reported


def test_missing_ambient_signal_is_reported_not_guessed():
    prep = scoring.prepare(make_day(ambient=False), CFG)
    assert "motor_casing_temperature" in prep.signals
    assert "no ambient" in prep.normalization and "no barometric" in prep.normalization


# --- baseline ----------------------------------------------------------------------------

def test_baseline_band_is_median_plus_minus_k_mad_of_window_medians():
    prep = scoring.prepare(make_day(), CFG)
    base = scoring.fit_baseline(prep, CFG)
    f = scoring.features(prep, CFG)
    med = f[(f.signal_name == "outlet_pressure") & (f.fresh >= 3)]["median"]
    band = base.bands["outlet_pressure"]
    mad = float(np.median(np.abs(med - med.median())))
    assert band["center"] == pytest.approx(med.median())
    assert band["halfwidth"] == pytest.approx(4.0 * mad)
    assert band["low"] == pytest.approx(med.median() - 4.0 * mad)
    assert base.abstained is None


def test_baseline_abstains_below_minimum_fit_duration():
    base = scoring.fit_baseline(scoring.prepare(make_day(hours=0.5), CFG), CFG)
    assert base.abstained and "running" in base.abstained
    frame = scoring.score_frame(scoring.prepare(make_day(), CFG), base, CFG)
    assert set(frame.state) == {P.INSUFFICIENT_EVIDENCE}
    assert frame.reason.str.contains("abstained").all()


def test_signal_without_enough_fit_readings_has_no_band():
    cfg = scoring.merge_config(CFG, {"baseline": {"min_fit_readings": 1000}})
    base = scoring.fit_baseline(scoring.prepare(make_day(), cfg), cfg)
    assert "outlet_pressure" not in base.bands  # 240 readings in 4 h
    assert "outlet_pressure" in base.signal_abstentions
    assert "motor_casing_temperature_rel_ambient" in base.bands  # 1800 readings


# --- scoring -----------------------------------------------------------------------------

def test_review_needs_n_consecutive_windows_outside_the_band():
    day = make_day(seed=2)
    t_fault = T0 + pd.Timedelta(hours=2)
    faulty = scoring.inject(day, "outlet_pressure", "step", 20.0, t_fault, sigma=0.3, cfg=CFG)
    frame, _ = _frame(faulty)
    p = frame[frame.signal_name == "outlet_pressure"].reset_index(drop=True)
    review = p.state == P.REVIEW_SUGGESTED
    assert review.any()
    assert (review == (p.run >= 3)).all()  # N = 3 consecutive outside windows, not fewer
    assert (p[p.run.isin([1, 2])].state == P.NORMAL).all()
    first = p.index[review & (p.end > t_fault)][0]  # windows ending after the fault
    assert p.loc[first - 2:first, "outside"].all() and not p.loc[first - 3, "outside"]


def test_no_samples_is_data_unavailable_and_few_readings_insufficient():
    day = make_day(seed=3)
    t = T0 + pd.Timedelta(hours=2)
    frame, _ = _frame(scoring.inject(day, "outlet_pressure", "dropout", 1800, t, 0.3, CFG))
    p = frame[frame.signal_name == "outlet_pressure"]
    inside = p[(p.start >= t) & (p.end <= t + pd.Timedelta(seconds=1800))]
    assert len(inside) and (inside.state == P.DATA_UNAVAILABLE).all()
    frame, _ = _frame(scoring.inject(day, "outlet_pressure", "stuck", 1800, t, 0.3, CFG))
    p = frame[frame.signal_name == "outlet_pressure"]
    inside = p[(p.start >= t) & (p.end <= t + pd.Timedelta(seconds=1800))]
    assert len(inside) and (inside.state == P.INSUFFICIENT_EVIDENCE).all()
    assert inside.reason.str.contains("fresh readings").all()


def test_scored_evidence_uses_the_schema_with_cited_evidence():
    day = make_day(seed=2)
    faulty = scoring.inject(day, "outlet_pressure", "step", 20.0, T0 + pd.Timedelta(hours=2),
                            sigma=0.3, cfg=CFG)
    frame, base = _frame(faulty)
    out = scoring.to_scored_evidence(frame, base, CFG)
    assert len(out) == len(frame) and all(isinstance(e, ScoredEvidence) for e in out)
    assert {e.source_dataset for e in out} == {SourceDataset.CIRA}
    assert {e.confidence_calibration_status for e in out} == {CalibrationStatus.NOT_APPLICABLE}
    review = [e for e in out if e.presentation_state == P.REVIEW_SUGGESTED]
    assert review
    for e in review:
        assert len(e.evidence) == 3
        for item in e.evidence:
            assert item.signal_name == "outlet_pressure" and item.unit == "bar"
            assert item.value is not None and item.baseline_low < item.baseline_high
            assert not (item.baseline_low <= item.value <= item.baseline_high)
            assert "outside" in item.statement
    assert all(e.abstention_reason for e in out
               if e.presentation_state in (P.INSUFFICIENT_EVIDENCE, P.DATA_UNAVAILABLE))
    assert {e.model_id for e in out} == {base.model_id}


def test_model_id_and_version_hash_config_and_fit_data():
    fit = scoring.prepare(make_day(seed=1), CFG)
    a = scoring.fit_baseline(fit, CFG)
    assert (a.model_id, a.model_version) == (scoring.fit_baseline(fit, CFG).model_id,
                                             scoring.fit_baseline(fit, CFG).model_version)
    other_cfg = scoring.merge_config(CFG, {"baseline": {"k": 5.0}})
    b = scoring.fit_baseline(fit, other_cfg)
    assert b.model_id != a.model_id and b.model_version != a.model_version
    c = scoring.fit_baseline(scoring.prepare(make_day(seed=9), CFG), CFG)
    assert c.model_id == a.model_id and c.model_version != a.model_version


# --- synthetic injection -----------------------------------------------------------------

@pytest.mark.parametrize("fault", scoring.FAULTS)
def test_injection_is_in_memory_and_labelled(fault):
    day = make_day()
    before = day.readings.copy()
    t = T0 + pd.Timedelta(hours=1)
    size = 1800 if fault in ("stuck", "dropout") else 2.0
    out = scoring.inject(day, "outlet_pressure", fault, size, t, sigma=0.5, cfg=CFG)
    pd.testing.assert_frame_equal(day.readings, before)  # the input is never modified
    assert out.synthetic and not day.synthetic
    hit = out.readings[out.readings.provenance == "synthetic_injection"]
    assert fault == "dropout" or fault == "stuck" or len(hit)
    p0 = before[before.signal_name == "outlet_pressure"].set_index("observed_at").value
    p1 = out.readings[out.readings.signal_name == "outlet_pressure"].set_index("observed_at").value
    after = p0.index >= t
    if fault == "step":
        assert np.allclose(p1[after] - p0[after], 1.0) and np.allclose(p1[~after], p0[~after])
    elif fault in ("ramp", "drift"):
        d = (p1 - p0)[after]
        assert d.iloc[0] == pytest.approx(0) and d.is_monotonic_increasing
        assert d.max() == pytest.approx(1.0)
    else:
        gone = (p0.index >= t) & (p0.index < t + pd.Timedelta(seconds=1800))
        assert not p1.index.isin(p0.index[gone]).any()
        assert p1.index.isin(p0.index[~gone]).all()
        s = out.samples_1m[(out.samples_1m.signal_name == "outlet_pressure")
                           & (out.samples_1m.bucket >= t)
                           & (out.samples_1m.bucket < t + pd.Timedelta(seconds=1800))]
        assert (s.sample_count == 0).all() if fault == "dropout" else (s.sample_count > 0).all()


def test_detection_of_a_large_step_and_not_of_a_zero_step():
    day = make_day(hours=5, seed=4)
    prep_fit = scoring.prepare(make_day(hours=5, seed=1), CFG)
    base = scoring.fit_baseline(prep_fit, CFG)
    t = T0 + pd.Timedelta(hours=2)
    big = scoring.detect(day, base, CFG, "outlet_pressure", "step", 20.0, t, sigma=0.3,
                         horizon_s=3600)
    assert big["detected"] and 0 < big["delay_s"] <= 600 + 3 * 60
    none = scoring.detect(day, base, CFG, "outlet_pressure", "step", 0.0, t, sigma=0.3,
                          horizon_s=3600)
    assert not none["detected"]


# --- protocol ----------------------------------------------------------------------------

def test_unlabelled_review_rate_counts_episodes_per_running_hour():
    frame = pd.DataFrame({
        "signal_name": ["a"] * 6 + ["b"] * 3,
        "stretch": 0,
        "state": [P.NORMAL, P.REVIEW_SUGGESTED, P.REVIEW_SUGGESTED, P.NORMAL,
                  P.REVIEW_SUGGESTED, P.NORMAL, P.NORMAL, P.NORMAL, P.NORMAL],
    })
    assert len(scoring.review_episodes(frame)) == 2
    assert scoring.unlabelled_review_rate(frame, running_hours=4.0) == pytest.approx(0.5)


def test_tuning_only_ever_loads_b_june():
    calls = []

    def loader(asset_id, source_day):
        calls.append((asset_id, source_day))
        return make_day(hours=6, seed=5, day=source_day)

    grid = {"k": [4.0, 6.0], "consecutive_windows": [2, 3], "window_s": [600],
            "step_s": [60]}
    frozen, table = scoring.tune(loader, CFG, grid, starts_per_fault=1)
    assert calls == [("cira-pump-B", dt.date(2024, 6, 11))]
    assert len(table) == 4
    assert frozen["frozen"]["tuned_on"] == {"asset_id": "cira-pump-B",
                                            "source_day": "2024-06-11"}
    assert frozen["baseline"]["k"] in (4.0, 6.0)
    assert frozen["review"]["consecutive_windows"] in (2, 3)


# --- database ----------------------------------------------------------------------------

@pytest.mark.db
def test_day_from_database_has_flags_segments_and_samples(loaded):
    conn = loaded[0]
    day = scoring.load_day(conn, "cira-pump-A", dt.date(2024, 4, 10))
    assert len(day.readings) and "quality_flags" in day.readings
    assert day.running and all(a < b for a, b in day.running)
    assert day.samples_1m.sample_count.sum() > 0
    assert set(day.readings.provenance) == {"real"}


@pytest.mark.db
def test_scoring_and_injection_never_write_to_the_database(loaded):
    conn = loaded[0]
    tables = ["telemetry", "readings", "state_segments", "ingest_runs"]

    def counts():
        return [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables]

    before = counts()
    day = scoring.load_day(conn, "cira-pump-A", dt.date(2024, 4, 10))
    cfg = scoring.merge_config(CFG, {"baseline": {"min_fit_running_s": 60,
                                                  "min_fit_readings": 5}})
    base = scoring.fit_baseline(scoring.prepare(day, cfg), cfg)
    t = day.running[0][0] + pd.Timedelta(seconds=60)
    for fault in scoring.FAULTS:
        faulty = scoring.inject(day, "outlet_pressure", fault, 60.0, t, 1.0, cfg)
        scoring.score_frame(scoring.prepare(faulty, cfg), base, cfg)
    assert counts() == before


def test_evaluation_labels_real_and_synthetic_and_reports_detectability():
    from pumpcopilot import scoring_report

    def loader(asset_id, source_day):
        return make_day(hours=5, seed=hash((asset_id, str(source_day))) % 100, day=source_day)

    cfg = scoring.merge_config(CFG, {"injection": {"signals": ["outlet_pressure"]}})
    res = scoring.evaluate(loader, cfg, starts_per_fault=1)
    b = res["pumps"]["B"]
    assert b["abstained"] is None and "unlabelled_reviews_per_running_hour" in b
    diag = b["diagnostics"]["outlet_pressure"]
    assert {"fit_interval_s", "score_interval_s", "fit_median", "score_median"} <= set(diag)
    assert 0.0 <= res["synthetic"]["clean_normal_share"]["outlet_pressure"] <= 1.0
    cfg["frozen"] = {"tuned_on": {}, "split": {"validation_running_hours": 1.0}, "grid": {},
                     "starts_per_fault": 1, "objective": "x",
                     "selected": {"window_s": 600, "step_s": 60, "k": 4.0,
                                  "consecutive_windows": 3, "unlabelled_reviews": 0,
                                  "unlabelled_reviews_per_running_hour": 0.0,
                                  "mean_detection_rate": 1.0, "median_delay_s": 60.0,
                                  "injections": 1}}
    md = scoring_report.markdown(cfg, [cfg["frozen"]["selected"]], res)
    assert "## REAL: pump B" in md and "## SYNTHETIC" in md
    assert "clean October windows normal" in md and "post-hoc" in md


def test_abstaining_pump_gets_a_labelled_exploratory_run():
    from pumpcopilot import scoring_report

    def loader(asset_id, source_day):
        short_a_june = asset_id == "cira-pump-A" and source_day.month == 6
        return make_day(hours=1.5 if short_a_june else 5, seed=source_day.month, day=source_day)

    cfg = scoring.merge_config(CFG, {"baseline": {"min_fit_running_s": 7200},
                                     "injection": {"signals": ["outlet_pressure"]}})
    res = scoring.evaluate(loader, cfg, starts_per_fault=1, exploratory_min_fit_s=3600)
    a = res["pumps"]["A"]
    assert a["abstained"] and "1.50 h running" in a["abstained"]
    exp = a["exploratory"]
    assert exp["min_fit_running_s"] == 3600 and exp["abstained"] is None
    assert "unlabelled_reviews_per_running_hour" in exp
    cfg["frozen"] = {"tuned_on": {}, "split": {"validation_running_hours": 1.0}, "grid": {},
                     "starts_per_fault": 1, "objective": "x",
                     "selected": {"window_s": 600, "step_s": 60, "k": 4.0,
                                  "consecutive_windows": 3, "unlabelled_reviews": 0,
                                  "unlabelled_reviews_per_running_hour": 0.0,
                                  "mean_detection_rate": 1.0, "median_delay_s": 60.0,
                                  "injections": 1}}
    md = scoring_report.markdown(cfg, [cfg["frozen"]["selected"]], res)
    assert "EXPLORATORY" in md and "no fault labels" in md
    assert "false review" not in md.lower()
