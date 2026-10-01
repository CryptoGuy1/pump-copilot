"""Synthetic fixtures shaped like the real sources. They test our code, not the data."""

import contextlib
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pumpcopilot import zema

N_CYCLES = 30


# --- every file the tests read must be tracked by Git ----------------------------------------
# A test that reads a file only this machine has passes here and fails in CI. An audit hook
# records every file inside the repository opened for reading during the run; at the end the
# run fails if Git does not track one of them. Local by design: the raw data (only the real-data
# CI job downloads it) and tool caches.
REPO = Path(__file__).resolve().parents[1]
LOCAL_BY_DESIGN = (".venv/", ".git/", ".pytest_cache/", ".ruff_cache/", "web/node_modules/",
                   "data/raw/", "data/cache/", "src/pumpcopilot.egg-info/")
_recorders: list[set] = []
READ: set = set()
_recorders.append(READ)


def _audit(event, args):
    if event != "open" or not _recorders:
        return
    path, mode, flags = args
    if path is None or isinstance(path, int):
        return
    if isinstance(mode, str):
        if any(c in mode for c in "wax+"):
            return
    elif isinstance(flags, int) and flags & os.O_ACCMODE != os.O_RDONLY:
        return
    try:
        p = Path(os.fsdecode(path))
        p = p if p.is_absolute() else Path.cwd() / p
        rel = os.path.relpath(os.path.normpath(p), REPO)
    except Exception:  # noqa: BLE001 (an audit hook must never raise)
        return
    if not rel.startswith("..") and "__pycache__" not in rel:
        for r in _recorders:
            r.add(rel.replace(os.sep, "/"))


sys.addaudithook(_audit)


def tracked_files() -> set[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True,
                         check=True).stdout.decode()
    return set(filter(None, out.split("\0")))


def untracked_reads(reads: set[str]) -> list[str]:
    tracked = tracked_files()
    return sorted(r for r in reads if r not in tracked and (REPO / r).is_file()
                  and not r.startswith(LOCAL_BY_DESIGN))


@contextlib.contextmanager
def recording_reads():
    """Every repository file opened for reading inside the block."""
    seen: set = set()
    _recorders.append(seen)
    try:
        yield seen
    finally:
        _recorders.remove(seen)


def pytest_sessionfinish(session, exitstatus):
    bad = untracked_reads(READ)
    if bad:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr:
            tr.write_line("Tests read files that Git does not track (they would be missing in "
                          "CI): " + ", ".join(bad), red=True)
        session.exitstatus = 1


# CI sets PUMPCOPILOT_NO_SKIPS=1: every test must run there, so a skip (for example a
# database that is not reachable) fails the test instead of passing quietly.
NO_SKIPS = os.environ.get("PUMPCOPILOT_NO_SKIPS") == "1"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if NO_SKIPS and report.skipped and not hasattr(report, "wasxfail"):
        report.outcome = "failed"
        report.longrepr = f"skipped with PUMPCOPILOT_NO_SKIPS=1: {report.longrepr}"


@pytest.fixture(autouse=True)
def _never_the_real_env_file(monkeypatch, tmp_path):
    """No test may load the developer's .env (it holds the real API key): the CLI's .env is a
    file that does not exist. Tests of .env handling set their own."""
    from pumpcopilot import cli

    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "no-such.env")


@pytest.fixture(autouse=True)
def _separate_request_ledger(tmp_path, monkeypatch):
    """Tests never count against the real API request ledger (reports/anthropic_requests.json)."""
    monkeypatch.setenv("PUMPCOPILOT_REQUEST_LEDGER", str(tmp_path / "anthropic_requests.json"))


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


# Header layout of the real files: 8 pump-prefixed measurements, then 2 unprefixed ambient ones.
CIRA_PUMP_HEADERS = ("ACR_Mot.PV", "ACR_Mot.SV", "ACR_Mot.TV", "ACR_Pmp.PV", "ACR_Pmp.SV",
                     "ACR_Pmp.TV", "Pres.PV", "Temp.PV")


def _cira_frame(
    pump: str,
    start: str | None = None,
    n: int | None = None,
    dup_at: int | None = None,
    gap_at: int | None = None,
    ts_fmt: str = "%Y-%m-%d %H:%M:%S",
    timestamps: list[pd.Timestamp] | None = None,
):
    if timestamps is None:
        ts = pd.date_range(start, periods=n, freq="10s")
        if gap_at is not None:
            ts = ts.append(pd.date_range(ts[-1] + pd.Timedelta("10min"), periods=5, freq="10s"))
        ts = list(ts)
    else:
        ts = list(timestamps)
    if dup_at is not None:
        ts[dup_at] = ts[dup_at - 1]
    rng = np.random.default_rng(len(pump))
    frame = {"Timestamp": [t.strftime(ts_fmt) for t in ts]}
    for h in CIRA_PUMP_HEADERS:
        frame[f"{pump}_{h}"] = rng.normal(20, 1, len(ts))
    # outlet pressure like the real pumps: idle about 0.5 bar, then running about 40 bar
    n_off = len(ts) // 3
    frame[f"{pump}_Pres.PV"] = np.r_[rng.normal(0.5, 0.01, n_off),
                                     rng.normal(40, 0.5, len(ts) - n_off)]
    # motor peak acceleration follows: idle about 0.47 m/s^2, running about 20 m/s^2
    frame[f"{pump}_ACR_Mot.SV"] = np.r_[rng.normal(0.47, 0.01, n_off),
                                        rng.normal(20, 1.0, len(ts) - n_off)]
    frame["Barometer"] = rng.normal(1018, 0.1, len(ts))
    frame["Temperature"] = rng.normal(20, 0.3, len(ts))
    return pd.DataFrame(frame)


@pytest.fixture
def make_cira_frame():
    return _cira_frame


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


# --- database ----------------------------------------------------------------------------

def test_database(name: str):
    """Context manager: a fresh, empty database on the DATABASE_URL server, dropped afterwards.
    Skips the test when the server is unreachable."""
    import contextlib

    psycopg = pytest.importorskip("psycopg")
    from psycopg.conninfo import make_conninfo

    from pumpcopilot import db

    @contextlib.contextmanager
    def cm():
        base = db.database_url()
        try:
            admin = psycopg.connect(base, autocommit=True, connect_timeout=2)
        except psycopg.OperationalError as e:
            pytest.skip(f"database not reachable at DATABASE_URL: {e}".splitlines()[0])
        with admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            admin.execute(f"CREATE DATABASE {name}")
        try:
            yield make_conninfo(base, dbname=name)
        finally:
            with psycopg.connect(base, autocommit=True) as admin:
                admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")

    return cm()


test_database.__test__ = False  # a helper, not a test


@pytest.fixture
def db_url():
    """A fresh, empty test database; tests using it are marked @pytest.mark.db."""
    with test_database("pumpcopilot_test") as url:
        yield url


POLICY_JOBS = ("policy_compression", "policy_refresh_continuous_aggregate")


def pause_policy_jobs(c) -> None:
    """Disable the Timescale policy jobs of a freshly migrated test database, then wait for any
    run already in progress to finish. A new policy runs once at creation; left alone it can
    race a test's own telemetry_1m refresh. Tests that cover the jobs run them explicitly."""
    import time

    c.execute("SELECT alter_job(job_id, scheduled => false) FROM timescaledb_information.jobs"
              " WHERE proc_name = ANY(%s)", [list(POLICY_JOBS)])
    deadline = time.monotonic() + 60
    while c.execute("SELECT 1 FROM pg_stat_activity WHERE datname = current_database()"
                    " AND pid <> pg_backend_pid() AND application_name LIKE '%Policy [%'"
                    ).fetchone():
        if time.monotonic() > deadline:
            raise RuntimeError("a Timescale policy job is still running after 60 s")
        time.sleep(0.05)


@pytest.fixture
def pause_jobs():
    return pause_policy_jobs


@pytest.fixture
def conn(db_url):
    from pumpcopilot import db

    with db.connect(db_url) as c:
        db.migrate(c)
        pause_policy_jobs(c)
        yield c


@pytest.fixture
def loaded(conn, cira_dir, tmp_path):
    """The synthetic CIRA fixture loaded into the test database."""
    import yaml

    from pumpcopilot import db, operating

    column_map = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "data" / "cira_columns.yaml").read_text())
    rules, _ = operating.derive_rules(cira_dir)
    rules_path = tmp_path / "operating_rules.yaml"
    operating.write_rules(rules, rules_path)
    runs = db.load_cira(conn, cira_dir, column_map, rules_path)
    return conn, cira_dir, rules, rules_path, runs
