"""CIRA operating state (running / off / transition / unknown) and data-quality flags.

Rules are derived from the data by `derive_rules`, stored in data/operating_rules.yaml, and the
derivation is written to reports/cira_operating_rules.md. Nothing here changes or drops a
value: every output is a label or a flag next to the original sample.

Why the rules look the way they do (measured on the raw files, 2026-09-24):
* Outlet pressure is bimodal per pump: idle about 0.5 bar, running about 40 bar, with an
  almost empty gap between. Thresholds sit inside that gap, with hysteresis.
* Pressure alone can stay up after a stop, so a fresh motor accelerometer peak reading
  (ACR_Mot.SV) at that pump's idle level vetoes running. The idle level is derived the same
  way on a log10 scale (idle about 0.5 m/s^2, running 10-200 m/s^2).
* Readings are sample-and-hold (A5 in docs/ASSUMPTIONS.md): each sensor sends a new reading
  every few seconds to a minute, and the gateway repeats the last one at 1 Hz. So we work on
  reading events (value changes): stale limits come from the time between readings while
  running, a motor reading can only veto if it was taken after the current pressure state
  began, and spikes are scored per reading.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import cira
from .schema import OperatingState

S = OperatingState
STATES = (S.RUNNING, S.OFF, S.TRANSITION, S.UNKNOWN)
_LABEL = {0: S.OFF, 1: S.RUNNING}

DEFAULT_SETTINGS = {
    "hysteresis_band": [1 / 3, 2 / 3],  # lower/upper as fractions of the idle-to-running gap
    "gap_quantiles": [0.999, 0.001],  # idle ceiling / running floor, robust to stray readings
    "min_class_share": 0.01,  # each of idle and running must hold at least this share
    "min_gap_ratio": 0.5,  # gap must span at least this share of idle-to-running medians
    "min_state_updates": 1.5,  # a new state must last into a second pressure reading
    "transition_updates": 2.0,  # the first two readings after a change are transition
    "max_interval_s": 10.0,  # sample intervals beyond this count as unknown time (no data)
    "unknown_if": ["missing", "placeholder_suspected", "duplicate_timestamp", "non_monotonic"],
}
VIBRATION_DEFAULTS = {
    "signal": "ACR_Mot.SV",
    "log10": True,  # peak values span three orders of magnitude
    "gap_quantiles": [0.99, 0.01],  # peaks are heavy-tailed; 99.9/0.1 fails for pump A
    "min_state_updates": 1.5,
}
STALE_DEFAULTS = {"default_s": 60.0, "quantile": 0.99, "min_readings": 50}
SPIKE_DEFAULTS = {"z_threshold": 8.0, "window_updates": 30, "min_history": 10,
                  "scale_floor_fraction": 0.01, "revert_within": 2, "return_z": 3.5}

# Descriptor Table 1 operating ranges (startup, shutdown), same clock as the files (A1 in
# docs/ASSUMPTIONS.md). Used only to compare our derived state changes against.
DESCRIPTOR_TABLE1 = {
    "A_2024-04-10": ("12:28:30", "12:49:28"), "B_2024-04-10": ("12:51:42", "12:56:41"),
    "C_2024-04-10": ("12:58:16", "13:10:14"), "A_2024-06-11": ("10:00:25", "11:21:24"),
    "B_2024-06-11": ("07:08:08", "13:07:33"), "C_2024-06-11": ("10:52:55", "11:19:54"),
    "A_2024-10-30": ("10:59:15", "13:19:13"), "B_2024-10-30": ("08:28:33", "11:05:56"),
}


def _known(defaults: dict, given: dict | None) -> dict:
    """Defaults overridden by the given values; unknown keys (old versions) are dropped."""
    given = given or {}
    return {k: given.get(k, d) for k, d in defaults.items()}


def _num(s: pd.Series) -> np.ndarray:
    return pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)


def _seconds(ts: pd.Series) -> np.ndarray:
    return (ts - ts.min()).dt.total_seconds().to_numpy(dtype=float)


def _short(col: str, pump: str) -> str:
    return col[len(pump) + 1:] if col.startswith(f"{pump}_") else col


# --- sample-and-hold readings ------------------------------------------------------------

def reading_events(t: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sample indices where a new reading appears (value change, missing samples skipped),
    and the seconds each reading was held until the next one (NaN for the last)."""
    ok = np.flatnonzero(np.isfinite(t) & np.isfinite(v))
    if len(ok) == 0:
        return ok, np.array([])
    vv = v[ok]
    idx = ok[np.r_[True, vv[1:] != vv[:-1]]]
    return idx, np.r_[np.diff(t[idx]), np.nan]


def median_hold_s(t: np.ndarray, v: np.ndarray) -> float:
    h = reading_events(t, v)[1]
    h = h[np.isfinite(h)]
    return float(np.median(h)) if len(h) else math.nan


def stale_limit_from_holds(holds: np.ndarray, quantile: float = 0.99, min_readings: int = 50,
                           default_s: float = 60.0) -> float:
    """Stale limit = the given percentile of time between readings (rounded up to 0.1 s)."""
    h = holds[np.isfinite(holds) & (holds > 0)]
    if len(h) < min_readings:
        return float(default_s)
    return math.ceil(float(np.quantile(h, quantile)) * 10) / 10


# --- threshold derivation ----------------------------------------------------------------

def _otsu_split(x: np.ndarray, bins: int = 256) -> float:
    """Threshold that maximises between-class variance of the histogram (Otsu's method)."""
    hist, edges = np.histogram(x, bins=bins)
    hist = hist.astype(float)
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mids)
    mu0 = np.divide(m0, w0, out=np.zeros_like(m0), where=w0 > 0)
    mu1 = np.divide(m0[-1] - m0, w1, out=np.zeros_like(m0), where=w1 > 0)
    between = w0 * w1 * (mu0 - mu1) ** 2
    return float(edges[int(np.argmax(between[:-1])) + 1])


def derive_pump_rule(pressure: np.ndarray, update_period_s: float,
                     settings: dict | None = None) -> dict:
    """Hysteresis thresholds inside the gap between the idle and running classes."""
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    p = pressure[np.isfinite(pressure)]
    if len(p) == 0:
        raise ValueError("no idle/running separation: no readings")
    split = _otsu_split(p)
    idle, run = p[p < split], p[p >= split]
    if min(len(idle), len(run)) < s["min_class_share"] * len(p):
        raise ValueError("no idle/running separation: one class is nearly empty")
    q_idle, q_run = s["gap_quantiles"]
    ceiling, floor = float(np.quantile(idle, q_idle)), float(np.quantile(run, q_run))
    idle_med, run_med = float(np.median(idle)), float(np.median(run))
    gap = floor - ceiling
    if gap < s["min_gap_ratio"] * (run_med - idle_med):
        raise ValueError(f"no idle/running separation: gap {gap:.3f} between "
                         f"{ceiling:.3f} and {floor:.3f} is too narrow")
    lo, hi = s["hysteresis_band"]
    return {
        "lower_bar": round(ceiling + lo * gap, 3),
        "upper_bar": round(ceiling + hi * gap, 3),
        "min_state_s": round(s["min_state_updates"] * update_period_s, 1),
        "transition_s": round(s["transition_updates"] * update_period_s, 1),
        "pressure_update_s": round(update_period_s, 1),
        "otsu_split_bar": round(split, 3),
        "idle_median_bar": round(idle_med, 3),
        "idle_ceiling_bar": round(ceiling, 3),
        "running_floor_bar": round(floor, 3),
        "running_median_bar": round(run_med, 3),
        "running_share": round(len(run) / len(p), 4),
        "samples": int(len(p)),
    }


def derive_vibration_rule(v: np.ndarray, update_period_s: float, settings: dict,
                          vibration: dict) -> dict:
    """Same derivation as pressure (Otsu, gap, hysteresis band), optionally on log10."""
    log = vibration["log10"]
    x = np.log10(v[np.isfinite(v) & (v > 0)]) if log else v
    r = derive_pump_rule(x, update_period_s, {
        **settings, "gap_quantiles": vibration["gap_quantiles"],
        "min_state_updates": vibration["min_state_updates"]})

    def back(y):
        return round(10 ** y, 4) if log else y

    return {
        "lower": back(r["lower_bar"]), "upper": back(r["upper_bar"]),
        "min_state_s": r["min_state_s"], "update_s": r["pressure_update_s"],
        "idle_median": back(r["idle_median_bar"]), "idle_ceiling": back(r["idle_ceiling_bar"]),
        "running_floor": back(r["running_floor_bar"]),
        "running_median": back(r["running_median_bar"]), "otsu_split": back(r["otsu_split_bar"]),
        "running_share": r["running_share"], "samples": r["samples"],
    }


# --- state -------------------------------------------------------------------------------

@dataclass
class StateResult:
    states: np.ndarray  # OperatingState per sample
    episode: np.ndarray  # increments at each accepted state change; -1 where unknown
    changes: list[dict]  # {"at_s", "from", "to"}
    flickers: int  # candidate pressure changes shorter than min_state_s (labelled transition)
    denied: np.ndarray  # pressure says running, fresh motor reading says idle: off
    unconfirmed: np.ndarray  # pressure says running, no motor reading since that began
    segments: list[dict]  # maximal stretches of one state, with segment attributes


def _debounce(t: np.ndarray, x: np.ndarray, lower: float, upper: float,
              min_state_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Hysteresis then a minimum duration on a sequence of usable points.

    Returns the accepted state per point (-1 undetermined, 0 off, 1 running) and a flicker
    mask: points of a candidate change that did not last min_state_s keep the previous state.
    """
    n = len(x)
    acc, flick = np.full(n, -1), np.zeros(n, dtype=bool)
    if n == 0:
        return acc, flick
    hyst = np.empty(n, dtype=int)
    s = -1
    for k in range(n):
        if x[k] > upper:
            s = 1
        elif x[k] < lower:
            s = 0
        hyst[k] = s
    starts = np.flatnonzero(np.r_[True, hyst[1:] != hyst[:-1]])
    ends = np.r_[starts[1:], n]
    current = None
    for a, e in zip(starts, ends, strict=True):
        if hyst[a] == -1:
            continue
        end_t = t[e] if e < n else t[e - 1]
        if current is None:
            current = hyst[a]
        elif hyst[a] != current:
            if end_t - t[a] < min_state_s:
                acc[a:e], flick[a:e] = current, True
                continue
            current = hyst[a]
        acc[a:e] = current
    return acc, flick


def vibration_evidence(t: np.ndarray, v: np.ndarray, rule: dict) -> tuple[np.ndarray, np.ndarray]:
    """Per sample: debounced motor state from the latest reading (-1, 0, 1) and its time."""
    n = len(v)
    state, at = np.full(n, -1), np.full(n, np.nan)
    idx, _ = reading_events(t, v)
    if len(idx) == 0:
        return state, at
    acc, _ = _debounce(t[idx], v[idx], rule["lower"], rule["upper"], rule["min_state_s"])
    pos = np.searchsorted(idx, np.arange(n), side="right") - 1
    has = pos >= 0
    state[has], at[has] = acc[pos[has]], t[idx][pos[has]]
    return state, at


def classify_states(t: np.ndarray, p: np.ndarray, bad: np.ndarray, rule: dict,
                    v: np.ndarray | None = None, vib_rule: dict | None = None) -> StateResult:
    """Pressure decides the state; a fresh motor reading can only veto running.

    Order: bad-quality samples are unknown first and never update the state. Pressure inside
    the band before any state is known is unknown. A pressure change that does not last
    min_state_s is a flicker (transition, no change). Where pressure says running, a motor
    reading taken after that pressure state began and showing idle vetoes it: off, "pressurized
    while stopped". Without such a reading the sample stays running; the running segment gets
    the attribute motor_unconfirmed (not a quality flag, not unknown). The first transition_s
    after each change of the resulting state are transition.
    """
    n = len(p)
    code, flick = np.full(n, -1), np.zeros(n, dtype=bool)
    denied, unconfirmed = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    good = np.flatnonzero(~bad & np.isfinite(p) & np.isfinite(t))
    acc, fl = _debounce(t[good], p[good], rule["lower_bar"], rule["upper_bar"],
                        rule["min_state_s"])
    code[good], flick[good] = acc, fl

    if v is not None and vib_rule is not None and len(good):
        vstate, vat = vibration_evidence(t, v, vib_rule)
        first = np.maximum.accumulate(np.where(np.r_[True, acc[1:] != acc[:-1]],
                                               np.arange(len(acc)), 0))
        began = np.full(n, np.nan)
        began[good] = t[good][first]  # start of the current pressure state
        prun = (code == 1) & ~flick
        fresh = np.isfinite(vat) & (vat >= began)
        denied = prun & fresh & (vstate == 0)
        unconfirmed = prun & ~(fresh & (vstate == 1)) & ~denied
        code[denied] = 0  # unconfirmed stays pressure-running for change detection

    seq = good[~flick[good] & (code[good] >= 0)]
    changes = []
    if len(seq):
        c = code[seq]
        for k in np.flatnonzero(c[1:] != c[:-1]) + 1:
            changes.append({"at_s": float(t[seq[k]]), "from": _LABEL[c[k - 1]],
                            "to": _LABEL[c[k]]})
    episode = np.full(n, -1)
    episode[good] = np.searchsorted([ch["at_s"] for ch in changes], t[good], side="right")

    states = np.full(n, S.UNKNOWN, dtype=object)
    states[code == 1], states[code == 0] = S.RUNNING, S.OFF
    in_window = np.zeros(n, dtype=bool)
    for ch in changes:
        in_window |= (t >= ch["at_s"]) & (t < ch["at_s"] + rule["transition_s"])
    states[flick | (in_window & (code >= 0))] = S.TRANSITION
    return StateResult(states, episode, changes, int(_count_runs(flick[good])),
                       denied, unconfirmed, _segments(t, states, unconfirmed, denied))


def _segments(t: np.ndarray, states: np.ndarray, unconfirmed: np.ndarray,
              denied: np.ndarray) -> list[dict]:
    """Maximal stretches of one state; unknown samples do not split a stretch. Running
    stretches carry motor_unconfirmed, off stretches pressurized_while_stopped."""
    known = np.flatnonzero((states != S.UNKNOWN) & np.isfinite(t))
    if len(known) == 0:
        return []
    st = states[known]
    attr = np.where(st == S.RUNNING, unconfirmed[known], (st == S.OFF) & denied[known])
    starts = np.flatnonzero(np.r_[True, (st[1:] != st[:-1]) | (attr[1:] != attr[:-1])])
    ends = np.r_[starts[1:], len(known)]
    segs = []
    for a, e in zip(starts, ends, strict=True):
        g = {"state": st[a], "start_s": float(t[known[a]]), "end_s": float(t[known[e - 1]])}
        if st[a] == S.RUNNING:
            g["motor_unconfirmed"] = bool(attr[a])
        elif st[a] == S.OFF:
            g["pressurized_while_stopped"] = bool(attr[a])
        segs.append(g)
    return segs


def _count_runs(mask: np.ndarray) -> int:
    return int(np.sum(mask & ~np.r_[False, mask[:-1]])) if len(mask) else 0


# --- quality flags -----------------------------------------------------------------------

def stale_mask(t: np.ndarray, v: np.ndarray, min_s: float) -> np.ndarray:
    """Samples whose reading was held longer than min_s (missing samples skipped)."""
    out = np.zeros(len(v), dtype=bool)
    ok = np.flatnonzero(np.isfinite(v) & np.isfinite(t))
    if len(ok) == 0:
        return out
    vv, tt = v[ok], t[ok]
    starts = np.flatnonzero(np.r_[True, vv[1:] != vv[:-1]])
    ends = np.r_[starts[1:], len(vv)]
    for a, e in zip(starts, ends, strict=True):
        end_t = tt[e] if e < len(vv) else tt[e - 1]
        if end_t - tt[a] > min_s:
            out[ok[a:e]] = True
    return out


def spike_mask(v: np.ndarray, running: np.ndarray, episode: np.ndarray, z_threshold: float,
               window_updates: int = 30, min_history: int = 10,
               scale_floor_fraction: float = 0.01, revert_within: int = 2,
               return_z: float = 3.5) -> np.ndarray:
    """Spikes: readings that jump away from the running baseline and come back.

    Robust z of a reading against the previous window_updates readings of the same running
    episode: |x - median| / max(1.4826 * MAD, floor * |median|). A reading above z_threshold
    is a spike only if a later reading returns within return_z of the same baseline within
    revert_within readings; then every reading of the excursion is flagged. An excursion that
    does not return in time is a sustained shift: nothing is flagged and the baseline adapts.
    """
    out = np.zeros(len(v), dtype=bool)
    idx, _ = reading_events(np.arange(len(v), dtype=float), v)
    ends = np.r_[idx[1:], len(v)]
    keep = running[idx] if len(idx) else np.array([], dtype=bool)
    reads = [(int(a), int(b), float(v[a]), int(episode[a]))
             for a, b in zip(idx[keep], ends[keep], strict=True)]
    history: list[float] = []
    current, k = None, 0
    while k < len(reads):
        a, _, x, ep = reads[k]
        if ep != current:
            history, current = [], ep
        if len(history) >= min_history:
            h = np.asarray(history[-window_updates:])
            med = float(np.median(h))
            scale = max(1.4826 * float(np.median(np.abs(h - med))),
                        scale_floor_fraction * abs(med), 1e-9)
            if abs(x - med) / scale > z_threshold:
                back = None
                for j in range(1, revert_within + 1):
                    if k + j >= len(reads) or reads[k + j][3] != ep:
                        break
                    if abs(reads[k + j][2] - med) / scale <= return_z:
                        back = k + j
                        break
                if back is not None:
                    for m in range(k, back):
                        seg = np.arange(reads[m][0], reads[m][1])
                        out[seg[running[seg] & np.isfinite(v[seg])]] = True
                    k = back
                    continue
                stop = min(k + revert_within + 1, len(reads))  # sustained: baseline adapts
                history += [r[2] for r in reads[k:stop] if r[3] == ep]
                k = stop
                continue
        history.append(x)
        k += 1
    return out


# --- per file ----------------------------------------------------------------------------

def _state_for(df: pd.DataFrame, ts_col: str, t: np.ndarray, pump: str, rules: dict,
               vibration: bool = True) -> StateResult:
    dup, nonmono = cira.timestamp_flags(df[ts_col])
    p = _num(df[f"{pump}_{rules['state_signal']}"])
    reasons = {
        "missing": ~np.isfinite(p),
        "placeholder_suspected": np.isin(p, list(cira.PLACEHOLDERS)),
        "duplicate_timestamp": dup,
        "non_monotonic": nonmono,
    }
    bad = ~np.isfinite(t)
    for name in rules["settings"]["unknown_if"]:
        bad |= reasons[name]
    rule = rules["pumps"][pump]
    if vibration and "vibration" in rule:
        v = _num(df[f"{pump}_{rules['vibration']['signal']}"])
        return classify_states(t, p, bad, rule, v=v, vib_rule=rule["vibration"])
    return classify_states(t, p, bad, rule)


@dataclass
class Annotation:
    pump: str
    day: str
    df: pd.DataFrame
    ts_col: str
    t: np.ndarray  # seconds since the first valid timestamp; NaN where unparseable
    t0: pd.Timestamp
    states: np.ndarray
    result: StateResult
    stale: dict[str, np.ndarray]  # by original header
    spike: dict[str, np.ndarray]


def stale_limit_s(rules: dict, short: str) -> float:
    return float(rules["stale"]["per_signal"].get(short, rules["stale"]["default_s"]))


def annotate(path: Path, rules: dict) -> Annotation:
    pump, day = cira.parse_name(path)
    df, ts_col, _ = cira.load(path)
    t = _seconds(df[ts_col])
    res = _state_for(df, ts_col, t, pump, rules)
    running = res.states == S.RUNNING
    stale, spike = {}, {}
    for c in df.columns:
        if c == ts_col:
            continue
        v = _num(df[c])
        stale[c] = stale_mask(t, v, stale_limit_s(rules, _short(str(c), pump)))
        spike[c] = spike_mask(v, running, res.episode, **rules["spike"])
    return Annotation(pump, day, df, ts_col, t, df[ts_col].min(), res.states, res, stale, spike)


# --- derivation of the full rule set -----------------------------------------------------

def _previous_stale_limit(holds: np.ndarray) -> float:
    """The step 2a rule, max(60 s, 3 x median hold), kept only for the before/after report."""
    h = holds[np.isfinite(holds)]
    med = float(np.median(h)) if len(h) else 0.0
    return float(max(60.0, math.ceil(3 * med / 10) * 10))


def derive_rules(raw_dir: Path, settings: dict | None = None, stale: dict | None = None,
                 spike: dict | None = None, vibration: dict | None = None,
                 state_signal: str = "Pres.PV") -> tuple[dict, dict]:
    """Derive thresholds, motor idle levels and stale limits. Returns (rules, derivation)."""
    s = _known(DEFAULT_SETTINGS, settings)
    vb = _known(VIBRATION_DEFAULTS, vibration)
    st = _known(STALE_DEFAULTS, stale)
    files = sorted(p for p in raw_dir.rglob("*.csv") if cira.FILENAME.match(p.name))
    loaded = []
    for f in files:
        pump, _ = cira.parse_name(f)
        df, ts_col, _ = cira.load(f)
        loaded.append((f, pump, df, ts_col, _seconds(df[ts_col])))

    # 1. pressure thresholds per pump
    pressure, p_holds = defaultdict(list), defaultdict(list)
    for _, pump, df, _, t in loaded:
        p = _num(df[f"{pump}_{state_signal}"])
        pressure[pump].append(p)
        p_holds[pump].append(reading_events(t, p)[1])
    rules = {
        "generated": {"by": "pumpcopilot rules cira", "on": datetime.now(UTC).date().isoformat(),
                      "files": [f.name for f in files]},
        "state_signal": state_signal, "settings": s, "vibration": vb,
        "stale": {**st, "per_signal": {}}, "spike": _known(SPIKE_DEFAULTS, spike), "pumps": {},
    }
    for pump in sorted(pressure):
        h = np.concatenate(p_holds[pump])
        rules["pumps"][pump] = derive_pump_rule(np.concatenate(pressure[pump]),
                                                float(np.nanmedian(h)), s)

    # 2. motor idle level per pump; its update period is taken while pressure says running
    motor, m_holds = defaultdict(list), defaultdict(list)
    for _, pump, df, ts_col, t in loaded:
        v = _num(df[f"{pump}_{vb['signal']}"])
        prun = _state_for(df, ts_col, t, pump, rules, vibration=False).states == S.RUNNING
        idx, hold = reading_events(t, v)
        motor[pump].append(v)
        m_holds[pump].append(hold[prun[idx]])
    for pump in sorted(motor):
        h = np.concatenate(m_holds[pump])
        period = float(np.nanmedian(h)) if np.isfinite(h).any() else 1.0
        rules["pumps"][pump]["vibration"] = derive_vibration_rule(
            np.concatenate(motor[pump]), period, s, vb)

    # 3. sample-and-hold: time between readings per signal, stale limits from running
    states, all_h, run_h = {}, defaultdict(list), defaultdict(list)
    for f, pump, df, ts_col, t in loaded:
        states[f.name] = _state_for(df, ts_col, t, pump, rules).states
        running = states[f.name] == S.RUNNING
        for c in df.columns:
            if c == ts_col:
                continue
            idx, hold = reading_events(t, _num(df[c]))
            all_h[_short(str(c), pump)].append(hold)
            run_h[_short(str(c), pump)].append(hold[running[idx]])
    sah = {}
    for short in sorted(all_h):
        a, r = np.concatenate(all_h[short]), np.concatenate(run_h[short])
        a, r = a[np.isfinite(a)], r[np.isfinite(r)]
        rules["stale"]["per_signal"][short] = stale_limit_from_holds(
            r, st["quantile"], st["min_readings"], st["default_s"])
        sah[short] = {
            "readings": int(len(a) + 1), "readings_running": int(len(r)),
            "median_interval_s": round(float(np.median(a)), 1) if len(a) else None,
            "median_interval_running_s": round(float(np.median(r)), 1) if len(r) else None,
            "p99_interval_running_s": round(float(np.quantile(r, 0.99)), 1) if len(r) else None,
            "limit_before_s": _previous_stale_limit(a),
            "limit_after_s": rules["stale"]["per_signal"][short],
        }

    # 4. stale share before and after, all samples and running samples
    tally = defaultdict(lambda: np.zeros(6))  # n, n_run, before, after, before_run, after_run
    for f, pump, df, ts_col, t in loaded:
        running = states[f.name] == S.RUNNING
        for c in df.columns:
            if c == ts_col:
                continue
            short, v = _short(str(c), pump), _num(df[c])
            before = stale_mask(t, v, sah[short]["limit_before_s"] - 1e-6)  # old rule was >=
            after = stale_mask(t, v, sah[short]["limit_after_s"])
            tally[short] += [len(v), running.sum(), before.sum(), after.sum(),
                             (before & running).sum(), (after & running).sum()]
    for short, (n, nr, b, a, br, ar) in tally.items():
        sah[short].update({
            "stale_pct_before": round(100 * b / n, 2), "stale_pct_after": round(100 * a / n, 2),
            "stale_running_pct_before": round(100 * br / nr, 2) if nr else None,
            "stale_running_pct_after": round(100 * ar / nr, 2) if nr else None,
        })

    histograms = {}
    for pump, parts in sorted(pressure.items()):
        p = np.concatenate(parts)
        p = p[np.isfinite(p)]
        counts, _ = np.histogram(p, bins=np.arange(0, p.max() + 2.5, 2.5))
        histograms[pump] = [[2.5 * k, int(n)] for k, n in enumerate(counts)]
    return rules, {"histograms_2p5_bar": histograms, "sample_and_hold": sah}


# --- report ------------------------------------------------------------------------------

def _clock(ann: Annotation, at_s: float | None) -> str | None:
    return None if at_s is None else str(ann.t0 + pd.Timedelta(seconds=at_s))


def _diff_s(ours: str | None, ref: pd.Timestamp) -> float | None:
    return None if ours is None else float((pd.Timestamp(ours) - ref).total_seconds())


def pump_day_report(ann: Annotation, max_interval_s: float) -> dict:
    n = len(ann.t)
    valid = np.flatnonzero(np.isfinite(ann.t))
    order = valid[np.argsort(ann.t[valid], kind="stable")]
    iv = np.clip(np.diff(ann.t[order]), 0, None)
    dur = np.zeros(n)
    dur[order[:-1]] = np.minimum(iv, max_interval_s)
    seconds = {s: float(dur[ann.states == s].sum()) for s in STATES}
    seconds[S.UNKNOWN] += float((iv - np.minimum(iv, max_interval_s)).sum())
    res = ann.result
    running = ann.states == S.RUNNING

    starts = [c["at_s"] for c in res.changes if c["to"] == S.RUNNING]
    stops = [c["at_s"] for c in res.changes if c["to"] == S.OFF]
    confirmed = np.flatnonzero(running & ~res.unconfirmed)
    key = f"{ann.pump}_{ann.day}"
    rep = {
        "samples": {str(s): int((ann.states == s).sum()) for s in STATES},
        "seconds": {str(s): round(v, 1) for s, v in seconds.items()},
        "state_changes": len(res.changes),
        "starts": len(starts),
        "stops": len(stops),
        "flickers": res.flickers,
        "pressurized_while_stopped_s": round(float(dur[res.denied].sum()), 1),
        "running_confirmed_s": round(float(dur[running & ~res.unconfirmed].sum()), 1),
        "running_unconfirmed_s": round(float(dur[running & res.unconfirmed].sum()), 1),
        "segments": [{"state": str(g["state"]), "start": _clock(ann, g["start_s"]),
                      "end": _clock(ann, g["end_s"]),
                      "duration_s": round(g["end_s"] - g["start_s"], 1),
                      **{k: v for k, v in g.items()
                         if k in ("motor_unconfirmed", "pressurized_while_stopped")}}
                     for g in res.segments],
        "changes": [{"at": _clock(ann, c["at_s"]), "to": str(c["to"])} for c in res.changes],
        "first_start": _clock(ann, starts[0] if starts else None),
        "first_confirmed_running": (str(ann.df[ann.ts_col].iloc[confirmed[0]])
                                    if len(confirmed) else None),
        "last_stop": _clock(ann, stops[-1] if stops else None),
        "stale": {_short(str(c), ann.pump): int(m.sum()) for c, m in ann.stale.items()},
        # held readings are expected while idle; while running they are the suspicious ones
        "stale_running": {_short(str(c), ann.pump): int((m & (ann.states == S.RUNNING)).sum())
                          for c, m in ann.stale.items()},
        "spike": {_short(str(c), ann.pump): int(m.sum()) for c, m in ann.spike.items()},
    }
    if key in DESCRIPTOR_TABLE1:
        up, down = (pd.Timestamp(f"{ann.day} {x}", tz="UTC") for x in DESCRIPTOR_TABLE1[key])
        rep["descriptor_table1"] = {
            "startup": str(up), "shutdown": str(down),
            "first_start_minus_startup_s": _diff_s(rep["first_start"], up),
            "last_stop_minus_shutdown_s": _diff_s(rep["last_stop"], down),
        }
    return rep


def state_report(raw_dir: Path, rules: dict) -> dict:
    files = sorted(p for p in raw_dir.rglob("*.csv") if cira.FILENAME.match(p.name))
    max_iv = rules["settings"]["max_interval_s"]
    days = {p.stem: pump_day_report(annotate(p, rules), max_iv) for p in files}
    return {"rules_generated": rules.get("generated"), "pump_days": days}


# --- files -------------------------------------------------------------------------------

RULES_HEADER = """\
# CIRA operating rules. GENERATED by `pumpcopilot rules cira`; derivation, histograms and the
# Table 1 check in reports/cira_operating_rules.md. Edit settings/vibration/stale/spike here
# and re-run to re-derive; thresholds and stale limits are always re-derived from the data.
#
# State rule, per pump:
#   Pressure (state_signal): running above upper_bar, off below lower_bar, in between the
#   previous state holds (hysteresis). Thresholds sit at 1/3 and 2/3 of the gap between
#   idle_ceiling_bar and running_floor_bar (quantiles of the Otsu idle/running classes).
#   A candidate change must last min_state_s (1.5 pressure readings), else it is a flicker
#   and labelled transition. Samples in settings.unknown_if are unknown before any rule runs.
#   Motor veto (vibration.signal, derived the same way on log10): pressure decides; where it
#   says running, a motor reading taken since that pressure state began and showing idle
#   vetoes it -> off ("pressurized while stopped"). With no such reading the sample stays
#   running and its segment carries motor_unconfirmed (an attribute, not a quality flag).
#   The first transition_s (2 pressure readings) after each change are transition.
# Stale: a reading held longer than per_signal[signal] seconds, the 99th percentile of that
#   signal's time between readings while running (default_s if too few readings).
# Spike: robust z of a new reading against the previous window_updates readings of the same
#   running episode above z_threshold, AND a return to within return_z within revert_within
#   readings. Sustained shifts are never spikes. Flags never change or drop values.
"""


def write_rules(rules: dict, path: Path) -> None:
    body = yaml.safe_dump(_plain(rules), sort_keys=False, default_flow_style=None, width=100)
    path.write_text(RULES_HEADER + body)


def load_rules(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _plain(o):
    """YAML-safe copy. Floats are written exactly so re-deriving from the file is idempotent."""
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    return o


def _cell(x) -> str:
    return "-" if x is None else f"{x:g}" if isinstance(x, float) else str(x)


def derivation_markdown(rules: dict, derivation: dict, report: dict) -> str:
    sp = rules["spike"]
    vib = rules["vibration"]
    out = ["# CIRA operating rules: derivation", "",
           f"Generated {rules['generated']['on']} by `{rules['generated']['by']}` from "
           f"{len(rules['generated']['files'])} files. Rule text: header of "
           "`data/operating_rules.yaml`. Assumptions A1-A6 in `docs/ASSUMPTIONS.md`.", "",
           "## Thresholds per pump (outlet pressure, bar)", "",
           "| pump | idle median | idle ceiling | lower | upper | running floor | running median"
           " | Otsu split | running share | update s | min state s | transition s |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for pump, r in rules["pumps"].items():
        out.append(
            f"| {pump} | {r['idle_median_bar']} | {r['idle_ceiling_bar']} | **{r['lower_bar']}**"
            f" | **{r['upper_bar']}** | {r['running_floor_bar']} | {r['running_median_bar']}"
            f" | {r['otsu_split_bar']} | {r['running_share']:.1%} | {r['pressure_update_s']}"
            f" | {r['min_state_s']} | {r['transition_s']} |")

    out += ["", f"## Motor vibration confirmation ({vib['signal']}, m/s^2)", "",
            f"Same derivation as pressure, on {'log10 of ' if vib['log10'] else ''}the motor "
            f"accelerometer peak value, with class quantiles {vib['gap_quantiles']} (the peak "
            "value is heavy-tailed). Pressure decides the state; a motor reading can only veto "
            "running (idle reading: off, pressurized while stopped), and only if it was taken "
            "after the current pressure state began: the sensor is sample-and-hold, and a "
            "reading held from before a start says nothing about the start. Running without "
            "such a reading stays running, with the segment attribute motor_unconfirmed.", "",
            "| pump | idle median | idle ceiling | lower | upper | running floor | running median"
            " | update s (running) | min state s |", "|---|---|---|---|---|---|---|---|---|"]
    for pump, r in rules["pumps"].items():
        m = r["vibration"]
        out.append(f"| {pump} | {m['idle_median']} | {m['idle_ceiling']} | **{m['lower']}** |"
                   f" **{m['upper']}** | {m['running_floor']} | {m['running_median']} |"
                   f" {m['update_s']} | {m['min_state_s']} |")

    out += ["", "## Pressure histograms (2.5 bar bins, all days pooled)", ""]
    hist = derivation["histograms_2p5_bar"]
    out.append("| bin (bar) | " + " | ".join(hist) + " |")
    out.append("|---" * (len(hist) + 1) + "|")
    for k in range(max(len(h) for h in hist.values())):
        cells = [str(h[k][1]) if k < len(h) else "0" for h in hist.values()]
        out.append(f"| {2.5 * k:g}-{2.5 * (k + 1):g} | " + " | ".join(cells) + " |")

    sah = derivation["sample_and_hold"]
    out += ["", "## Sample-and-hold readings", "",
            "A reading event is a value change; the gateway repeats the last reading at 1 Hz in"
            " between. Intervals are seconds between reading events, pooled over pumps and days.",
            "", "| signal | readings | while running | median interval | median interval running"
            " | p99 interval running |", "|---|---|---|---|---|---|"]
    for short, d in sah.items():
        out.append(f"| {short} | {d['readings']} | {d['readings_running']} |"
                   f" {_cell(d['median_interval_s'])} | {_cell(d['median_interval_running_s'])} |"
                   f" {_cell(d['p99_interval_running_s'])} |")
    out += ["", "## Stale before and after", "",
            "Before: step 2a rule, held at least max(60 s, 3 x median interval). After: held "
            f"longer than the p{rules['stale']['quantile'] * 100:g} interval while running.", "",
            "| signal | limit before s | limit after s | stale % all: before | after"
            " | stale % running: before | after |", "|---|---|---|---|---|---|---|"]
    for short, d in sah.items():
        out.append(f"| {short} | {d['limit_before_s']:g} | {d['limit_after_s']:g} |"
                   f" {d['stale_pct_before']} | {d['stale_pct_after']} |"
                   f" {_cell(d['stale_running_pct_before'])} |"
                   f" {_cell(d['stale_running_pct_after'])} |")

    m = sp["revert_within"]
    out += ["", "## Spike decision delay", "",
            f"A reading with robust z above {sp['z_threshold']:g} is only flagged once a later "
            f"reading returns to within z {sp['return_z']:g} of the baseline, within {m} "
            f"readings. So a spike decision waits for up to {m} further readings: the delay "
            "below is that many reading intervals while running. Replay and live scoring must "
            "hold the flag back that long.", "",
            f"| signal | typical delay s ({m} x median interval) | slow delay s ({m} x p99) |",
            "|---|---|---|"]
    for short, d in sah.items():
        med, p99 = d["median_interval_running_s"], d["p99_interval_running_s"]
        out.append(f"| {short} | {_cell(None if med is None else round(m * med, 1))} |"
                   f" {_cell(None if p99 is None else round(m * p99, 1))} |")

    out += ["", "## Check against descriptor Table 1", "",
            "Start and stop are changes of the final state (pressure, with the motor veto). "
            "Our stop is the first sample below the threshold, so +1 s is an exact match.", "",
            "| pump-day | Table 1 startup | ours | diff s | first confirmed running"
            " | Table 1 shutdown | ours | diff s | pressurized while stopped s"
            " | running s: motor confirmed | motor unconfirmed |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, d in report["pump_days"].items():
        t1 = d.get("descriptor_table1")
        if not t1:
            continue
        out.append(
            f"| {key} | {t1['startup'][11:19]} | {(d['first_start'] or '-')[11:19]}"
            f" | {_cell(t1['first_start_minus_startup_s'])}"
            f" | {(d['first_confirmed_running'] or '-')[11:19]} | {t1['shutdown'][11:19]}"
            f" | {(d['last_stop'] or '-')[11:19]} | {_cell(t1['last_stop_minus_shutdown_s'])}"
            f" | {d['pressurized_while_stopped_s']:g} | {d['running_confirmed_s']:g}"
            f" | {d['running_unconfirmed_s']:g} |")
    return "\n".join(out) + "\n"
