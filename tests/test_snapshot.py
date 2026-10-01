"""Step 7b: the static snapshot. File names agree with the web app, every exported file is the
response its endpoint's model describes, and answers are recorded within a hard cap."""

import json
import re
from pathlib import Path

import pytest
from conftest import pause_policy_jobs, test_database
from replay_helpers import ASSET, DAY, Clock, load_replay_day, run_to_end

from pumpcopilot import api, assistant, db, replay, snapshot

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "web" / "public" / "snapshot"


def _routes():
    app = api.create_app(database_url="postgresql://unused@localhost/unused")
    return [r for r in app.routes if "GET" in getattr(r, "methods", ())
            and getattr(r, "response_model", None) is not None]


def _validate_tree(out: Path) -> int:
    """Every file under out/api/ against its route's response model; the answers and the
    manifest against theirs. Returns the number of API files."""
    routes = _routes()
    n = 0
    for f in sorted((out / "api").rglob("*.json")):
        rel = f.relative_to(out).as_posix()[: -len(".json")]
        path = "/" + rel.split("@", 1)[0]
        route = next((r for r in routes if r.path_regex.match(path)), None)
        assert route is not None, f"{rel}: no GET endpoint"
        route.response_model.model_validate(json.loads(f.read_text()))
        n += 1
    m = snapshot.SnapshotManifest.model_validate_json((out / "manifest.json").read_text())
    if (out / "answers.json").exists():
        rec = snapshot.RecordedAnswers.model_validate_json((out / "answers.json").read_text())
        assert m.answers == len(rec.answers) and m.answers_model == rec.model
    assert m.files == n + 1 + (out / "answers.json").exists()
    return n


def test_file_names_agree_with_the_web_app():
    cases = json.loads((ROOT / "web" / "src" / "snapshot-keys.json").read_text())["cases"]
    for c in cases:
        assert snapshot.key(c["path"], c["query"]) == c["key"]


def test_the_quick_questions_are_the_ones_the_assistant_panel_asks():
    src = (ROOT / "web" / "src" / "components" / "Assistant.tsx").read_text()
    quick = re.search(r"const QUICK = \[(.*?)\];", src, re.S).group(1)
    assert tuple(re.findall(r'"([^"]+)"', quick)) == snapshot.QUICK


def test_every_committed_snapshot_file_validates_against_its_response_model():
    n = _validate_tree(COMMITTED)
    m = snapshot.SnapshotManifest.model_validate_json((COMMITTED / "manifest.json").read_text())
    assert n > 0 and m.bytes < 25_000_000
    assert [(s.asset_id, s.scenario) for s in m.sessions] == [
        ("cira-pump-B", None), ("cira-pump-A", None), ("cira-pump-B", "B_stuck_pressure")]
    assert all(s.status == "completed" for s in m.sessions)


# --- on a test database ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("snapshot")
    with test_database("pumpcopilot_test_snapshot") as url:
        with db.connect(url) as c:
            db.migrate(c)
            pause_policy_jobs(c)
            limits = load_replay_day(c, tmp)
            clock = Clock()
            for scenario in (None, "B_stuck_pressure"):
                sid = replay.create_session(c, ASSET, DAY, speed=60, scenario=scenario,
                                            stale_limits=limits)
                run_to_end(c, sid, clock)
        yield {"url": url, "tmp": tmp}


class _Counted(assistant.FakeProvider):
    """A scripted provider that counts each request against the ledger, as the real one does."""

    def __init__(self, ledger, script):
        super().__init__(script)
        self.ledger = ledger

    def generate(self, ctx, question):
        self.ledger.reserve("generate")
        return super().generate(ctx, question)


@pytest.mark.db
def test_answers_are_recorded_through_the_endpoint_within_the_cap(snap, tmp_path):
    out, ledger = tmp_path / "answers.json", tmp_path / "ledger.json"
    rec = snapshot.record_answers(snap["url"], out=out, ledger_path=ledger,
                                  provider_factory=lambda lg: _Counted(lg, [{}]))
    assert rec["requests_used"] == 6 and rec["cap"] == 6 and len(rec["answers"]) == 6
    assert [a["synthetic"] for a in rec["answers"]] == [False] * 3 + [True] * 3
    assert [a["question"] for a in rec["answers"]] == list(snapshot.QUICK) * 2
    # a malformed model answer falls back to the evidence summary, and is recorded as such
    assert {a["response"]["served"] for a in rec["answers"]} == {"template"}
    assert {a["response"]["fallback_reason"] for a in rec["answers"]} == {"malformed answer"}
    snapshot.RecordedAnswers.model_validate_json(out.read_text())
    with pytest.raises(SystemExit, match="recorded already"):  # never asked twice
        snapshot.record_answers(snap["url"], out=out, ledger_path=ledger)


@pytest.mark.db
def test_recording_refuses_to_start_without_six_requests_left(snap, tmp_path):
    ledger = assistant.RequestLedger(tmp_path / "ledger.json", cap=6)
    ledger.reserve("earlier")
    with pytest.raises(SystemExit, match="5 of 6 requests left"):
        snapshot.record_answers(snap["url"], out=tmp_path / "a.json", ledger_path=ledger.path,
                                provider_factory=lambda lg: _Counted(lg, [{}]))
    assert ledger.used() == 1 and not (tmp_path / "a.json").exists()


@pytest.mark.db
def test_the_export_writes_a_valid_file_for_every_request(snap, tmp_path):
    answers = tmp_path / "answers.json"
    snapshot.record_answers(snap["url"], out=answers, ledger_path=tmp_path / "ledger.json",
                            provider_factory=lambda lg: _Counted(lg, [{}]))
    out = tmp_path / "snapshot"
    m = snapshot.export(snap["url"], out=out, answers=answers)
    assert _validate_tree(out) == m["files"] - 2
    assert [s["scenario"] for s in m["sessions"]] == [None, "B_stuck_pressure"]
    for path, q in [("/api/fleet", {}), ("/api/cases", {"status": "open", "limit": 8}),
                    (f"/api/assets/{ASSET}/days/{DAY}/signals", {"resolution": "1m"})]:
        assert (out / snapshot.key(path, q)).exists()
    assert not (out / "api" / "health.json").exists()  # live status is never exported
