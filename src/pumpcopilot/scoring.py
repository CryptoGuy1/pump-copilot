"""CIRA baseline, scoring and evaluation.

Pipeline, per pump and day (days are never joined by time):
1. Eligible readings: reading events (A5) from running segments only; transition, stale- and
   spike-flagged readings are excluded. Temperatures are normalised against the ambient
   temperature when that signal exists (the report says so either way).
2. Features: rolling windows over the eligible readings of each signal, minute-aligned inside
   running stretches: median, MAD (unscaled) and the fresh reading count.
3. Baseline: per signal, center = median of the fit day's window medians, band = center
   +/- max(k x MAD, floor_fraction x |center|). Too little running time or too few readings on
   the fit day means abstention.
4. Scoring: one ScoredEvidence per (signal, window). review_suggested needs N consecutive
   windows outside the band; no samples -> data_unavailable; too few fresh readings ->
   insufficient_evidence.

Synthetic faults are applied to in-memory copies only (DayData.synthetic, provenance
"synthetic_injection"); nothing in this module writes to the database.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import itertools
import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .schema import (
    CalibrationStatus,
    EvidenceItem,
    FeatureWindow,
    PresentationState,
    ScoredEvidence,
    SourceDataset,
)

P = PresentationState
FAULTS = ("step", "ramp", "drift", "stuck", "dropout")
EXCLUDED_FLAGS = ("stale_suspected", "spike_suspected")
SYNTHETIC = "synthetic_injection"
TUNING_DAY = ("cira-pump-B", dt.date(2024, 6, 11))
# what counts as detecting each fault: offsets must raise a review; a stuck sensor stops
# producing fresh readings; a dropout removes the samples themselves
DETECTED_AS = {
    "step": (P.REVIEW_SUGGESTED,), "ramp": (P.REVIEW_SUGGESTED,), "drift": (P.REVIEW_SUGGESTED,),
    "stuck": (P.INSUFFICIENT_EVIDENCE,),
    "dropout": (P.DATA_UNAVAILABLE, P.INSUFFICIENT_EVIDENCE),
}

DEFAULT_CONFIG = {
    "features": {"window_s": 600, "step_s": 60, "min_fresh_readings": 3,
                 "min_fresh_fraction": 0.5},
    "baseline": {"k": 5.0, "floor_fraction": 0.001, "min_fit_running_s": 7200,
                 "min_fit_readings": 100},
    "review": {"consecutive_windows": 3},
    "normalization": {"ambient_signal": "ambient_temperature",
                      "barometric_signal": "ambient_pressure", "temperature_unit": "degC"},
    "injection": {"signals": ["outlet_pressure", "pump_vibration_velocity",
                              "motor_casing_temperature"],
                  "offset_sizes_sigma": [1.0, 3.0, 6.0], "durations_s": [300, 1200, 3600],
                  "ramp_s": 600, "drift_s": 7200, "offset_horizon_s": 3600},
    "tuning": {"split_fraction": 0.6},
}
MODEL_SECTIONS = ("features", "baseline", "review", "normalization")


def merge_config(base: dict, override: dict | None = None) -> dict:
    """Deep merge onto DEFAULT_CONFIG (then base, then override)."""
    def deep(a, b):
        out = copy.deepcopy(a)
        for k, v in (b or {}).items():
            out[k] = deep(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) \
                else copy.deepcopy(v)
        return out
    return deep(deep(DEFAULT_CONFIG, base), override)


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _ns(ts) -> np.ndarray | int:
    if isinstance(ts, pd.Series):
        return ts.dt.as_unit("ns").astype("int64").to_numpy()
    return pd.Timestamp(ts).as_unit("ns").value


# --- data --------------------------------------------------------------------------------

@dataclass
class DayData:
    """One asset-day: reading events, per-minute sample counts, running stretches."""
    asset_id: str
    source_day: dt.date
    readings: pd.DataFrame  # observed_at, signal_name, value, unit, held_s, operating_state,
    #                         quality_flags, provenance_hash[, provenance]
    samples_1m: pd.DataFrame  # bucket, signal_name, sample_count
    running: list[tuple[pd.Timestamp, pd.Timestamp]]  # [start, end) of running stretches
    synthetic: bool = False
    injections: list[dict] = field(default_factory=list)


def load_day(conn, asset_id: str, source_day: dt.date, merge_gap_s: float = 10.0) -> DayData:
    """Read one asset-day from the database (read-only)."""
    from . import db

    r = pd.DataFrame(db.fetch_readings(conn, asset_id, source_day))
    r["observed_at"] = pd.to_datetime(r["observed_at"], utc=True)
    r["quality_flags"] = r["quality_flags"].map(tuple)
    r["provenance"] = "real"
    s = pd.DataFrame(db.fetch_day_1m(conn, asset_id, source_day))
    s = s[["bucket", "signal_name", "sample_count"]].assign(
        bucket=lambda d: pd.to_datetime(d["bucket"], utc=True))
    running: list[list[pd.Timestamp]] = []
    for seg in db.fetch_segments(conn, asset_id, source_day):
        if seg["state"] != "running":
            continue
        a = pd.Timestamp(seg["start_at"])
        b = pd.Timestamp(seg["end_at"]) + pd.Timedelta(seconds=1)
        if running and (a - running[-1][1]).total_seconds() <= merge_gap_s:
            running[-1][1] = b  # motor_unconfirmed splits are attributes, not stops
        else:
            running.append([a, b])
    return DayData(asset_id, source_day, r, s, [(a, b) for a, b in running])


def slice_day(day: DayData, start: pd.Timestamp | None, end: pd.Timestamp | None) -> DayData:
    lo = start if start is not None else pd.Timestamp.min.tz_localize("UTC")
    hi = end if end is not None else pd.Timestamp.max.tz_localize("UTC")
    r = day.readings[(day.readings.observed_at >= lo) & (day.readings.observed_at < hi)]
    s = day.samples_1m[(day.samples_1m.bucket >= lo) & (day.samples_1m.bucket < hi)]
    run = [(max(a, lo), min(b, hi)) for a, b in day.running if min(b, hi) > max(a, lo)]
    return DayData(day.asset_id, day.source_day, r.copy(), s.copy(), run, day.synthetic,
                   list(day.injections))


@dataclass
class Prepared:
    asset_id: str
    source_day: dt.date
    readings: pd.DataFrame  # eligible, normalised; signal_name is the scored signal
    samples_1m: pd.DataFrame
    running: list[tuple[pd.Timestamp, pd.Timestamp]]
    signals: list[str]
    units: dict[str, str]
    normalization: str
    synthetic: bool


def scored_name(signal: str, prep: Prepared) -> str:
    rel = f"{signal}_rel_ambient"
    return rel if rel in prep.signals else signal


def _in_running(t: pd.Series, running) -> np.ndarray:
    if not running:
        return np.zeros(len(t), dtype=bool)
    starts = np.array([_ns(a) for a, _ in running])
    ends = np.array([_ns(b) for _, b in running])
    tn = _ns(t)
    i = np.searchsorted(starts, tn, side="right") - 1
    return (i >= 0) & (tn < ends[np.clip(i, 0, None)])


def prepare(day: DayData, cfg: dict, signals: list[str] | None = None) -> Prepared:
    """Eligible readings of the scored signals, temperatures normalised against ambient."""
    n = cfg["normalization"]
    amb, baro = n["ambient_signal"], n["barometric_signal"]
    r = day.readings
    if "provenance" not in r:
        r = r.assign(provenance="real")
    present = set(r.signal_name)
    flagged = r.quality_flags.map(lambda f: any(x in EXCLUDED_FLAGS for x in f))
    ok = ((r.operating_state == "running") & ~flagged & np.isfinite(r.value)
          & _in_running(r.observed_at, day.running))
    elig = r[ok]
    units = r.groupby("signal_name")["unit"].first().to_dict()
    temps = sorted(s for s, u in units.items() if u == n["temperature_unit"] and s != amb)
    wanted = [s for s in sorted(present - {amb, baro}) if signals is None or s in signals]

    parts, rename = [], {}
    ambient = None
    if amb in present:
        a = r[(r.signal_name == amb) & ~flagged & np.isfinite(r.value)]
        ambient = a[["observed_at", "value"]].rename(columns={"value": "_amb"}).sort_values(
            "observed_at")
        note = (f"temperatures ({', '.join(temps)}) normalised as value minus {amb} (latest "
                "ambient reading at or before each reading, same day only)")
    else:
        note = f"no ambient temperature signal ({amb}) found: temperatures are not normalised"
    if baro in present:
        b = r[r.signal_name == baro].value
        note += (f"; barometric signal {baro} found (range {b.max() - b.min():.1f} "
                 f"{units[baro]} this day), not used: pressures are scored as measured")
    else:
        note += f"; no barometric signal ({baro}) found"
    for s in wanted:
        g = elig[elig.signal_name == s]
        if ambient is not None and s in temps:
            g = pd.merge_asof(g.sort_values("observed_at"), ambient, on="observed_at",
                              direction="backward").dropna(subset=["_amb"])
            g = g.assign(value=g.value - g._amb, unit="K",
                         signal_name=f"{s}_rel_ambient").drop(columns="_amb")
            rename[s] = f"{s}_rel_ambient"
        parts.append(g)
    out = pd.concat(parts, ignore_index=True) if parts else elig.iloc[0:0]
    scored = sorted(rename.get(s, s) for s in wanted)
    smp = day.samples_1m[day.samples_1m.signal_name.isin(wanted)].replace(
        {"signal_name": rename})
    out_units = {rename.get(s, s): ("K" if s in rename else units[s]) for s in wanted}
    return Prepared(day.asset_id, day.source_day, out, smp, list(day.running), scored,
                    out_units, note, day.synthetic)


# --- features ----------------------------------------------------------------------------

def windows(prep: Prepared, cfg: dict) -> pd.DataFrame:
    w, s = cfg["features"]["window_s"], cfg["features"]["step_s"]
    rows = []
    for k, (a, b) in enumerate(prep.running):
        start = a.ceil("min")
        while start + pd.Timedelta(seconds=w) <= b:
            rows.append((start, start + pd.Timedelta(seconds=w), k))
            start += pd.Timedelta(seconds=s)
    return pd.DataFrame(rows, columns=["start", "end", "stretch"])


def features(prep: Prepared, cfg: dict, signals: list[str] | None = None) -> pd.DataFrame:
    """Per signal and window: median, MAD, fresh reading count, sample count."""
    win = windows(prep, cfg)
    cols = ["start", "end", "stretch", "signal_name", "unit", "median", "mad", "fresh",
            "samples"]
    if win.empty:
        return pd.DataFrame(columns=cols)
    ws, we = _ns(win.start), _ns(win.end)
    frames = []
    for sig in (signals or prep.signals):
        g = prep.readings[prep.readings.signal_name == sig].sort_values("observed_at")
        t, v = _ns(g.observed_at), g.value.to_numpy(dtype=float)
        lo, hi = np.searchsorted(t, ws, "left"), np.searchsorted(t, we, "left")
        med = np.full(len(win), np.nan)
        mad = np.full(len(win), np.nan)
        for i, (a, b) in enumerate(zip(lo, hi, strict=True)):
            if b > a:
                x = v[a:b]
                m = np.median(x)
                med[i], mad[i] = m, np.median(np.abs(x - m))
        sm = prep.samples_1m[prep.samples_1m.signal_name == sig].sort_values("bucket")
        cum = np.r_[0, np.cumsum(sm.sample_count.to_numpy())]
        bt = _ns(sm.bucket) if len(sm) else np.array([], dtype=np.int64)
        samples = cum[np.searchsorted(bt, we, "left")] - cum[np.searchsorted(bt, ws, "left")]
        frames.append(win.assign(signal_name=sig, unit=prep.units.get(sig), median=med, mad=mad,
                                 fresh=hi - lo, samples=samples))
    return pd.concat(frames, ignore_index=True)[cols]


# --- baseline ----------------------------------------------------------------------------

@dataclass
class Baseline:
    asset_id: str
    source_day: dt.date
    bands: dict[str, dict]
    signal_abstentions: dict[str, str]
    abstained: str | None
    running_s: float
    model_id: str
    model_version: str
    normalization: str


def model_ids(cfg: dict, fit_fingerprint: str) -> tuple[str, str]:
    part = {k: cfg[k] for k in MODEL_SECTIONS}
    return (f"cira-band-{_hash(part)[:12]}",
            _hash({"config": part, "fit": fit_fingerprint})[:16])


def fit_baseline(prep: Prepared, cfg: dict, feats: pd.DataFrame | None = None) -> Baseline:
    b, fcfg = cfg["baseline"], cfg["features"]
    running_s = sum((e - s).total_seconds() for s, e in prep.running)
    fit = prep.readings.sort_values(["signal_name", "observed_at"])
    fingerprint = _hash([prep.asset_id, str(prep.source_day),
                         list(zip(fit.provenance_hash.astype(str), fit.value.round(12),
                                  strict=True))])
    mid, ver = model_ids(cfg, fingerprint)
    if running_s < b["min_fit_running_s"]:
        why = (f"baseline abstained: fit day {prep.source_day} has {running_s / 3600:.2f} h "
               f"running, minimum is {b['min_fit_running_s'] / 3600:.2f} h")
        return Baseline(prep.asset_id, prep.source_day, {}, {}, why, running_s, mid, ver,
                        prep.normalization)
    f = feats if feats is not None else features(prep, cfg)
    bands, abst = {}, {}
    for sig in prep.signals:
        g = prep.readings[prep.readings.signal_name == sig]
        if len(g) < b["min_fit_readings"]:
            abst[sig] = f"{len(g)} fit readings < {b['min_fit_readings']} required"
            continue
        interval = float(np.nanmedian(g.held_s)) if g.held_s.notna().any() else math.nan
        expected = fcfg["window_s"] / interval if interval > 0 else 0.0
        min_fresh = max(fcfg["min_fresh_readings"],
                        math.ceil(fcfg["min_fresh_fraction"] * expected))
        med = f[(f.signal_name == sig) & (f.fresh >= min_fresh)]["median"].dropna()
        if med.empty:
            abst[sig] = f"no fit window with {min_fresh} fresh readings"
            continue
        center = float(med.median())
        mad = float(np.median(np.abs(med - center)))
        half = max(b["k"] * mad, b["floor_fraction"] * abs(center))
        x = g.value.to_numpy(dtype=float)
        bands[sig] = {
            "center": center, "mad": mad, "halfwidth": half, "low": center - half,
            "high": center + half, "unit": prep.units[sig], "readings": int(len(g)),
            "windows": int(len(med)), "typical_interval_s": interval, "min_fresh": int(min_fresh),
            "sigma": float(1.4826 * np.median(np.abs(x - np.median(x)))),
        }
    return Baseline(prep.asset_id, prep.source_day, bands, abst, None, running_s, mid, ver,
                    prep.normalization)


# --- scoring -----------------------------------------------------------------------------

def score_frame(prep: Prepared, base: Baseline, cfg: dict,
                feats: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per (signal, window): state, reason, band-relative score and consecutive-outside run."""
    f = (feats if feats is not None else features(prep, cfg)).copy()
    n_req = cfg["review"]["consecutive_windows"]
    step = pd.Timedelta(seconds=cfg["features"]["step_s"])
    f = f.sort_values(["signal_name", "start"]).reset_index(drop=True)
    state, reason, score, outside, run = [], [], [], [], []
    for sig, g in f.groupby("signal_name", sort=False):
        band = base.bands.get(sig)
        prev_start, prev_stretch, r = None, None, 0
        for row in g.itertuples():
            out, sc, st, why = False, np.nan, None, None
            if base.abstained:
                st, why = P.INSUFFICIENT_EVIDENCE, base.abstained
            elif band is None:
                st = P.INSUFFICIENT_EVIDENCE
                why = f"no baseline for {sig}: {base.signal_abstentions.get(sig, 'not in fit')}"
            elif row.samples == 0:
                st, why = P.DATA_UNAVAILABLE, f"no samples for {sig} in window"
            elif row.fresh < band["min_fresh"]:
                st = P.INSUFFICIENT_EVIDENCE
                why = f"{row.fresh} fresh readings < {band['min_fresh']} required for {sig}"
            else:
                sc = abs(row.median - band["center"]) / band["halfwidth"]
                out = sc > 1.0
            consecutive = (prev_start is not None and row.stretch == prev_stretch
                           and row.start - prev_start == step)
            r = (r + 1 if consecutive else 1) if out else 0
            if st is None:
                st = P.REVIEW_SUGGESTED if r >= n_req else P.NORMAL
            prev_start, prev_stretch = row.start, row.stretch
            state.append(st)
            reason.append(why)
            score.append(sc)
            outside.append(out)
            run.append(r)
    f["state"], f["reason"], f["score"], f["outside"], f["run"] = state, reason, score, outside, run
    for key in ("center", "low", "high"):
        f[key] = f.signal_name.map(lambda s, k=key: base.bands.get(s, {}).get(k, np.nan))
    return f


def _evidence_item(j: int, w) -> EvidenceItem:
    return EvidenceItem(
        ref=f"E{j + 1}", signal_name=w.signal_name, value=float(w.median),
        baseline_low=float(w.low), baseline_high=float(w.high), unit=w.unit,
        statement=(f"{w.signal_name} median {w.median:.5g} {w.unit} in window "
                   f"{w.start:%H:%M}-{w.end:%H:%M} is outside the baseline band "
                   f"{w.low:.5g}-{w.high:.5g} {w.unit}"))


def to_scored_evidence(frame: pd.DataFrame, base: Baseline, cfg: dict) -> list[ScoredEvidence]:
    n_req = cfg["review"]["consecutive_windows"]
    out = []
    for _, g in frame.groupby("signal_name", sort=False):
        rows = list(g.itertuples())
        for i, row in enumerate(rows):
            items = ()
            if row.state == P.REVIEW_SUGGESTED:  # cite the N consecutive outside windows
                items = tuple(_evidence_item(j, w) for j, w in
                              enumerate(rows[i - n_req + 1:i + 1]))
            abstaining = row.state in (P.INSUFFICIENT_EVIDENCE, P.DATA_UNAVAILABLE)
            out.append(ScoredEvidence(
                asset_id=base.asset_id, source_dataset=SourceDataset.CIRA,
                model_id=base.model_id, model_version=base.model_version,
                model_domain=SourceDataset.CIRA,
                feature_window=FeatureWindow(start=row.start.to_pydatetime(),
                                             end=row.end.to_pydatetime()),
                evidence=items, score=None if np.isnan(row.score) else float(row.score),
                confidence_calibration_status=CalibrationStatus.NOT_APPLICABLE,
                presentation_state=row.state,
                abstention_reason=row.reason if abstaining else None))
    return out


def review_episodes(frame: pd.DataFrame) -> list[dict]:
    """Maximal runs of review_suggested windows per signal and running stretch."""
    eps = []
    for sig, g in frame.groupby("signal_name", sort=False):
        rev = (g.state == P.REVIEW_SUGGESTED).to_numpy()
        stretch = g.stretch.to_numpy()
        start = None
        for i in range(len(g) + 1):
            on = i < len(g) and rev[i] and (start is None or stretch[i] == stretch[start])
            if on and start is None:
                start = i
            elif not on and start is not None:
                ep = {"signal_name": sig, "windows": i - start}
                if "start" in g:
                    ep.update(start=g.start.iloc[start], end=g.end.iloc[i - 1],
                              max_score=float(g.score.iloc[start:i].max()))
                eps.append(ep)
                start = i if i < len(g) and rev[i] else None
    return eps


def unlabelled_review_rate(frame: pd.DataFrame, running_hours: float) -> float:
    return len(review_episodes(frame)) / running_hours if running_hours > 0 else math.nan


def running_hours(day: DayData | Prepared) -> float:
    return sum((b - a).total_seconds() for a, b in day.running) / 3600


# --- synthetic injection -----------------------------------------------------------------

def inject(day: DayData, signal: str, fault: str, size: float, t0: pd.Timestamp, sigma: float,
           cfg: dict) -> DayData:
    """Copy of `day` with one synthetic fault. Offsets are size x sigma; stuck and dropout use
    size as a duration in seconds. The input is never modified; nothing is written anywhere."""
    if fault not in FAULTS:
        raise ValueError(f"unknown fault {fault}")
    inj = cfg["injection"]
    r = day.readings.copy()
    if "provenance" not in r:
        r["provenance"] = "real"
    s = day.samples_1m.copy()
    sig = r.signal_name == signal
    after = sig & (r.observed_at >= t0)
    secs = (r.observed_at - t0).dt.total_seconds()
    if fault == "step":
        r.loc[after, "value"] += size * sigma
        r.loc[after, "provenance"] = SYNTHETIC
    elif fault in ("ramp", "drift"):
        span = inj["ramp_s"] if fault == "ramp" else inj["drift_s"]
        r.loc[after, "value"] += size * sigma * np.clip(secs[after] / span, 0, 1)
        r.loc[after, "provenance"] = SYNTHETIC
    else:
        end = t0 + pd.Timedelta(seconds=size)
        gone = sig & (r.observed_at >= t0) & (r.observed_at < end)
        held = r.index[sig & (r.observed_at < t0)]
        if fault == "stuck" and len(held):
            r.loc[held[-1], "provenance"] = SYNTHETIC  # this reading is now held through `end`
        r = r[~gone]
        if fault == "dropout":
            m = (s.signal_name == signal) & (s.bucket >= t0) & (s.bucket < end)
            s.loc[m, "sample_count"] = 0
    spec = {"signal": signal, "fault": fault, "size": size, "t0": str(t0), "sigma": sigma,
            "provenance": SYNTHETIC}
    return DayData(day.asset_id, day.source_day, r, s, list(day.running), True,
                   day.injections + [spec])


def fault_size_label(fault: str, size: float) -> str:
    return f"{size / 60:g} min" if fault in ("stuck", "dropout") else f"{size:g} sigma"


def fault_sizes(cfg: dict, fault: str) -> list[float]:
    inj = cfg["injection"]
    return list(inj["durations_s"] if fault in ("stuck", "dropout")
                else inj["offset_sizes_sigma"])


def horizon_s(cfg: dict, fault: str, size: float) -> float:
    inj, w = cfg["injection"], cfg["features"]["window_s"]
    if fault == "drift":
        return inj["drift_s"]
    if fault in ("stuck", "dropout"):
        return size + w
    return inj["offset_horizon_s"]


def start_times(day: DayData | Prepared, cfg: dict, horizon: float, n: int) -> list:
    """n fault starts spread over the running time, each with room for its horizon."""
    lead = pd.Timedelta(seconds=2 * cfg["features"]["window_s"])
    spans = [(a + lead, b - pd.Timedelta(seconds=horizon)) for a, b in day.running]
    spans = [(a, b) for a, b in spans if b > a]
    total = sum((b - a).total_seconds() for a, b in spans)
    if total <= 0 or n <= 0:
        return []
    out, offsets = [], [(i + 0.5) * total / n for i in range(n)]
    for off in offsets:
        for a, b in spans:
            length = (b - a).total_seconds()
            if off <= length:
                out.append((a + pd.Timedelta(seconds=off)).floor("s"))
                break
            off -= length
    return out


def detect(day: DayData, base: Baseline, cfg: dict, signal: str, fault: str, size: float,
           t0: pd.Timestamp, sigma: float, horizon_s: float,
           clean: pd.DataFrame | None = None, prep_signals: list[str] | None = None) -> dict:
    """Inject one fault and compare with the clean scoring of the same day."""
    only = prep_signals or [signal]
    if clean is None:
        clean = score_frame(prepare(day, cfg, only), base, cfg)
    prep = prepare(inject(day, signal, fault, size, t0, sigma, cfg), cfg, only)
    name = scored_name(signal, prep)
    faulty = score_frame(prep, base, cfg, features(prep, cfg, [name]))
    c = clean[clean.signal_name == name][["start", "state"]].rename(columns={"state": "clean"})
    m = faulty[faulty.signal_name == name].merge(c, on="start", how="left")
    target = DETECTED_AS[fault]
    t_end = t0 + pd.Timedelta(seconds=horizon_s)
    hit = m[(m.end > t0) & (m.end <= t_end) & m.state.isin(target) & ~m.clean.isin(target)]
    first = hit.end.min() if len(hit) else None
    return {"signal": signal, "scored_signal": name, "fault": fault, "size": size,
            "size_label": fault_size_label(fault, size), "t0": str(t0), "detected": bool(len(hit)),
            "delay_s": (first - t0).total_seconds() if first is not None else None,
            "detected_as": sorted({str(x) for x in hit.state}) if len(hit) else []}


def run_injections(day: DayData, base: Baseline, cfg: dict, starts_per_fault: int,
                   clean: pd.DataFrame | None = None) -> list[dict]:
    results = []
    prep_all = prepare(day, cfg)
    clean = clean if clean is not None else score_frame(prep_all, base, cfg)
    for signal in cfg["injection"]["signals"]:
        name = scored_name(signal, prep_all)
        band = base.bands.get(name)
        if band is None:
            continue
        for fault in FAULTS:
            for size in fault_sizes(cfg, fault):
                h = horizon_s(cfg, fault, size)
                for t0 in start_times(day, cfg, h, starts_per_fault):
                    results.append(detect(day, base, cfg, signal, fault, size, t0,
                                          band["sigma"], h, clean))
    return results


def summarize_detections(results: list[dict]) -> list[dict]:
    rows = []
    for (fault, size), g in itertools.groupby(
            sorted(results, key=lambda r: (FAULTS.index(r["fault"]), r["size"])),
            key=lambda r: (r["fault"], r["size"])):
        g = list(g)
        delays = [r["delay_s"] for r in g if r["detected"]]
        rows.append({"fault": fault, "size": size, "size_label": fault_size_label(fault, size),
                     "injections": len(g), "detected": sum(r["detected"] for r in g),
                     "detection_rate": sum(r["detected"] for r in g) / len(g),
                     "median_delay_s": float(np.median(delays)) if delays else None})
    return rows


# --- protocol ----------------------------------------------------------------------------

def split_running(day: DayData, fraction: float) -> pd.Timestamp:
    """Time at which `fraction` of the day's running time has elapsed."""
    target = fraction * running_hours(day) * 3600
    for a, b in day.running:
        length = (b - a).total_seconds()
        if target <= length:
            return (a + pd.Timedelta(seconds=target)).floor("min")
        target -= length
    return day.running[-1][1]


def tune(loader, cfg: dict, grid: dict, starts_per_fault: int = 3) -> tuple[dict, list[dict]]:
    """Choose k, N and the window settings on B June alone (within-day time split).

    Objective: the fewest unlabelled review episodes per running hour on the June validation part,
    then the highest mean synthetic detection rate there, then the shortest median delay,
    then the larger k and N (more conservative).
    """
    asset, day = TUNING_DAY
    june = loader(asset, day)
    split = split_running(june, cfg["tuning"]["split_fraction"])
    fit_day, val_day = slice_day(june, None, split), slice_day(june, split, None)
    val_hours = running_hours(val_day)
    table = []
    for w, s in itertools.product(grid["window_s"], grid["step_s"]):
        cw = merge_config(cfg, {"features": {"window_s": w, "step_s": s}})
        prep_fit, prep_val = prepare(fit_day, cw), prepare(val_day, cw)
        f_fit, f_val = features(prep_fit, cw), features(prep_val, cw)
        sigma_base = fit_baseline(prep_fit, cw, f_fit)  # sigma does not depend on k or N
        injected = []  # (spec, features of the injected signal), independent of k and N
        for signal in cw["injection"]["signals"]:
            name = scored_name(signal, prep_val)
            if name not in sigma_base.bands:
                continue
            for fault in FAULTS:
                for size in fault_sizes(cw, fault):
                    h = horizon_s(cw, fault, size)
                    for t0 in start_times(val_day, cw, h, starts_per_fault):
                        p = prepare(inject(val_day, signal, fault, size, t0,
                                           sigma_base.bands[name]["sigma"], cw), cw, [signal])
                        injected.append(((signal, name, fault, size, t0, h),
                                         features(p, cw, [name])))
        for k, n in itertools.product(grid["k"], grid["consecutive_windows"]):
            ckn = merge_config(cw, {"baseline": {"k": k}, "review": {"consecutive_windows": n}})
            base = fit_baseline(prep_fit, ckn, f_fit)
            clean = score_frame(prep_val, base, ckn, f_val)
            results = []
            for (_signal, name, fault, size, t0, h), f_inj in injected:
                faulty = score_frame(prep_val, base, ckn, f_inj)
                c = clean[clean.signal_name == name][["start", "state"]].rename(
                    columns={"state": "clean"})
                m = faulty.merge(c, on="start", how="left")
                target = DETECTED_AS[fault]
                hit = m[(m.end > t0) & (m.end <= t0 + pd.Timedelta(seconds=h))
                        & m.state.isin(target) & ~m.clean.isin(target)]
                results.append({"fault": fault, "size": size, "detected": bool(len(hit)),
                                "delay_s": (hit.end.min() - t0).total_seconds()
                                if len(hit) else None})
            cells = summarize_detections(results)
            delays = [r["delay_s"] for r in results if r["detected"]]
            table.append({
                "window_s": w, "step_s": s, "k": k, "consecutive_windows": n,
                "unlabelled_reviews": len(review_episodes(clean)),
                "unlabelled_reviews_per_running_hour": round(
                    unlabelled_review_rate(clean, val_hours), 4),
                "mean_detection_rate": round(float(np.mean([c["detection_rate"] for c in cells]))
                                             if cells else 0.0, 4),
                "median_delay_s": float(np.median(delays)) if delays else None,
                "injections": len(results)})
    best = min(table, key=lambda r: (r["unlabelled_reviews_per_running_hour"],
                                     -r["mean_detection_rate"],
                                     r["median_delay_s"] if r["median_delay_s"] is not None
                                     else math.inf, -r["k"], -r["consecutive_windows"]))
    frozen = merge_config(cfg, {
        "features": {"window_s": best["window_s"], "step_s": best["step_s"]},
        "baseline": {"k": best["k"]},
        "review": {"consecutive_windows": best["consecutive_windows"]}})
    frozen["frozen"] = {
        "on": dt.date.today().isoformat(),
        "tuned_on": {"asset_id": asset, "source_day": str(day)},
        "split": {"fit": f"running time before {split}", "validate": f"from {split}",
                  "validation_running_hours": round(val_hours, 3)},
        "grid": grid, "starts_per_fault": starts_per_fault,
        "objective": ("fewest unlabelled review episodes per running hour on the June validation "
                      "part, then highest mean synthetic detection rate, then shortest median "
                      "delay, then larger k and N"),
        "selected": best,
    }
    return frozen, table


def _score_pump(loader, asset: str, cfg: dict) -> tuple[dict, tuple | None]:
    """Fit on June, score October once. Returns the report entry and, if scored,
    (October day, baseline, frame, prepared October) for the synthetic track."""
    fit = prepare(loader(asset, dt.date(2024, 6, 11)), cfg)
    base = fit_baseline(fit, cfg)
    entry = {"asset_id": asset, "fit_day": "2024-06-11", "score_day": "2024-10-30",
             "fit_running_hours": round(base.running_s / 3600, 3),
             "model_id": base.model_id, "model_version": base.model_version,
             "normalization": base.normalization, "abstained": base.abstained,
             "bands": base.bands, "signal_abstentions": base.signal_abstentions}
    if base.abstained is not None:
        return entry, None
    oct_day = loader(asset, dt.date(2024, 10, 30))
    prep = prepare(oct_day, cfg)
    frame = score_frame(prep, base, cfg)
    hours = running_hours(prep)
    eps = review_episodes(frame)
    entry.update({
        "score_running_hours": round(hours, 3),
        "windows": len(frame),
        "states": {str(k): int(v) for k, v in frame.state.value_counts().items()},
        "states_by_signal": {sig: {str(k): int(v) for k, v in g.state.value_counts().items()}
                             for sig, g in frame.groupby("signal_name")},
        "review_episodes": [{**e, "start": str(e["start"]), "end": str(e["end"])} for e in eps],
        "unlabelled_reviews_per_running_hour": (round(len(eps) / hours, 4) if hours
                                                else None),
        "scored_evidence": len(to_scored_evidence(frame, base, cfg)),
        "diagnostics": _diagnostics(fit, prep),
    })
    return entry, (oct_day, base, frame, prep)


def evaluate(loader, cfg: dict, starts_per_fault: int = 5,
             exploratory_min_fit_s: float | None = 3600) -> dict:
    """Fit on June, score October once (real), then synthetic injections on B October.

    A pump that abstains under the protocol's minimum fit also gets an exploratory run at
    exploratory_min_fit_s, reported separately and labelled exploratory.
    """
    out = {"pumps": {}, "synthetic": None}
    for pump in ("B", "A"):
        entry, scored = _score_pump(loader, f"cira-pump-{pump}", cfg)
        if entry["abstained"] and exploratory_min_fit_s:
            exp_cfg = merge_config(cfg, {"baseline": {"min_fit_running_s": exploratory_min_fit_s}})
            exp, _ = _score_pump(loader, f"cira-pump-{pump}", exp_cfg)
            entry["exploratory"] = {**exp, "min_fit_running_s": exploratory_min_fit_s}
        if pump == "B" and scored:
            oct_day, base, frame, prep = scored
            results = run_injections(oct_day, base, cfg, starts_per_fault, frame)
            out["synthetic"] = {
                "day": "cira-pump-B 2024-10-30", "provenance": SYNTHETIC,
                "starts_per_fault": starts_per_fault, "cells": summarize_detections(results),
                "by_signal": {sig: summarize_detections([r for r in results
                                                         if r["signal"] == sig])
                              for sig in cfg["injection"]["signals"]},
                "injections": len(results),
                "clean_normal_share": {
                    sig: float((frame[frame.signal_name == scored_name(sig, prep)].state
                                == P.NORMAL).mean())
                    for sig in cfg["injection"]["signals"]}}
        out["pumps"][pump] = entry
    return out


def _diagnostics(fit: Prepared, score: Prepared) -> dict:
    """Post-hoc, per signal: typical reading interval and median level, fit day vs score day."""
    out = {}
    for sig in fit.signals:
        a = fit.readings[fit.readings.signal_name == sig]
        b = score.readings[score.readings.signal_name == sig]
        out[sig] = {
            "fit_interval_s": float(a.held_s.median()) if len(a) else None,
            "score_interval_s": float(b.held_s.median()) if len(b) else None,
            "fit_median": float(a.value.median()) if len(a) else None,
            "score_median": float(b.value.median()) if len(b) else None,
        }
    return out
