"""Shared by the replay and API tests: a synthetic B_2024-06-11-shaped day, loading it, and a
simulated wall clock for stepping replay workers."""
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from pumpcopilot import db, operating, replay

ROOT = Path(__file__).resolve().parents[1]
COLUMN_MAP = yaml.safe_load((ROOT / "data" / "cira_columns.yaml").read_text())
DAY = dt.date(2024, 6, 11)
ASSET = "cira-pump-B"
START = pd.Timestamp("2024-06-11 07:00:00", tz="UTC")
WALL0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _held(rng, n, every, fn):
    """1 Hz rows whose value changes every `every` seconds (sample-and-hold, A5)."""
    return np.repeat(fn(np.arange(0, n, every), rng), every)[:n]


def replay_csv(path: Path, hours=3.0, run=(20, 170), bump_min=120):
    """B_2024-06-11-shaped day: idle, a 2.5 h run with a pressure shift after 2 h, idle."""
    rng = np.random.default_rng(3)
    n = int(hours * 3600)
    ts = START + pd.to_timedelta(np.arange(n), unit="s")
    on = lambda s: (s >= run[0] * 60) & (s < run[1] * 60)  # noqa: E731
    cols = {
        "Pres.PV": lambda s, r: np.where(on(s), 41 + 0.8 * (s >= bump_min * 60), 0.5)
        + r.normal(0, 0.05, len(s)),
        "ACR_Mot.SV": lambda s, r: np.where(on(s), 20.0, 0.47) + r.normal(0, 0.01, len(s)),
        "ACR_Mot.PV": lambda s, r: r.normal(0.002, 0.00005, len(s)),
        "ACR_Mot.TV": lambda s, r: r.normal(30, 0.05, len(s)),
        "ACR_Pmp.PV": lambda s, r: r.normal(0.004, 0.0001, len(s)),
        "ACR_Pmp.SV": lambda s, r: r.normal(25, 0.5, len(s)),
        "ACR_Pmp.TV": lambda s, r: r.normal(30, 0.05, len(s)),
        "Temp.PV": lambda s, r: r.normal(35, 0.05, len(s)),
    }
    frame = {"Timestamp": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in ts]}
    for h, fn in cols.items():
        frame[f"B_{h}"] = _held(rng, n, 10, fn)
    frame["Barometer"] = _held(rng, n, 10, lambda s, r: r.normal(1018, 0.1, len(s)))
    frame["Temperature"] = _held(rng, n, 10, lambda s, r: r.normal(20, 0.02, len(s)))
    pd.DataFrame(frame).to_csv(path, index=False)


class Clock:
    def __init__(self, t=WALL0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += dt.timedelta(seconds=s)


def run_to_end(conn, sid, clock, worker=None, max_ticks=100_000, stop_at=None, wall_s=10.0):
    """Tick a worker, advancing the simulated wall clock wall_s per tick (at 60x, 1 s of
    wall time is one minute of source time)."""
    w = worker or replay.Worker(conn, "w1", clock=clock)
    for _ in range(max_ticks):
        st = replay.get_session(conn, sid)
        if st["status"] == "completed" or (stop_at and st["cursor_at"] and
                                           st["cursor_at"] >= stop_at):
            return w
        w.tick()
        clock.advance(wall_s)
    raise AssertionError("session did not complete")


def load_replay_day(conn, tmp_path: Path) -> dict:
    """Write, derive rules for and load the replay day; returns the stale limits."""
    raw = tmp_path / "replay_raw"
    raw.mkdir(exist_ok=True)
    replay_csv(raw / "B_2024-06-11.csv")
    rules, _ = operating.derive_rules(raw)
    rules_path = tmp_path / "operating_rules.yaml"
    operating.write_rules(rules, rules_path)
    db.load_cira(conn, raw, COLUMN_MAP, rules_path)
    return replay.stale_limits(rules, COLUMN_MAP)
