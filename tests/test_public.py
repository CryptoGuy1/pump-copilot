"""What the repository makes public: no committed report carries a home directory, and the
audits record paths relative to the project root."""

import re
import subprocess
from pathlib import Path

from pumpcopilot import zema
from pumpcopilot.provenance import PROJECT_ROOT, project_path

ROOT = Path(__file__).resolve().parents[1]
HOME = re.compile(rb"/Users/|/home/")


def _committed(prefix: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", prefix],
                         capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def test_no_committed_report_contains_a_home_path():
    files = _committed("reports")
    assert "reports/cira_audit.json" in files and "reports/zema_audit.json" in files
    hits = [f for f in files if (ROOT / f).is_file() and HOME.search((ROOT / f).read_bytes())]
    assert hits == []


def test_paths_are_recorded_relative_to_the_project_root(tmp_path):
    assert PROJECT_ROOT == ROOT
    assert project_path(ROOT / "data" / "raw" / "zema") == "data/raw/zema"
    assert project_path(tmp_path / "elsewhere") == "elsewhere"  # outside: the name only


def test_the_zema_audit_records_no_absolute_path(zema_dir):
    r = zema.audit(zema_dir, expected_cycles=30)
    assert not Path(r["root"]).is_absolute() and not HOME.search(r["root"].encode())
