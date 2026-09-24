"""CIRA centrifugal pump telemetry: per-file audit and canonical conversion.

The public listing documents 8 CSVs named <PUMP>_<YYYY-MM-DD>.csv with a timestamp and ten
measured columns prefixed by the pump letter. We do NOT hard-code column names or units:
the audit reports what is actually there, and conversion requires an explicit column map
(data/cira_columns.yaml) written after reading the audit and the data descriptor.
Cadence is measured, never assumed. Delimiter and decimal separator are sniffed per file:
A_2024-10-30.csv is written with ";" and decimal commas, the other files with "," and ".".
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd

from .provenance import file_digest, record_hash
from .schema import OperatingState, QualityFlag, SourceDataset, TelemetryEvent

FILENAME = re.compile(r"^(?P<pump>[A-Z])_(?P<day>\d{4}-\d{2}-\d{2})\.csv$")
KNOWN_ABSENT = {("C", "2024-10-30"): "pump off that day per dataset listing"}
PLACEHOLDERS = {-999.0, -9999.0, 9999.0, 65535.0}

TIMESTAMP_HEADER = re.compile(r"time|date", re.I)
TZ_MARKER = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
N_MEASUREMENTS = 10  # per the dataset listing: one timestamp column + ten measured values
MAX_UNPARSEABLE_SHARE = 0.01
SPAN_MARGIN = pd.Timedelta(hours=3)
MIN_SEGMENT_STEPS = 60  # shorter cadence runs are absorbed into the surrounding segment

# Data descriptor (Data 2025, 10(6), 91) sec. 3.1: "The gateway collects the sensor data and
# timestamps them in UTC with a resolution of one second." So naive timestamps default to UTC.
DEFAULT_NAIVE_TZ = "UTC"
NAIVE_UTC_NOTE = (
    "naive timestamps treated as UTC (data descriptor sec. 3.1: gateway timestamps in UTC)"
)
UTC_NOTE = "UTC marker present; README says no timezone; confirm with authors"


def parse_name(path: Path) -> tuple[str, str]:
    m = FILENAME.match(path.name)
    if not m:
        raise ValueError(f"unexpected CIRA filename: {path.name}")
    return m["pump"], m["day"]


def _timestamp_column(df: pd.DataFrame) -> str:
    for c in df.columns:
        if TIMESTAMP_HEADER.search(str(c)):
            return c
    return df.columns[0]


def sniff_format(path: Path, n_lines: int = 20) -> dict[str, str]:
    """Delimiter from the header line; decimal comma only possible with a ";" delimiter."""
    with open(path, encoding="utf-8", errors="replace") as f:
        head = [line for _, line in zip(range(n_lines), f, strict=False)]
    header = head[0] if head else ""
    delimiter = ";" if header.count(";") > header.count(",") else ","
    decimal = "."
    if delimiter == ";":
        fields = (x.strip() for line in head[1:] for x in line.split(";"))
        if any(re.fullmatch(r"-?\d+,\d+", x) for x in fields):
            decimal = ","
    return {"delimiter": delimiter, "decimal": decimal}


def _parse_timestamps(raw: pd.Series, tz: str) -> tuple[pd.Series, str]:
    """Parse to UTC. Only naive values are localized (to tz); marked ones are kept."""
    s = raw.astype("string").str.strip()
    marked = s.str.contains(TZ_MARKER, na=False)
    parts = []
    if marked.any():
        parts.append(pd.to_datetime(s[marked], errors="coerce", utc=True, format="ISO8601"))
    if (~marked).any():
        naive = pd.to_datetime(s[~marked], errors="coerce", format="ISO8601")
        naive = naive.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
        parts.append(naive.dt.tz_convert("UTC"))
    ts = pd.concat(parts).reindex(s.index) if parts else pd.Series(dtype="datetime64[ns, UTC]")

    present = s.notna()
    if present.any() and s[present].str.endswith("Z").all():
        note = UTC_NOTE
    elif not marked.any():
        note = NAIVE_UTC_NOTE if tz == "UTC" else f"naive timestamps localized to {tz} (verify)"
    else:
        note = (f"mixed: {int(marked.sum())} of {int(present.sum())} timestamps carry a UTC "
                f"marker or offset; the rest localized to {tz} (verify)")
    return ts, note


def load(path: Path, tz: str = DEFAULT_NAIVE_TZ) -> tuple[pd.DataFrame, str, dict]:
    """Returns (frame indexed by original row number, timestamp column name, read info).

    Fully blank rows are dropped and counted. Timestamps are returned in UTC; naive ones are
    localized to tz first (UTC per the descriptor). The format and timezone assumption go into
    the read info.
    """
    fmt = sniff_format(path)
    df = pd.read_csv(path, sep=fmt["delimiter"], decimal=fmt["decimal"], skip_blank_lines=False)
    blank = df.isna().all(axis=1)
    df = df.loc[~blank].copy()
    ts_col = _timestamp_column(df)
    df[ts_col], note = _parse_timestamps(df[ts_col], tz)
    info = {"format": fmt, "blank_rows": int(blank.sum()), "timestamp_assumption": note}
    return df, ts_col, info


def _longest_constant_run(s: pd.Series) -> int:
    v = s.to_numpy()
    if len(v) == 0:
        return 0
    change = np.r_[True, v[1:] != v[:-1]]
    idx = np.flatnonzero(change)
    return int(np.diff(np.r_[idx, len(v)]).max())


def _cadence_segments(
    valid: pd.Series, min_steps: int = MIN_SEGMENT_STEPS
) -> tuple[list[dict], np.ndarray, np.ndarray]:
    """Split a file into runs of constant sampling interval.

    Runs shorter than min_steps (single gaps, short bursts) are absorbed into the segment
    before them, so April's 5 s then 1 s logging shows up as two segments, not hundreds.
    Returns (segments, step seconds, cadence of the segment each step belongs to).
    """
    steps = valid.diff().dt.total_seconds().to_numpy()[1:]
    if len(steps) == 0:
        return [], steps, steps
    rounded = np.round(steps)
    starts = np.flatnonzero(np.r_[True, rounded[1:] != rounded[:-1]])
    ends = np.r_[starts[1:], len(rounded)]
    anchors = [(s, rounded[s]) for s, e in zip(starts, ends, strict=True) if e - s >= min_steps]
    if not anchors:
        anchors = [(0, float(np.median(steps)))]
    bounds = []  # (first step, cadence), consecutive equal cadences merged
    for s, cadence in anchors:
        if not bounds:
            bounds.append((0, cadence))
        elif cadence != bounds[-1][1]:
            bounds.append((s, cadence))
    local = np.empty(len(steps))
    segments = []
    for k, (s, cadence) in enumerate(bounds):
        e = bounds[k + 1][0] if k + 1 < len(bounds) else len(steps)
        local[s:e] = cadence
        segments.append({"start": str(valid.iloc[s]), "end": str(valid.iloc[e]),
                         "cadence_s": float(cadence), "steps": int(e - s)})
    return segments, steps, local


def _file_issues(df: pd.DataFrame, ts_col: str, day: str, n_unparseable: int,
                 valid: pd.Series) -> list[str]:
    issues = []
    n_ts = sum(bool(TIMESTAMP_HEADER.search(str(c))) for c in df.columns)
    n_meas = len(df.columns) - 1
    if n_ts != 1 or n_meas != N_MEASUREMENTS:
        issues.append(f"expected 1 timestamp + {N_MEASUREMENTS} measurement columns, "
                      f"found {n_ts} + {n_meas}")
    if len(df) and n_unparseable / len(df) > MAX_UNPARSEABLE_SHARE:
        issues.append(f"{n_unparseable} of {len(df)} timestamps unparseable "
                      f"({n_unparseable / len(df):.1%})")
    lo = pd.Timestamp(day, tz="UTC") - SPAN_MARGIN
    hi = pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1) + SPAN_MARGIN
    if valid.empty:
        issues.append("span: no parseable timestamps")
    elif valid.min() < lo or valid.max() > hi:
        issues.append(f"span {valid.min()} .. {valid.max()} is not on {day} "
                      f"(+/- {SPAN_MARGIN})")
    return issues


def audit_file(path: Path, gap_factor: float = 5.0) -> dict:
    pump, day = parse_name(path)
    df, ts_col, info = load(path)
    ts = df[ts_col]
    valid = ts.dropna()
    segments, steps, local = _cadence_segments(valid)
    big = np.flatnonzero(steps > gap_factor * local)
    gaps = [{"after": str(valid.iloc[i]), "seconds": float(steps[i])} for i in big[:50]]
    n_unparseable = int(ts.isna().sum())

    columns = {}
    for c in df.columns:
        if c == ts_col:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        nonnull = s.dropna()
        top_share = float(nonnull.value_counts(normalize=True).iloc[0]) if len(nonnull) else None
        columns[str(c)] = {
            "non_numeric": int(s.isna().sum() - df[c].isna().sum()),
            "missing": int(df[c].isna().sum()),
            "min": float(nonnull.min()) if len(nonnull) else None,
            "p50": float(nonnull.median()) if len(nonnull) else None,
            "max": float(nonnull.max()) if len(nonnull) else None,
            "most_common_value_share": top_share,
            "longest_constant_run": _longest_constant_run(nonnull),
            "placeholder_hits": int(nonnull.isin(PLACEHOLDERS).sum()),
        }

    return {
        "file": path.name,
        "pump": pump,
        "day": day,
        "sha256": file_digest(path),
        "format": info["format"],
        "rows": len(df),
        "blank_rows": info["blank_rows"],
        "timestamp_column": ts_col,
        "timestamp_assumption": info["timestamp_assumption"],
        "unparseable_timestamps": n_unparseable,
        "duplicate_timestamps": int(valid.duplicated().sum()),
        "non_monotonic_steps": int((steps < 0).sum()),
        "span": [str(valid.min()), str(valid.max())] if len(valid) else None,
        "cadence_segments": segments,
        "gaps_over_factor": {"factor": gap_factor, "count": len(gaps), "site_level": 0,
                             "items": gaps},
        "columns": columns,
        "issues": _file_issues(df, ts_col, day, n_unparseable, valid),
    }


def _site_gaps(reports: list[dict]) -> list[dict]:
    """A gap every pump shows on the same day is a site/logger outage: record it once.

    Moves those gaps out of the per-file lists, leaving a count under "site_level".
    """
    by_day = defaultdict(list)
    for r in reports:
        by_day[r["day"]].append(r)
    site = []
    for day, rs in sorted(by_day.items()):
        if len(rs) < 2:
            continue
        keys = [{(g["after"], g["seconds"]) for g in r["gaps_over_factor"]["items"]} for r in rs]
        shared = set.intersection(*keys)
        for after, seconds in sorted(shared):
            site.append({"day": day, "after": after, "seconds": seconds,
                         "pumps": sorted(r["pump"] for r in rs)})
        for r in rs:
            g = r["gaps_over_factor"]
            g["items"] = [x for x in g["items"] if (x["after"], x["seconds"]) not in shared]
            g["site_level"] = len(shared)
            g["count"] -= len(shared)
    return site


def audit(raw_dir: Path) -> dict:
    files = sorted(p for p in raw_dir.rglob("*.csv") if FILENAME.match(p.name))
    reports = [audit_file(p) for p in files]
    site_gaps = _site_gaps(reports)
    seen = {(r["pump"], r["day"]) for r in reports}
    pumps = sorted({p for p, _ in seen})
    days = sorted({d for _, d in seen})
    missing = []
    for p in pumps:
        for d in days:
            if (p, d) not in seen:
                missing.append(
                    {"pump": p, "day": d, "explained": KNOWN_ABSENT.get((p, d), "UNEXPLAINED")}
                )
    issues = [f"{m['pump']} {m['day']} absent, unexplained" for m in missing
              if m["explained"] == "UNEXPLAINED"]
    if len(files) != 8:
        issues.append(f"found {len(files)} CSVs, dataset listing says 8")
    issues += [f"{r['file']}: {i}" for r in reports for i in r["issues"]]
    headers = {r["file"]: list(r["columns"]) for r in reports}
    return {
        "ok": not issues,
        "issues": issues,
        "pumps": pumps,
        "days": days,
        "absent_asset_days": missing,
        "site_gaps": site_gaps,
        "headers_by_file": headers,
        "files": reports,
    }


def to_events(
    path: Path, column_map: dict[str, dict], asset_prefix: str = "cira-pump-"
) -> Iterator[TelemetryEvent]:
    """Emit canonical events. column_map: {original_header_without_pump_prefix: {signal, unit}}.

    Unmapped measurement columns are an error: we never silently drop or guess a signal.
    """
    pump, day = parse_name(path)
    df, ts_col, _ = load(path)
    strip = re.compile(rf"^{pump}_")  # "Barometer" must not lose its B for pump B
    rename = {c: strip.sub("", str(c)) for c in df.columns if c != ts_col}
    unmapped = sorted(set(rename.values()) - set(column_map))
    if unmapped:
        raise KeyError(f"{path.name}: unmapped columns {unmapped}; add them to cira_columns.yaml")

    seen_ts: set = set()
    prev = None
    for row_idx, row in df.iterrows():
        ts = row[ts_col]
        if pd.isna(ts):
            continue
        base_flags: list[QualityFlag] = []
        if ts in seen_ts:
            base_flags.append(QualityFlag.DUPLICATE_TIMESTAMP)
        if prev is not None and ts < prev:
            base_flags.append(QualityFlag.NON_MONOTONIC)
        seen_ts.add(ts)
        prev = ts if prev is None or ts > prev else prev
        for orig, short in rename.items():
            spec = column_map[short]
            raw = pd.to_numeric(row[orig], errors="coerce")
            value = None if pd.isna(raw) else float(raw)
            flags = list(base_flags)
            if value is None:
                flags.append(QualityFlag.MISSING)
            elif value in PLACEHOLDERS:
                flags.append(QualityFlag.PLACEHOLDER_SUSPECTED)
            if spec.get("unit_verified") is not True:
                flags.append(QualityFlag.UNIT_UNVERIFIED)
            yield TelemetryEvent(
                asset_id=f"{asset_prefix}{pump}",
                source_dataset=SourceDataset.CIRA,
                source_file=path.name,
                source_day=day,
                observed_at=ts.to_pydatetime(),
                sample_id=f"{path.stem}:{row_idx}",
                signal_name=spec["signal"],
                value=value,
                unit=spec["unit"],
                operating_state=OperatingState.UNKNOWN,  # set by explicit rules in step 2
                quality_flags=tuple(flags),
                provenance_hash=record_hash("cira", path.name, f"{row_idx}:{orig}", value),
            )
