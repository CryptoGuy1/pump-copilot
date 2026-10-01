"""`make dev` checks that its ports are free before it starts anything."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dev_checks_its_ports_before_starting_anything():
    s = (ROOT / "scripts" / "dev.sh").read_text()
    check = s.index("for port in 8000 5173")
    assert check < s.index("docker compose up") < s.index("pumpcopilot worker &")
    assert "lsof -nP -iTCP" in s and "kill $pid" in s and "exit 1" in s[check:]
