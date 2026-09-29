"""API keys stay out of Git: .env is ignored and never tracked, the example has no value, and
no tracked file contains an Anthropic key. The API (and so `make dev`) loads .env; nothing else
does, and the end-to-end backend never does."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEY_PREFIX = "sk-" + "ant-"  # built at runtime so this file does not match itself


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def test_no_tracked_file_contains_an_anthropic_key():
    # the index holds every tracked file as it will be committed (staged changes included)
    r = _git("grep", "--cached", "-I", "-l", "-F", "-e", KEY_PREFIX)
    assert r.returncode in (0, 1), r.stderr
    assert r.stdout.strip() == "", f"tracked files contain {KEY_PREFIX!r}: {r.stdout.split()}"


def test_dot_env_is_ignored_and_not_tracked():
    assert _git("check-ignore", "-q", ".env").returncode == 0, ".env is not in .gitignore"
    assert _git("ls-files", "--error-unmatch", ".env").returncode != 0, ".env is tracked"


def test_env_example_names_the_key_without_a_value():
    assert (ROOT / ".env.example").read_text() == "ANTHROPIC_API_KEY=\n"
    assert _git("check-ignore", "-q", ".env.example").returncode != 0  # it is committed


def test_claude_settings_deny_reading_or_editing_dot_env():
    deny = json.loads((ROOT / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    assert {"Read(./.env)", "Edit(./.env)"} <= set(deny)


def test_the_api_command_loads_dot_env_without_overriding(monkeypatch, tmp_path):
    from pumpcopilot import cli

    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-the-file\nPUMPCOPILOT_TEST_ONLY=1\n")
    monkeypatch.setattr(cli, "ENV_FILE", env)
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: None)
    for name in ("ANTHROPIC_API_KEY", "PUMPCOPILOT_TEST_ONLY"):
        monkeypatch.setenv(name, "x")  # so monkeypatch restores the original afterwards
        monkeypatch.delenv(name)
    cli.main(["api", "--no-dotenv"])
    assert "ANTHROPIC_API_KEY" not in os.environ
    cli.main(["api"])
    assert os.environ["ANTHROPIC_API_KEY"] == "from-the-file"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "already-set")
    cli.main(["api"])
    assert os.environ["ANTHROPIC_API_KEY"] == "already-set"  # the environment wins


def test_a_missing_dot_env_is_fine(monkeypatch, tmp_path):
    from pumpcopilot import cli

    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: None)
    cli.main(["api"])


@pytest.mark.parametrize("script,expect", [
    ("web/e2e/backend.sh", "pumpcopilot api --port 8001 --no-dotenv"),
    ("scripts/dev.sh", "pumpcopilot api &"),
])
def test_scripts_load_dot_env_only_through_the_api(script, expect):
    text = (ROOT / script).read_text()
    assert expect in text
    assert "source .env" not in text and ". ./.env" not in text and "dotenv" not in text.replace(
        "--no-dotenv", "")
