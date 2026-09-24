"""ZeMA hydraulic test rig: loading, audit, and split-feasibility preflight.

Answers the Phase 2 questions before any modelling:
  * Does every channel have the expected cycles x samples?
  * Are labels within their documented value sets?
  * How is pump_leakage distributed along cycle order? (decides whether an ordered
    train/val/test split with buffer gaps can retain all three classes)
  * How entangled is pump_leakage with the other four conditions? (confounding)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .provenance import file_digest, record_hash
from .schema import ChannelMeta, CycleLabels, CycleRecord

EXPECTED_CYCLES = 2205
CYCLE_SECONDS = 60

# name -> (Hz, unit, virtual)
CHANNELS: dict[str, tuple[float, str, bool]] = {
    **{f"PS{i}": (100, "bar", False) for i in range(1, 7)},
    "EPS1": (100, "W", False),
    "FS1": (10, "l/min", False),
    "FS2": (10, "l/min", False),
    **{f"TS{i}": (1, "degC", False) for i in range(1, 5)},
    "VS1": (1, "mm/s", False),
    "CE": (1, "%", True),
    "CP": (1, "kW", True),
    "SE": (1, "%", True),
}
MEASURED = [c for c, (_, _, v) in CHANNELS.items() if not v]
LABEL_COLUMNS = ["cooler_pct", "valve_pct", "pump_leakage", "accumulator_bar", "stable_flag"]
LABEL_DOMAINS = {
    "cooler_pct": {3, 20, 100},
    "valve_pct": {100, 90, 80, 73},
    "pump_leakage": {0, 1, 2},
    "accumulator_bar": {130, 115, 100, 90},
    "stable_flag": {0, 1},
}


def find_root(raw_dir: Path) -> Path:
    """UCI archives are sometimes nested; locate the folder containing profile.txt."""
    hits = sorted(raw_dir.rglob("profile.txt"))
    if not hits:
        raise FileNotFoundError(f"profile.txt not found under {raw_dir}")
    return hits[0].parent


def load_channel(root: Path, name: str) -> np.ndarray:
    return pd.read_csv(root / f"{name}.txt", sep="\t", header=None).to_numpy(dtype=float)


def load_labels(root: Path) -> pd.DataFrame:
    df = pd.read_csv(root / "profile.txt", sep="\t", header=None, names=LABEL_COLUMNS)
    df.index.name = "cycle_id"
    return df


def run_lengths(series: pd.Series) -> list[dict]:
    """Contiguous runs of equal value in cycle order."""
    values = series.to_numpy()
    runs, start = [], 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[start]:
            runs.append({"value": int(values[start]), "start": start, "length": i - start})
            start = i
    return runs


def ordered_split_feasibility(
    labels: pd.Series,
    fractions: tuple[float, float, float] = (0.6, 0.2, 0.2),
    gap: int = 20,
) -> dict:
    """Class counts for an ordered train/val/test split with `gap` cycles discarded between."""
    n = len(labels)
    a = int(n * fractions[0])
    b = int(n * (fractions[0] + fractions[1]))
    parts = {
        "train": labels.iloc[:a],
        "val": labels.iloc[a + gap : b],
        "test": labels.iloc[b + gap :],
    }
    classes = sorted(labels.unique())
    counts = {k: {int(c): int((v == c).sum()) for c in classes} for k, v in parts.items()}
    feasible = all(all(cnt > 0 for cnt in counts[k].values()) for k in counts)
    return {
        "fractions": fractions,
        "gap": gap,
        "counts": counts,
        "all_classes_everywhere": feasible,
    }


def audit(raw_dir: Path, expected_cycles: int = EXPECTED_CYCLES) -> dict:
    root = find_root(raw_dir)
    issues: list[str] = []
    channels: dict[str, dict] = {}

    for name, (hz, unit, virtual) in CHANNELS.items():
        path = root / f"{name}.txt"
        if not path.exists():
            issues.append(f"missing channel file {name}.txt")
            continue
        arr = load_channel(root, name)
        expected_samples = int(hz * CYCLE_SECONDS)
        if arr.shape != (expected_cycles, expected_samples):
            issues.append(f"{name}: shape {arr.shape} != ({expected_cycles}, {expected_samples})")
        channels[name] = {
            "shape": list(arr.shape),
            "unit": unit,
            "virtual": virtual,
            "nan": int(np.isnan(arr).sum()),
            "min": float(np.nanmin(arr)),
            "max": float(np.nanmax(arr)),
            "sha256": file_digest(path),
        }

    labels = load_labels(root)
    if len(labels) != expected_cycles:
        issues.append(f"profile.txt has {len(labels)} rows, expected {expected_cycles}")
    for col, domain in LABEL_DOMAINS.items():
        bad = set(labels[col].unique()) - domain
        if bad:
            issues.append(f"{col}: unexpected values {sorted(bad)}")

    leak = labels["pump_leakage"]
    confounding = {
        col: pd.crosstab(labels[col], leak).to_dict()
        for col in LABEL_COLUMNS
        if col != "pump_leakage"
    }
    runs = run_lengths(leak)
    return {
        "root": str(root),
        "issues": issues,
        "ok": not issues,
        "channels": channels,
        "class_counts": {int(k): int(v) for k, v in leak.value_counts().sort_index().items()},
        "leakage_runs": {
            "n_runs": len(runs),
            "median_length": float(np.median([r["length"] for r in runs])),
            "first_10": runs[:10],
        },
        "confounding_crosstabs": confounding,
        "unstable_cycles": int((labels["stable_flag"] == 1).sum()),
        "split_preflight": [
            ordered_split_feasibility(leak, gap=g) for g in (0, 20, 50)
        ],
    }


def cycle_records(raw_dir: Path) -> list[CycleRecord]:
    root = find_root(raw_dir)
    labels = load_labels(root)
    meta = {
        n: ChannelMeta(sample_hz=hz, n_samples=int(hz * CYCLE_SECONDS), unit=u, virtual=v)
        for n, (hz, u, v) in CHANNELS.items()
    }
    out = []
    for cid, row in labels.iterrows():
        payload = {k: int(row[k]) for k in LABEL_COLUMNS}
        out.append(
            CycleRecord(
                cycle_id=int(cid),
                channels=meta,
                labels=CycleLabels(**payload),
                provenance_hash=record_hash("zema", "profile.txt", int(cid), payload),
            )
        )
    return out
