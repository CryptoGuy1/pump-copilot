"""Step 7b: the static snapshot for GitHub Pages.

The snapshot is the API's own responses for the static screens, written as files that the web
app's static build reads instead of the API. Its source is a dedicated local database prepared
by scripts/snapshot_source.sh (reset, load, three complete replays).

* `record_answers`: one real checked answer for each quick question on one real and one
  synthetic case, through the normal endpoint (POST /api/cases/{id}/assistant: the checker
  and the fallback), within a ledger capped at 6 requests. Saved to reports/snapshot_answers.json.
* `export`: the GET responses the static screens use, the recorded answers, and a manifest,
  into web/public/snapshot/. Every request goes through the app, so every file is a real
  response; a request with no file shows "Not included in this snapshot" in the app.

A request's file is named by `key`, which the web app computes the same way (web/src/snapshot.ts;
both are tested against web/src/snapshot-keys.json).
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from . import api_models as M
from .provenance import project_path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web" / "public" / "snapshot"
ANSWERS = ROOT / "reports" / "snapshot_answers.json"
LEDGER = ROOT / "reports" / "anthropic_requests_snapshot.json"
CAP = 6
# The quick questions, as the assistant panel asks them (web/src/components/Assistant.tsx).
QUICK = ("Summarize this case", "Which signal drove it?", "What should I check next?")
STATUSES = ("open", "acknowledged", "dispositioned", "closed")


def key(path: str, query: dict | None = None) -> str:
    """The snapshot file for one GET request: the path, then the query sorted by name (unset
    values left out), e.g. api/cases@limit=8~status=open.json."""
    q = sorted((k, str(v).lower() if isinstance(v, bool) else str(v))
               for k, v in (query or {}).items() if v is not None)
    base = path.strip("/")
    return base + ("@" + "~".join(f"{k}={v}" for k, v in q) if q else "") + ".json"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordedAnswer(_Strict):
    case_id: int
    synthetic: bool
    question: str
    response: M.AssistantResponse


class RecordedAnswers(_Strict):
    recorded_at: dt.datetime
    provider: str
    model: str
    cap: int
    requests_used: int
    answers: list[RecordedAnswer]


class SnapshotSession(_Strict):
    session_id: int
    asset_id: str
    source_day: str
    scenario: str | None
    synthetic: bool
    status: str


class SnapshotManifest(_Strict):
    exported_at: dt.datetime
    commit: str
    commit_dirty: bool
    sessions: list[SnapshotSession]
    answers_model: str | None
    answers_recorded_at: dt.datetime | None
    answers: int
    files: int
    bytes: int
    not_included: list[str]


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _cases_for_answers(client) -> list[tuple[int, bool]]:
    """The first case of the first real session and of the first synthetic session."""
    sessions = client.get("/api/replay/sessions").json()["sessions"]
    picked = []
    for synthetic in (False, True):
        s = next(x for x in sorted(sessions, key=lambda x: x["session_id"])
                 if x["synthetic"] is synthetic)
        cases = client.get("/api/cases", params={"session_id": s["session_id"]}).json()["cases"]
        if not cases:
            raise RuntimeError(f"session {s['session_id']} has no case")
        picked.append((min(c["case_id"] for c in cases), synthetic))
    return picked


def record_answers(database_url: str, out: Path = ANSWERS, ledger_path: Path = LEDGER,
                   cap: int = CAP, provider_factory=None) -> dict:
    """Ask the three quick questions on one real and one synthetic case, once each, through the
    normal endpoint. Refuses to start if the answers exist already or the ledger has fewer than
    6 requests left; stops at the first request the API could not complete."""
    from fastapi.testclient import TestClient

    from . import api, assistant

    if out.exists():
        raise SystemExit(f"{project_path(out)} exists: the answers are recorded already")
    ledger = assistant.RequestLedger(ledger_path, cap=cap)
    planned = 2 * len(QUICK)
    if ledger.remaining() < planned:
        raise SystemExit(f"the ledger has {ledger.remaining()} of {cap} requests left; "
                         f"{planned} are needed")
    factory = provider_factory or (lambda lg: assistant.AnthropicProvider(max_retries=0,
                                                                          ledger=lg))
    provider = factory(ledger)
    app = api.create_app(database_url=database_url, assistant_provider=lambda: provider)
    answers = []
    with TestClient(app) as c:
        for case_id, synthetic in _cases_for_answers(c):
            for q in QUICK:
                r = c.post(f"/api/cases/{case_id}/assistant",
                           json={"question": q, "provider": "auto"})
                if r.status_code != 200:
                    raise SystemExit(f"stopped: case {case_id}, {q!r}: HTTP {r.status_code}; "
                                     f"{ledger.used()} of {cap} requests used")
                answers.append({"case_id": case_id, "synthetic": synthetic, "question": q,
                                "response": r.json()})
    rec = RecordedAnswers.model_validate({
        "recorded_at": dt.datetime.now(dt.UTC).replace(microsecond=0),
        "provider": provider.name, "model": provider.model, "cap": cap,
        "requests_used": ledger.used(), "answers": answers})
    out.write_text(rec.model_dump_json(indent=1) + "\n")
    return rec.model_dump(mode="json")


def _requests(c) -> list[tuple[str, dict]]:
    """Every GET the static screens make, for the snapshot database's contents."""
    reqs: list[tuple[str, dict]] = [(p, {}) for p in (
        "/api/fleet", "/api/assets", "/api/signal-names", "/api/assumptions", "/api/about",
        "/api/evaluation/chapters", "/api/data-quality", "/api/replay/sessions",
        "/api/replay/scenarios", "/api/cases")]
    reqs += [("/api/cases", {"status": "open", "limit": 8}),   # fleet: open cases
             ("/api/cases", {"limit": 5})]                     # design system gallery
    reqs += [("/api/cases", {"status": s}) for s in STATUSES]  # the cases page's filters
    sessions = c.get("/api/replay/sessions").json()["sessions"]
    for a in sorted({s["asset_id"] for s in sessions}):
        reqs.append(("/api/cases", {"asset_id": a}))
    for d in c.get("/api/assets").json()["asset_days"]:
        reqs.append((f"/api/assets/{d['asset_id']}/days/{d['source_day']}/data-quality", {}))
    days = sorted({(s["asset_id"], s["source_day"]) for s in sessions})
    for a, d in days:
        base = f"/api/assets/{a}/days/{d}"
        reqs += [(base, {}), (f"{base}/segments", {}), (f"{base}/signals", {"resolution": "1m"}),
                 (f"{base}/scores", {})]
        reqs += [(f"{base}/scores", {"session_id": s["session_id"]}) for s in sessions
                 if (s["asset_id"], s["source_day"]) == (a, d)]
    for s in sessions:
        reqs += [("/api/cases", {"session_id": s["session_id"]}),
                 (f"/api/replay/sessions/{s['session_id']}/baseline", {})]
    for case in c.get("/api/cases", params={"limit": 2000}).json()["cases"]:
        cid = case["case_id"]
        reqs += [(f"/api/cases/{cid}", {}), (f"/api/cases/{cid}/evidence-summary", {})]
        offset = 0
        while offset is not None:
            q = {"offset": offset, "limit": 500}
            reqs.append((f"/api/cases/{cid}/evidence", q))
            offset = c.get(f"/api/cases/{cid}/evidence", params=q).json().get("next_offset")
    return reqs


NOT_INCLUDED = [
    "live stream (/api/stream) and worker health (/api/health)",
    "raw-resolution signals, and asset-days that were not replayed",
    "case exports, and any case-list filter beyond one status, session or pump",
    "every action: case actions, replay controls and new assistant questions",
]


def export(database_url: str, out: Path = OUT, answers: Path = ANSWERS) -> dict:
    """Write the snapshot: every response under out/api/, the recorded answers and a manifest.
    The directory is replaced as a whole."""
    from fastapi.testclient import TestClient

    from . import api, assistant

    rec = RecordedAnswers.model_validate_json(answers.read_text()) if answers.exists() else None
    # the commit the export is made from, before writing anything (its own output is left out)
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--", ".", f":(exclude){project_path(out)}"))
    if out.exists():
        shutil.rmtree(out)
    (out / "api").mkdir(parents=True)
    total = files = 0
    app = api.create_app(database_url=database_url,
                         assistant_provider=lambda: assistant.TemplateProvider())
    with TestClient(app) as c:
        for path, q in _requests(c):
            r = c.get(path, params=q)
            if r.status_code != 200:
                raise RuntimeError(f"GET {path} {q}: HTTP {r.status_code}: {r.text[:200]}")
            f = out / key(path, q)
            if f.exists():
                continue
            f.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps(r.json(), separators=(",", ":"))
            f.write_text(body)
            files += 1
            total += len(body.encode())
        sessions = c.get("/api/replay/sessions").json()["sessions"]
    if rec:  # each recorded answer must still describe its case as it is now
        from . import db

        with db.connect(database_url) as conn:
            for a in rec.answers:
                now = assistant.context_hash(assistant.build_context(conn, a.case_id))
                if now != a.response.context_hash:
                    raise RuntimeError(f"case {a.case_id} changed since its answer was recorded")
    if rec:
        body = rec.model_dump_json()
        (out / "answers.json").write_text(body)
        files += 1
        total += len(body.encode())
    manifest = SnapshotManifest.model_validate({
        "exported_at": dt.datetime.now(dt.UTC).replace(microsecond=0),
        "commit": commit, "commit_dirty": dirty,
        "sessions": [{k: s[k] for k in ("session_id", "asset_id", "source_day", "scenario",
                                        "synthetic", "status")}
                     for s in sorted(sessions, key=lambda s: s["session_id"])],
        "answers_model": rec.model if rec else None,
        "answers_recorded_at": rec.recorded_at if rec else None,
        "answers": len(rec.answers) if rec else 0, "files": files + 1, "bytes": 0,
        "not_included": NOT_INCLUDED})
    body = manifest.model_dump_json(indent=1)
    manifest.bytes = total + len(body.encode())
    (out / "manifest.json").write_text(manifest.model_dump_json(indent=1) + "\n")
    return manifest.model_dump(mode="json")
