"""Synthetic fixtures shaped like the real sources. They test our code, not the data."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pumpcopilot import zema

N_CYCLES = 30


@pytest.fixture
def zema_dir(tmp_path: Path) -> Path:
    root = tmp_path / "zema" / "extracted"
    root.mkdir(parents=True)
    rng = np.random.default_rng(0)
    for name, (hz, _, _) in zema.CHANNELS.items():
        arr = rng.normal(size=(N_CYCLES, int(hz * 60)))
        np.savetxt(root / f"{name}.txt", arr, delimiter="\t", fmt="%.4f")
    # leakage laid out in contiguous blocks, like a staged test campaign
    leak = [0] * 12 + [1] * 9 + [2] * 9
    prof = pd.DataFrame({
        "cooler": [100] * N_CYCLES,
        "valve": ([100, 90, 80, 73] * 8)[:N_CYCLES],
        "leak": leak,
        "acc": [130] * N_CYCLES,
        "stable": [0] * 25 + [1] * 5,
    })
    prof.to_csv(root / "profile.txt", sep="\t", header=False, index=False)
    return tmp_path / "zema"


def _cira_frame(
    pump: str, start: str, n: int, dup_at: int | None = None, gap_at: int | None = None
):
    ts = pd.date_range(start, periods=n, freq="10s")
    if gap_at is not None:
        ts = ts.append(pd.date_range(ts[-1] + pd.Timedelta("10min"), periods=5, freq="10s"))
    ts = list(ts)
    if dup_at is not None:
        ts[dup_at] = ts[dup_at - 1]
    rng = np.random.default_rng(len(pump))
    return pd.DataFrame({
        "Timestamp": [t.strftime("%Y-%m-%d %H:%M:%S") for t in ts],
        f"{pump}_vib_pump": rng.normal(2, 0.1, len(ts)),
        f"{pump}_temp_casing": rng.normal(40, 1, len(ts)),
        f"{pump}_pressure_out": rng.normal(12, 0.2, len(ts)),
    })


@pytest.fixture
def cira_dir(tmp_path: Path) -> Path:
    root = tmp_path / "cira"
    root.mkdir()
    for day in ["2024-04-10", "2024-06-12", "2024-10-30"]:
        for pump in "ABC":
            if pump == "C" and day == "2024-10-30":
                continue
            df = _cira_frame(pump, f"{day} 08:00:00", 40,
                             dup_at=5 if pump == "A" else None,
                             gap_at=1 if pump == "B" else None)
            df.to_csv(root / f"{pump}_{day}.csv", index=False)
    return root
