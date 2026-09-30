"""Step 3a-3 (pre-registered final revision): steady-state baseline start, cases."""
import datetime as dt
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pumpcopilot import scoring
from pumpcopilot.schema import PresentationState

P = PresentationState
T0 = pd.Timestamp("2024-06-11 08:00:00", tz="UTC")
S3 = scoring.merge_config({
    "features": {"window_readings": 6, "min_fresh_readings": 5, "step_s": 60,
                 "window_floor_s": 60, "stale_via_flag": True},
    "baseline": {"k": 4.0, "floor_fraction": 0.001},
    "review": {"consecutive_windows": 3},
    "within_run": {"baseline_s": 1800, "min_baseline_s": 900, "min_baseline_readings": 15,
                   "min_baseline_windows": 5},
    "onset": {"settling_s": {"pressure": 300, "vibration": 600, "temperature": 1800}},
    "cases": {"gap_s": 900},
})


def _sig(name, unit, every, values_fn, start, hours, rng, delay_s=0):
    t = start + pd.to_timedelta(np.arange(delay_s, hours * 3600, every), unit="s")
    secs = (t - start).total_seconds().to_numpy()
    return pd.DataFrame({"observed_at": t, "signal_name": name, "unit": unit,
                         "value": values_fn(secs, rng), "held_s": float(every),
                         "operating_state": "running", "quality_flags": [()] * len(t),
                         "provenance_hash": [f"{name}:{start}:{i}" for i in range(len(t))]})


WARM_S = 1500  # shorter than the 30 min temperature settling assumption


def warm(secs, rng):
    """Casing temperature: +10 K over the first 25 min, then flat."""
    return 35.0 + 10.0 * np.clip(secs / WARM_S, 0, 1) + rng.normal(0, 0.05, len(secs))


def flat(level):
    return lambda secs, rng: rng.normal(level, 0.3, len(secs))


def make_day(runs=((0.0, 4.0),), temp=warm, pressure_delay_s=0, seed=0,
             day=dt.date(2024, 6, 11)):
    rng = np.random.default_rng(seed)
    parts, running = [], []
    for h0, length in runs:
        start = T0 + pd.Timedelta(hours=h0)
        parts += [_sig("outlet_pressure", "bar", 60, flat(41.0), start, length, rng,
                       pressure_delay_s),
                  _sig("motor_casing_temperature", "degC", 8, temp, start, length, rng),
                  _sig("ambient_temperature", "degC", 8, lambda s, r: r.normal(20, 0.02, len(s)),
                       start, length, rng)]
        running.append((start, start + pd.Timedelta(hours=length)))
    r = pd.concat(parts, ignore_index=True)
    minutes = pd.date_range(running[0][0], running[-1][1], freq="60s", inclusive="left")
    s = pd.DataFrame([(m, g, 60) for m in minutes for g in r.signal_name.unique()],
                     columns=["bucket", "signal_name", "sample_count"])
    return scoring.DayData("cira-pump-B", day, r, s, running,
                           stale_limits={"outlet_pressure": 150.0,
                                         "motor_casing_temperature": 30.0})


TEMP = "motor_casing_temperature_rel_ambient"


# --- fixed settling times (A7) -----------------------------------------------------------

def test_score_report_regenerates_the_committed_report_exactly():
    """`pumpcopilot score report` rebuilds reports/cira_scoring_eval.md from the stored results
    (3a-3 section included) and the future-work file: equal to the report as committed before
    stage 2b plus the one future-work line added through that file."""

    from pumpcopilot import cli, scoring_report

    root = Path(__file__).resolve().parents[1]
    text = cli.scoring_report_text()
    assert text == (root / "reports" / "cira_scoring_eval.md").read_text()
    before = subprocess.run(["git", "-C", str(root), "show",
                             "9405c58:reports/cira_scoring_eval.md"],
                            capture_output=True, text=True, check=True).stdout
    added = [line for line in text.splitlines() if line not in before.splitlines()]
    assert added == ["- Clip bands at zero for non-negative signals (vibration, acceleration "
                     "peaks), so a band never suggests a negative reading."]
    assert "## Future work" in text
    assert scoring_report.load_future_work() == scoring_report.FUTURE_WORK


def test_signal_types_map_to_fixed_settling_times():
    assert scoring.signal_type("outlet_pressure") == "pressure"
    assert scoring.signal_type("pump_vibration_velocity") == "vibration"
    assert scoring.signal_type("motor_acceleration_peak") == "vibration"
    assert scoring.signal_type(TEMP) == "temperature"
    assert scoring.signal_type("motor_accelerometer_contact_temperature_rel_ambient") \
        == "temperature"
    assert scoring.signal_type("flow_rate") is None
    a = T0
    assert scoring.settled_at("outlet_pressure", a, S3) == a + pd.Timedelta(minutes=5)
    assert scoring.settled_at("pump_vibration_velocity", a, S3) == a + pd.Timedelta(minutes=10)
    assert scoring.settled_at(TEMP, a, S3) == a + pd.Timedelta(minutes=30)
    assert scoring.settled_at("flow_rate", a, S3) is None


def test_settling_times_are_config_not_derived_from_data():
    # the same settling time whatever the data: a flat or a warming day gives the same onset
    for temp in (warm, flat(35.0)):
        prep = scoring.prepare(make_day(temp=temp), S3)
        _, bases = scoring.score_within_run_steady(prep, S3)
        assert bases[0].bands[TEMP]["onset"] == prep.running[0][0] + pd.Timedelta(minutes=30)


# --- baseline start ----------------------------------------------------------------------

def test_baseline_starts_at_the_later_of_first_fresh_reading_and_settling_time():
    # pressure settles after 5 min but its sensor reports only from minute 40 (B June's silence)
    prep = scoring.prepare(make_day(pressure_delay_s=2400), S3)
    frame, bases = scoring.score_within_run_steady(prep, S3)
    a = prep.running[0][0]
    bp = bases[0].bands["outlet_pressure"]
    bt = bases[0].bands[TEMP]
    assert bp["onset"] == a + pd.Timedelta(minutes=5)
    assert bp["baseline_start"] == a + pd.Timedelta(seconds=2400)  # first fresh reading
    assert bt["first_fresh"] < bt["onset"]
    assert bt["baseline_start"] == bt["onset"] == a + pd.Timedelta(minutes=30)  # settling
    for sig, band in (("outlet_pressure", bp), (TEMP, bt)):
        assert band["baseline_end"] == band["baseline_start"] + pd.Timedelta(seconds=1800)
        g = frame[frame.signal_name == sig]
        assert len(g) and (g.start >= band["baseline_end"]).all()  # nothing scored earlier


def test_signal_without_a_settling_time_abstains():
    cfg = scoring.merge_config(S3, {"onset": {"settling_s": {"temperature": None}}})
    _, bases = scoring.score_within_run_steady(scoring.prepare(make_day(), S3), cfg)
    assert "no settling time" in bases[0].signal_abstentions[TEMP]


def test_steady_baseline_does_not_review_the_warm_up():
    day = make_day(seed=2)
    cfg = scoring.merge_config(S3, {"within_run": {"baseline_s": 900}})
    steady, _ = scoring.score_within_run_steady(scoring.prepare(day, cfg), cfg)
    early, _ = scoring.score_within_run(scoring.prepare(day, cfg), cfg)
    t_steady = steady[steady.signal_name == TEMP]
    t_early = early[early.signal_name == TEMP]
    assert (t_early.state == P.REVIEW_SUGGESTED).mean() > 0.3  # 3a-2: baseline in warm-up
    assert (t_steady.state == P.REVIEW_SUGGESTED).mean() < 0.05


# --- cases -------------------------------------------------------------------------------

def _ep(sig, start, end):
    return {"signal_name": sig, "start": str(T0 + pd.Timedelta(minutes=start)),
            "end": str(T0 + pd.Timedelta(minutes=end)), "windows": 1, "max_score": 1.5}


def test_cases_merge_episodes_on_the_same_run_within_the_gap():
    running = [(T0, T0 + pd.Timedelta(hours=3)), (T0 + pd.Timedelta(hours=4),
                                                  T0 + pd.Timedelta(hours=6))]
    eps = [_ep("a", 10, 20), _ep("b", 30, 40),   # 10 min apart: one case
           _ep("a", 60, 70),                     # 20 min after: new case
           _ep("c", 175, 179), _ep("a", 245, 250)]  # other run: never merged across
    cases = scoring.cases_from_episodes(eps, running, gap_s=900)
    assert [(c["run"], c["episodes"], sorted(c["signals"])) for c in cases] == [
        (0, 2, ["a", "b"]), (0, 1, ["a"]), (0, 1, ["c"]), (1, 1, ["a"])]
    summary = scoring.case_summary(cases, running)
    assert summary["cases"] == 4 and summary["cases_per_run"] == [3, 1]
    assert summary["cases_per_running_hour"] == pytest.approx(4 / 5)
    # covered: 10-40, 60-70, 175-179, 245-250 min = 30 + 10 + 4 + 5 = 49 min of 300
    assert summary["case_hours"] == pytest.approx(49 / 60)
    assert summary["case_time_fraction"] == pytest.approx(49 / 300)


def test_cases_can_be_recomputed_from_stored_results():
    stored = {"pumps": {"B": {"review_episodes": [_ep("a", 10, 20), _ep("a", 25, 30)]},
                        "A": {"abstained": "baseline abstained: x"}}}
    running = {"B": [(T0, T0 + pd.Timedelta(hours=2))], "A": [(T0, T0 + pd.Timedelta(hours=1))]}
    out = scoring.cases_for_results(stored, running, gap_s=900)
    assert out["B"]["cases"] == 1 and out["B"]["cases_per_running_hour"] == pytest.approx(0.5)
    assert out["A"]["abstained"]


# --- protocol ----------------------------------------------------------------------------

def _row(k, n, frac, cases, det, baseline_s=1800, wr=6):
    return {"k": k, "consecutive_windows": n, "baseline_s": baseline_s, "window_readings": wr,
            "case_time_fraction": frac, "cases": cases, "qualify_detection_rate": det,
            "qualify_injections": 15, "evaluable_windows": 100, "injections": 90}


def test_selection_requires_80_percent_6_sigma_step_detection():
    table = [_row(8.0, 5, 0.01, 1, 0.60),   # least coverage, but misses too many 6-sigma steps
             _row(5.0, 3, 0.20, 3, 0.80),   # qualifies (exactly 80%)
             _row(4.0, 3, 0.30, 2, 1.00)]   # qualifies, more coverage
    best, info = scoring.select_3a3(table, min_detection=0.8)
    assert (best["k"], best["consecutive_windows"]) == (5.0, 3)
    assert info == {"requirement_met": True, "qualified": 2, "candidates": 3}


def test_selection_ignores_rows_without_qualifying_injections():
    no_inj = {**_row(8.0, 5, 0.0, 0, None), "qualify_injections": 0}
    best, info = scoring.select_3a3([no_inj, _row(3.0, 2, 0.5, 4, 0.9)], min_detection=0.8)
    assert best["k"] == 3.0 and info["qualified"] == 1


def test_selection_tie_breaks_fewer_cases_then_larger_k_then_larger_n():
    rows = [_row(3.0, 5, 0.1, 2, 0.9), _row(4.0, 2, 0.1, 1, 0.9), _row(4.0, 3, 0.1, 1, 0.9),
            _row(6.0, 2, 0.1, 1, 0.9), _row(6.0, 3, 0.1, 1, 0.9), _row(8.0, 5, 0.1, 3, 0.9)]
    best, _ = scoring.select_3a3(rows, min_detection=0.8)
    # fraction ties; fewest cases (1) leaves k 4 and 6; larger k (6); then larger N (3)
    assert (best["k"], best["consecutive_windows"]) == (6.0, 3)
    best, _ = scoring.select_3a3(rows[:3], min_detection=0.8)
    assert (best["k"], best["consecutive_windows"]) == (4.0, 3)


def test_selection_falls_back_to_highest_detection_when_nothing_qualifies():
    rows = [_row(8.0, 5, 0.01, 1, 0.5), _row(3.0, 2, 0.9, 9, 0.7), _row(4.0, 2, 0.8, 8, 0.7)]
    best, info = scoring.select_3a3(rows, min_detection=0.8)
    assert best["qualify_detection_rate"] == 0.7
    assert (best["k"], best["consecutive_windows"]) == (4.0, 2)  # then the same tie-breaks
    assert info == {"requirement_met": False, "qualified": 0, "candidates": 3}


def test_tuning_3a3_loads_only_b_june_and_records_qualification():
    calls = []

    def loader(asset_id, source_day):
        calls.append((asset_id, source_day))
        return make_day(runs=((0.0, 6.0),), seed=5, day=source_day)

    grid = {"k": [4.0], "consecutive_windows": [2, 3], "window_readings": [6],
            "baseline_s": [1800]}
    cfg = scoring.merge_config(S3, {"tuning": {"qualify": {"starts": 2}}})
    frozen, table = scoring.tune_3a3(loader, cfg, grid, starts_per_fault=1)
    assert calls == [("cira-pump-B", dt.date(2024, 6, 11))]
    assert len(table) == 2
    for r in table:
        assert {"cases_per_running_hour", "case_time_fraction", "qualify_detection_rate",
                "qualify_injections"} <= set(r)
        assert r["qualify_injections"] > 0
    split = scoring.split_running(make_day(runs=((0.0, 6.0),)), 0.6)
    assert all(pd.Timestamp(t) >= split for t in frozen["frozen"]["qualify_starts"])
    fz = frozen["frozen"]
    assert fz["label"] == scoring.PREREG_LABEL
    assert "requirement_met" in fz and fz["qualification"]["min_detection"] == 0.8
    assert frozen["cases"]["gap_s"] == 900
    assert frozen["onset"]["settling_s"] == {"pressure": 300, "vibration": 600,
                                             "temperature": 1800}


def test_preregistration_check_detects_changes(tmp_path):
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "cfg.yaml").write_text("a: 1\n")
    git("add", ".")
    git("commit", "-qm", "prereg")
    head = git("rev-parse", "HEAD")
    assert scoring.verify_preregistration(head, ["cfg.yaml"], repo=tmp_path)["unchanged"]
    (tmp_path / "cfg.yaml").write_text("a: 2\n")
    res = scoring.verify_preregistration(head, ["cfg.yaml"], repo=tmp_path)
    assert not res["unchanged"] and res["changed"] == ["cfg.yaml"]


def test_3a3_report_has_protocol_prereg_cases_and_final_section():
    from pumpcopilot import scoring_report

    def loader(asset_id, source_day):
        return make_day(runs=((0.0, 5.0),), seed=source_day.month, day=source_day)

    cfg = scoring.merge_config(S3, {"injection": {"signals": ["outlet_pressure"]}})
    cfg["frozen"] = {"label": scoring.PREREG_LABEL, "grid": {}, "objective": "x",
                     "selected": {}, "tuned_on": {}}
    protocol_only = scoring_report.markdown_3a3(cfg, prereg=None, results=None, cases=None)
    assert "Protocol" in protocol_only and "not yet scored" in protocol_only
    assert "89 of 90" in protocol_only and "before October was scored" in protocol_only
    assert "A7" in protocol_only
    res = scoring.evaluate_3a3(loader, cfg, starts_per_fault=1)
    assert set(res["pumps"]) == {"B", "A"} and "cases_per_running_hour" in res["pumps"]["B"]
    cases = {"3a-3 within-run steady": {"B": res["pumps"]["B"]["case_summary"]}}
    md = scoring_report.markdown_3a3(cfg, prereg={"commit": "abc1234", "unchanged": True,
                                                   "changed": []}, results=res, cases=cases)
    assert "abc1234" in md and "last detector revision" in md.lower()
    assert "Future work" in md and "cases per running hour" in md
    corr = md.split("## Post-pre-registration corrections")[1].split("\n## ")[0]
    assert "score_within_run_steady" in corr and "reading held" in corr
    assert "unchanged" in corr and "regenerates identically" in corr
    assert md.index("Post-pre-registration corrections") < md.index("last detector revision")
    # the section only appears once October is scored
    assert "Post-pre-registration corrections" not in protocol_only
