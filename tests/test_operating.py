from pathlib import Path

import numpy as np
import pytest
import yaml

from pumpcopilot import cira, operating
from pumpcopilot.schema import OperatingState, QualityFlag

RULES_FILE = Path(__file__).resolve().parents[1] / "data" / "operating_rules.yaml"
RULE = {"lower_bar": 10.0, "upper_bar": 25.0, "min_state_s": 90.0, "transition_s": 0.0}
S = OperatingState


def _held(readings, hold=60):
    """1 Hz samples of a sensor that reports a new reading every `hold` seconds."""
    v = np.repeat(np.asarray(readings, dtype=float), hold)
    return np.arange(len(v), dtype=float), v


def _classify(readings, bad=None, **rule):
    t, p = _held(readings)
    bad = np.zeros(len(p), bool) if bad is None else bad
    return operating.classify_states(t, p, bad, {**RULE, **rule})


# --- threshold derivation ----------------------------------------------------------------

def test_thresholds_sit_inside_the_gap_between_idle_and_running():
    rng = np.random.default_rng(0)
    p = np.r_[rng.normal(0.5, 0.02, 5000), rng.normal(40, 1.0, 3000)]
    r = operating.derive_pump_rule(p, update_period_s=60.0)
    assert r["idle_ceiling_bar"] < r["lower_bar"] < r["upper_bar"] < r["running_floor_bar"]
    assert r["idle_median_bar"] == pytest.approx(0.5, abs=0.01)
    assert r["running_median_bar"] == pytest.approx(40, abs=0.1)
    assert r["min_state_s"] == 90.0 and r["transition_s"] == 120.0


def test_derivation_refuses_a_pump_that_never_runs():
    p = np.random.default_rng(0).normal(0.5, 0.02, 5000)
    with pytest.raises(ValueError, match="idle/running"):
        operating.derive_pump_rule(p, update_period_s=60.0)


def test_update_period_is_the_median_hold_time():
    t, v = _held([1.0, 2.0, 3.0, 4.0], hold=60)
    assert operating.median_hold_s(t, v) == 60.0


# --- operating state ---------------------------------------------------------------------

def test_hysteresis_switches_only_outside_the_band():
    # 0.5, then 15 (inside the band: stays off), 30 (above upper: on),
    # 15 (inside the band: stays on), 5 (below lower: off)
    r = _classify([0.5, 0.5, 15, 15, 30, 30, 15, 15, 5, 5])
    at = dict(enumerate(r.states))
    assert at[150] == S.OFF
    assert at[300] == S.RUNNING
    assert at[420] == S.RUNNING
    assert at[500] == S.OFF
    assert [c["to"] for c in r.changes] == [S.RUNNING, S.OFF]


def test_brief_flicker_is_not_a_state_change():
    # one 40 bar reading (60 s) between idle readings, then a real start
    r = _classify([0.5, 0.5, 40, 0.5, 0.5, 40, 40, 40])
    assert r.flickers == 1
    assert [(c["at_s"], c["to"]) for c in r.changes] == [(300.0, S.RUNNING)]
    assert r.states[150] == S.TRANSITION  # the flicker itself
    assert r.states[250] == S.OFF
    assert r.states[400] == S.RUNNING


def test_first_seconds_after_a_change_are_transition():
    r = _classify([0.5] * 3 + [40] * 5 + [0.5] * 4, transition_s=120)
    assert r.states[179] == S.OFF
    assert r.states[180] == S.TRANSITION and r.states[299] == S.TRANSITION
    assert r.states[300] == S.RUNNING
    assert r.states[480] == S.TRANSITION and r.states[600] == S.OFF


def test_bad_quality_samples_are_unknown_and_do_not_break_the_state():
    t, p = _held([40] * 5)
    bad = np.zeros(len(p), bool)
    bad[100:110] = True
    p[200] = np.nan
    r = operating.classify_states(t, p, bad, RULE)
    assert (r.states[100:110] == S.UNKNOWN).all()
    assert r.states[200] == S.UNKNOWN
    assert r.states[99] == S.RUNNING and r.states[110] == S.RUNNING
    assert r.changes == []


def test_state_is_unknown_until_it_can_be_determined():
    r = _classify([15, 15, 15, 40, 40, 40])  # starts inside the band
    assert (r.states[:180] == S.UNKNOWN).all()
    assert (r.states[180:] == S.RUNNING).all()
    assert r.changes == []  # first determination is not a change


# --- motor-vibration confirmation ---------------------------------------------------------

VIB = {"lower": 1.5, "upper": 4.0, "min_state_s": 12.0}


def _pumped(v):
    """Pressure off for 120 s, then running for 360 s (1 Hz, 60 s readings)."""
    t, p = _held([0.5] * 2 + [40] * 6)
    return t, p, np.zeros(len(p), bool), np.asarray(v, dtype=float)


def _segs(r):
    return [(g["state"], g["start_s"], g["end_s"], g.get("motor_unconfirmed"),
             g.get("pressurized_while_stopped")) for g in r.segments]


def test_pressure_decides_and_a_fresh_motor_reading_confirms():
    # the motor accelerometer reports running from 125 s (new reading every sample)
    t, p, bad, v = _pumped(np.r_[np.full(125, 0.47), 20 + 0.01 * np.arange(355)])
    r = operating.classify_states(t, p, bad, RULE, v=v, vib_rule=VIB)
    # before the first motor reading the pump is still running, just motor-unconfirmed
    assert r.states[122] == S.RUNNING and r.states[130] == S.RUNNING
    assert not r.denied.any()
    assert [(c["at_s"], c["to"]) for c in r.changes] == [(120.0, S.RUNNING)]
    assert _segs(r) == [(S.OFF, 0.0, 119.0, None, False),
                        (S.RUNNING, 120.0, 124.0, True, None),
                        (S.RUNNING, 125.0, 479.0, False, None)]


def test_fresh_idle_motor_reading_vetoes_running():
    # motor stops at 300 s while the outlet pressure holds: pressurized while stopped
    v = np.r_[np.full(125, 0.47), 20 + 0.01 * np.arange(175), 0.3 + 0.001 * np.arange(180)]
    t, p, bad, v = _pumped(v)
    r = operating.classify_states(t, p, bad, RULE, v=v, vib_rule=VIB)
    assert r.states[400] == S.OFF and r.denied[400]
    assert r.denied.sum() == 180
    assert [(c["at_s"], c["to"]) for c in r.changes] == [(120.0, S.RUNNING), (300.0, S.OFF)]
    assert _segs(r)[-1] == (S.OFF, 300.0, 479.0, None, True)


def test_frozen_motor_sensor_leaves_running_with_segment_attribute():
    # the accelerometer never reports after the start: no veto, so pressure's state stands
    t, p, bad, v = _pumped(np.full(480, 0.47))
    r = operating.classify_states(t, p, bad, RULE, v=v, vib_rule=VIB)
    assert (r.states[120:] == S.RUNNING).all()
    assert not r.denied.any()
    assert [c["to"] for c in r.changes] == [S.RUNNING]
    assert _segs(r) == [(S.OFF, 0.0, 119.0, None, False),
                        (S.RUNNING, 120.0, 479.0, True, None)]


def test_unknown_samples_do_not_split_segments():
    t, p, bad, v = _pumped(np.full(480, 0.47))
    bad[200:205] = True
    r = operating.classify_states(t, p, bad, RULE, v=v, vib_rule=VIB)
    assert (r.states[200:205] == S.UNKNOWN).all()
    assert _segs(r)[-1] == (S.RUNNING, 120.0, 479.0, True, None)


def test_motor_unconfirmed_is_not_a_quality_flag(cira_dir, make_cira_frame, rules):
    df = make_cira_frame("A", "2024-04-10 08:00:00", 40)
    df["A_ACR_Mot.SV"] = 0.47  # frozen motor sensor
    path = cira_dir / "A_2024-04-10.csv"
    df.to_csv(path, index=False)
    spec = yaml.safe_load((RULES_FILE.parent / "cira_columns.yaml").read_text())
    ev = list(cira.to_events(path, spec, rules=rules))
    assert S.RUNNING in {e.operating_state for e in ev}
    assert "motor_unconfirmed" not in {f.value for f in QualityFlag}
    assert all(QualityFlag.SPIKE_SUSPECTED not in e.quality_flags for e in ev)


# --- quality flags -----------------------------------------------------------------------

def test_quality_flags_exist_in_schema():
    assert QualityFlag("stale_suspected") is QualityFlag.STALE_SUSPECTED
    assert QualityFlag("spike_suspected") is QualityFlag.SPIKE_SUSPECTED


def test_stale_flags_values_held_longer_than_the_limit():
    t = np.arange(200, dtype=float)
    v = np.r_[np.full(70, 1.0), np.full(50, 2.0), np.arange(80, dtype=float)]
    v[10] = np.nan  # a dropout inside the held run does not reset it
    m = operating.stale_mask(t, v, min_s=60)
    assert m[:70].sum() == 69 and not m[10]
    assert not m[70:].any()
    assert not operating.stale_mask(t, v, min_s=70).any()  # held exactly the limit: not stale


def test_reading_events_are_value_changes():
    t, v = _held([1.0, 2.0, 2.0, 3.0], hold=10)  # the repeated 2.0 is one reading held 20 s
    idx, hold = operating.reading_events(t, v)
    assert idx.tolist() == [0, 10, 30]
    assert hold[:2].tolist() == [10.0, 20.0] and np.isnan(hold[2])


def test_stale_limit_is_p99_of_running_reading_intervals():
    holds = np.r_[np.full(990, 10.0), np.full(10, 100.0)]
    assert operating.stale_limit_from_holds(holds) == pytest.approx(
        np.quantile(holds, 0.99), abs=0.1)
    # too few running readings to estimate a tail: fall back to the default
    assert operating.stale_limit_from_holds(np.full(10, 10.0), min_readings=50,
                                            default_s=60.0) == 60.0


def test_spikes_are_flagged_only_in_running_state():
    rng = np.random.default_rng(1)
    readings = rng.normal(40, 0.5, 60)
    readings[40] = 60.0
    t, v = _held(readings, hold=10)
    running = np.ones(len(v), bool)
    episode = np.zeros(len(v), int)
    m = operating.spike_mask(v, running, episode, z_threshold=6.0)
    assert np.flatnonzero(m).tolist() == list(range(400, 410))
    running[400:410] = False
    assert not operating.spike_mask(v, running, episode, z_threshold=6.0).any()


def test_sustained_shift_is_never_a_spike():
    rng = np.random.default_rng(2)
    readings = np.r_[rng.normal(40, 0.5, 40), rng.normal(55, 0.5, 20)]
    t, v = _held(readings, hold=10)
    ones, zeros = np.ones(len(v), bool), np.zeros(len(v), int)
    assert not operating.spike_mask(v, ones, zeros, z_threshold=6.0).any()


def test_spike_must_revert_within_m_readings():
    rng = np.random.default_rng(1)
    readings = rng.normal(40, 0.5, 60)
    readings[40], readings[41] = 60.0, 61.0  # two outlying readings (distinct values),
    # back on the third; equal consecutive values would be one held reading
    t, v = _held(readings, hold=10)
    ones, zeros = np.ones(len(v), bool), np.zeros(len(v), int)
    m2 = operating.spike_mask(v, ones, zeros, z_threshold=6.0, revert_within=2)
    assert np.flatnonzero(m2).tolist() == list(range(400, 420))
    assert not operating.spike_mask(v, ones, zeros, z_threshold=6.0, revert_within=1).any()


# --- file-level annotation, events and report --------------------------------------------

@pytest.fixture
def rules(cira_dir):
    return operating.derive_rules(cira_dir)[0]


def test_annotation_never_changes_values(cira_dir, rules):
    path = cira_dir / "A_2024-04-10.csv"
    df, _, _ = cira.load(path)
    ann = operating.annotate(path, rules)
    assert ann.df.equals(df)
    assert set(ann.states) <= set(S)
    assert S.RUNNING in set(ann.states)


def test_events_carry_state_and_new_flags(cira_dir, rules):
    path = cira_dir / "A_2024-04-10.csv"
    spec = yaml.safe_load((RULES_FILE.parent / "cira_columns.yaml").read_text())
    plain = list(cira.to_events(path, spec))
    ev = list(cira.to_events(path, spec, rules=rules))
    assert [e.value for e in ev] == [e.value for e in plain]
    assert {e.operating_state for e in ev} >= {S.OFF, S.RUNNING}
    assert all(e.operating_state == S.UNKNOWN for e in plain)


def test_state_report_per_pump_day(cira_dir, rules):
    rep = operating.state_report(cira_dir, rules)
    day = rep["pump_days"]["A_2024-04-10"]
    secs = day["seconds"]
    assert set(secs) == {"running", "off", "transition", "unknown"}
    assert sum(secs.values()) == pytest.approx(390.0)  # 40 samples at 10 s
    assert day["state_changes"] == 1
    assert day["pressurized_while_stopped_s"] == 0.0
    assert day["running_confirmed_s"] + day["running_unconfirmed_s"] == pytest.approx(
        secs["running"])
    assert day["first_confirmed_running"] is not None
    assert all("motor_unconfirmed" in g for g in day["segments"] if g["state"] == "running")
    assert set(day["stale"]) == set(day["spike"]) == set(day["stale_running"])
    assert len(day["stale"]) == 10
    assert all(day["stale_running"][k] <= day["stale"][k] for k in day["stale"])
    # B has a 10 minute gap: it counts as unknown time, not as the state before it
    assert rep["pump_days"]["B_2024-04-10"]["seconds"]["unknown"] >= 590.0


def test_rederiving_from_the_written_file_is_idempotent(cira_dir, tmp_path):
    rules, _ = operating.derive_rules(cira_dir)
    path = tmp_path / "operating_rules.yaml"
    operating.write_rules(rules, path)
    back = operating.load_rules(path)
    again, _ = operating.derive_rules(cira_dir, settings=back["settings"], spike=back["spike"])
    assert again["pumps"] == back["pumps"] == rules["pumps"]
    assert again["settings"] == back["settings"]


def test_rules_report_documents_vibration_stale_and_decision_delay(cira_dir, rules):
    _, derivation = operating.derive_rules(cira_dir)
    md = operating.derivation_markdown(rules, derivation,
                                       operating.state_report(cira_dir, rules))
    for heading in ("Motor vibration", "Sample-and-hold", "Stale before and after",
                    "Spike decision delay"):
        assert heading in md
    assert rules["vibration"]["signal"] == "ACR_Mot.SV"
    assert set(rules["pumps"]["A"]["vibration"]) >= {"lower", "upper", "min_state_s"}


def test_committed_rules_file_is_consistent():
    r = yaml.safe_load(RULES_FILE.read_text())
    assert r["state_signal"] == "Pres.PV"
    assert set(r["pumps"]) == {"A", "B", "C"}
    for p in r["pumps"].values():
        assert p["idle_ceiling_bar"] < p["lower_bar"] < p["upper_bar"] < p["running_floor_bar"]
    assert r["stale"]["default_s"] == 60
    assert r["spike"]["revert_within"] == 2
    for p in r["pumps"].values():
        vib = p["vibration"]
        assert vib["idle_ceiling"] < vib["lower"] < vib["upper"] < vib["running_floor"]
