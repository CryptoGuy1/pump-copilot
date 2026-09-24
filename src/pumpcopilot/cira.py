"""CIRA centrifugal pump telemetry: per-file audit and canonical conversion.

The public listing documents 8 CSVs named <PUMP>_<YYYY-MM-DD>.csv with a timestamp and ten
measured columns prefixed by the pump letter. We do NOT hard-code column names or units:
the audit reports what is actually there, and conversion requires an explicit column map
(data/cira_columns.yaml) written after reading the audit and the data descriptor.
Cadence is measured, never assumed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd

from .provenance import file_digest, record_hash
from .schema import OperatingState, QualityFlag, SourceDataset, TelemetryEvent

FILENAME = re.compile(r"^(?P<pump>[A-Z])_(?P<day>\d{4}-\d{2}-\d{2})\.csv$")
KNOWN_ABSENT = {("C", "2024-10-30"): "pump off that day per dataset listing"}
PLACEHOLDERS = {-999.0, -9999.0, 9999.0, 65535.0}


def parse_name(path: Path) -> tuple[str, str]:
    m = FILENAME.match(path.name)
    if not m:
        raise ValueError(f"unexpected CIRA filename: {path.name}")
    return m["pump"], m["day"]


def _timestamp_column(df: pd.DataFrame) -> str:
    for c in df.columns:
        if re.search(r"time|date", str(c), re.I):
            return c
    return df.columns[0]


def load(path: Path, tz: str = "Europe/Rome") -> tuple[pd.DataFrame, str]:
    """Returns (frame indexed by original row number, timestamp column name).

    Timestamps are localized to plant time if naive; the assumption is recorded in the audit.
    """
    df = pd.read_csv(path)
    ts_col = _timestamp_column(df)
    ts = pd.to_datetime(df[ts_col], errors="coerce")
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    df[ts_col] = ts
    return df, ts_col


def _longest_constant_run(s: pd.Series) -> int:
    v = s.to_numpy()
    if len(v) == 0:
        return 0
    change = np.r_[True, v[1:] != v[:-1]]
    idx = np.flatnonzero(change)
    return int(np.diff(np.r_[idx, len(v)]).max())


def audit_file(path: Path, gap_factor: float = 5.0) -> dict:
    pump, day = parse_name(path)
    df, ts_col = load(path)
    ts = df[ts_col]
    valid = ts.dropna()
    dt = valid.diff().dt.total_seconds().dropna()
    median_dt = float(dt.median()) if len(dt) else None
    gaps = []
    if median_dt:
        big = dt[dt > gap_factor * median_dt]
        gaps = [
            {"after": str(valid.loc[i - 1]) if i - 1 in valid.index else None, "seconds": float(s)}
            for i, s in big.items()
        ][:50]

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
        "rows": len(df),
        "timestamp_column": ts_col,
        "timestamp_assumption": "naive timestamps localized to Europe/Rome (verify)",
        "unparseable_timestamps": int(ts.isna().sum()),
        "duplicate_timestamps": int(valid.duplicated().sum()),
        "non_monotonic_steps": int((dt < 0).sum()),
        "span": [str(valid.min()), str(valid.max())] if len(valid) else None,
        "cadence_seconds": {
            "median": median_dt,
            "p95": float(dt.quantile(0.95)) if len(dt) else None,
            "max": float(dt.max()) if len(dt) else None,
        },
        "gaps_over_factor": {"factor": gap_factor, "count": len(gaps), "first": gaps[:10]},
        "columns": columns,
    }


def audit(raw_dir: Path) -> dict:
    files = sorted(p for p in raw_dir.rglob("*.csv") if FILENAME.match(p.name))
    reports = [audit_file(p) for p in files]
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
    headers = {r["file"]: list(r["columns"]) for r in reports}
    return {
        "ok": not issues,
        "issues": issues,
        "pumps": pumps,
        "days": days,
        "absent_asset_days": missing,
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
    df, ts_col = load(path)
    strip = re.compile(rf"^{pump}[_\s-]*")
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
