"""Step 4b API: contract, rules for every endpoint, case actions, replay control, guardrails."""
import ast
import datetime as dt
import json
import re
import socket
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from conftest import pause_policy_jobs, test_database
from fastapi.testclient import TestClient
from replay_helpers import ASSET, DAY, START, Clock, load_replay_day, run_to_end

from pumpcopilot import api, cases, db, events, replay

ROOT = Path(__file__).resolve().parents[1]
D = DAY.isoformat()
PROVENANCE = ("synthetic", "model_version", "assumptions")


def _app(url=None, reports=None, data=None):
    return api.create_app(database_url=url, reports_dir=reports or ROOT / "reports",
                          docs_dir=ROOT / "docs", data_dir=data or ROOT / "data")


def _err(r, status, code=None):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and {"status", "code", "message"} <= set(body["error"])
    assert body["error"]["status"] == status
    if code:
        assert body["error"]["code"] == code
    return body["error"]


# --- no database needed ------------------------------------------------------------------

def test_openapi_schema_matches_the_committed_file():
    committed = json.loads((ROOT / "api" / "openapi.json").read_text())
    assert _app().openapi() == committed, (
        "the API changed: regenerate with `pumpcopilot api --export-openapi` and commit "
        "api/openapi.json")


def _api_routes():
    from fastapi.routing import APIRoute

    return [r for r in _app().routes if isinstance(r, APIRoute) and r.path.startswith("/api/")]


def test_every_endpoint_declares_a_response_model():
    for r in _api_routes():
        if r.path == "/api/stream":  # server-sent events: the event payloads are modelled
            continue
        assert r.response_model is not None, f"{sorted(r.methods)} {r.path}"
        assert r.response_model.model_config.get("extra") == "forbid", r.path
    schema = _app().openapi()
    stream = schema["paths"]["/api/stream"]["get"]["responses"]["200"]["content"]
    assert stream["text/event-stream"]["schema"] == {"$ref": "#/components/schemas/StreamEvent"}
    for name in ("ReplayProgressEvent", "ScoreBatchEvent", "CaseEvent", "StreamEvent"):
        assert name in schema["components"]["schemas"]
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            ok = op["responses"].get("200") or op["responses"].get("201")
            body = ok["content"].get("application/json", {}).get("schema")
            assert path == "/api/stream" or body and body != {}, f"{method} {path}"


def test_api_command_binds_to_loopback_only(monkeypatch):
    from pumpcopilot import cli

    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(kw))
    cli.main(["api", "--port", "8123"])
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8123
    with pytest.raises(SystemExit):
        cli.main(["api", "--host", "0.0.0.0"])


def test_cors_allows_the_local_frontend_dev_server_only():
    c = TestClient(_app())
    pre = {"Access-Control-Request-Method": "GET"}
    ok = c.options("/api/fleet", headers={"Origin": "http://localhost:5173", **pre})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = c.options("/api/fleet", headers={"Origin": "https://example.com", **pre})
    assert "access-control-allow-origin" not in bad.headers


def test_errors_share_one_format():
    c = TestClient(_app())
    _err(c.get("/api/no-such-thing"), 404, "not_found")
    e = _err(c.post("/api/replay/sessions", json={"asset_id": ASSET, "source_day": D,
                                                  "speed": 5}), 422, "validation_error")
    assert e["details"]
    _err(c.get(f"/api/assets/{ASSET}/days/not-a-date/segments"), 422, "validation_error")


def test_assumptions_register_is_served_as_data():
    items = TestClient(_app()).get("/api/assumptions").json()["assumptions"]
    ids = [a["id"] for a in items]
    assert ids[:8] == [f"A{i}" for i in range(1, 9)]
    a8 = items[7]
    assert "Replay" in a8["title"] and a8["assumption"] and a8["impact_if_wrong"]
    assert a8["how_to_revisit"] and a8["evidence"]


def test_evaluation_results_are_served_as_data(tmp_path):
    for name, body in (("cira_scoring_eval.json", {"pumps": {"B": {}}}),
                       ("cira_scoring_eval_3a2.json", {"label": "post-hoc"}),
                       ("cira_scoring_eval_3a3.json", {"label": "3a-3", "pumps": {}}),
                       ("cira_cases_all_modes.json", {"3a across-day": {}})):
        (tmp_path / name).write_text(json.dumps(body))
    out = TestClient(_app(reports=tmp_path)).get("/api/evaluation").json()
    assert out["modes"]["3a-3"]["results"]["label"] == "3a-3"
    assert out["modes"]["3a-2"]["label"].startswith("post-hoc revision")
    assert out["modes"]["3a-3"]["label"].startswith("3a-3: pre-registered")
    assert out["cases_all_modes"] == {"3a across-day": {}}
    missing = TestClient(_app(reports=tmp_path / "none")).get("/api/evaluation").json()
    assert missing["modes"]["3a"]["results"] is None and missing["modes"]["3a"]["note"]


def test_zema_scores_carry_their_measured_calibration_in_plain_language(tmp_path):
    import numpy as np

    from pumpcopilot import zema_bench

    reports = tmp_path / "reports"
    reports.mkdir()
    score = zema_bench.scored_evidence("logreg", "chronological", 1814, np.array([.2, .7, .1]),
                                       "v1", calibration_measured=True).model_dump(mode="json")
    cal = {"brier": 0.875, "ece_top_label": 0.404}
    (reports / "zema_benchmark.json").write_text(json.dumps({
        "preregistration": {"tag": "prereg-3b-r2"}, "splits": {"chronological": {
            "logreg": {"calibration": cal}, "majority": {"calibration": {"brier": 0.709,
                                                                         "ece_top_label": 0}}}},
        "scores": [{"split": "chronological", "model": "logreg", "evidence": score}]}))
    z = TestClient(_app(reports=reports, data=tmp_path)).get("/api/evaluation").json()["zema"]
    c = z["scores"][0]["calibration"]
    assert (c["brier"], c["ece"], c["grade"]) == (0.875, 0.404, "very poor")
    assert "worse than always predicting the class shares" in c["text"]


def test_the_zema_benchmark_is_served_with_its_scope_and_status(tmp_path):
    import yaml

    data, reports = tmp_path / "data", tmp_path / "reports"
    data.mkdir()
    reports.mkdir()
    z = TestClient(_app(reports=reports, data=data)).get("/api/evaluation").json()["zema"]
    assert z["status"] == "not_tuned" and z["config"] is None and z["results"] is None
    assert "hydraulic test rig" in z["scope_note"] and "centrifugal" in z["scope_note"]
    (data / "zema_benchmark.yaml").write_text(yaml.safe_dump({"frozen": {"selected": {}}}))
    z = TestClient(_app(reports=reports, data=data)).get("/api/evaluation").json()["zema"]
    assert z["status"] == "pre-registered, not yet evaluated" and z["config"]
    assert z["scores"] == []
    import numpy as np

    from pumpcopilot import zema_bench

    score = zema_bench.scored_evidence("logreg", "chronological", 1814,
                                       np.array([0.2, 0.7, 0.1]), "v1",
                                       calibration_measured=True).model_dump(mode="json")
    (reports / "zema_benchmark.json").write_text(json.dumps({
        "splits": {}, "preregistration": {"tag": "prereg-3b-r2"},
        "scores": [{"split": "chronological", "model": "logreg", "evidence": score}]}))
    z = TestClient(_app(reports=reports, data=data)).get("/api/evaluation").json()["zema"]
    assert z["status"] == "evaluated"
    assert z["results"]["preregistration"]["tag"] == "prereg-3b-r2"
    ev = z["scores"][0]["evidence"]
    assert ev["cycle_id"] == 1814 and ev["time_is_placeholder"] is True
    assert ev["confidence_calibration_status"] == "calibration_measured"
    assert z["scores"][0]["calibration"] is None  # the results carry no measurement here
    assert ev["output_label"] == "hydraulic test rig pump leakage state: 1"
    assert z["preregistration_tag"] == "prereg-3b-r2"
    assert z["output_label"] == "hydraulic test rig pump leakage state: k"


def _source(mod) -> str:
    return Path(mod.__file__).read_text()


def test_guardrail_api_code_reaches_nothing_outside_the_app():
    from pumpcopilot import events

    banned = {"requests", "httpx", "urllib", "smtplib", "subprocess", "webbrowser", "aiohttp",
              "socket", "ftplib", "http.client"}
    for mod in (api, cases, events):
        tree = ast.parse(_source(mod))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not {x for x in names if x.split(".")[0] in banned or x in banned}, mod.__name__
    paths = [r.path for r in _app().routes]
    assert not [p for p in paths if any(w in p.lower() for w in
                                        ("escalat", "notify", "email", "webhook", "send"))]
    assert "escalate to reliability engineer (export only)" in cases.DISPOSITIONS


def _imported_modules(start: str) -> set[str]:
    """pumpcopilot modules the API process imports (following `from . import x`)."""
    import pumpcopilot

    pkg = Path(pumpcopilot.__file__).parent
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name in seen or not (pkg / f"{name}.py").exists():
            continue
        seen.add(name)
        for n in ast.walk(ast.parse((pkg / f"{name}.py").read_text())):
            if isinstance(n, ast.ImportFrom) and n.level == 1:
                todo += [n.module] if n.module else [a.name for a in n.names]
    return seen


def test_guardrail_only_the_assistant_module_has_a_network_client():
    import pumpcopilot

    clients = {"requests", "httpx", "httpx2", "urllib", "smtplib", "aiohttp", "ftplib",
               "http", "anthropic", "websocket", "websockets", "paramiko"}
    mods = _imported_modules("api")
    assert {"api", "cases", "events", "replay", "assistant"} <= mods
    pkg = Path(pumpcopilot.__file__).parent
    for name in sorted(mods):
        tree = ast.parse((pkg / f"{name}.py").read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                  and n.module and n.level == 0}
        used = {x for x in names if x.split(".")[0] in clients}
        if name == "assistant":
            assert used <= {"httpx2", "anthropic"}, used
        else:
            assert not used, (name, used)
    src = (pkg / "assistant.py").read_text()
    assert 'ALLOWED_HOST = "api.anthropic.com"' in src and "AllowlistTransport()" in src


def test_guardrail_api_queries_telemetry_only_through_scoped_functions():
    import re

    src = _source(api)
    assert not re.search(r"(?i)\b(from|join|into|update)\s+(telemetry|readings|telemetry_1m)\b",
                         src)
    for fn in db.QUERY_FUNCTIONS:
        assert fn.__name__ in src or fn is db.fetch_readings


# --- with a database ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """One database for the module: the replay day, a completed real session and a completed
    synthetic one."""
    tmp = tmp_path_factory.mktemp("api")
    with test_database("pumpcopilot_test_api") as url:
        with db.connect(url) as c:
            db.migrate(c)
            pause_policy_jobs(c)
            limits = load_replay_day(c, tmp)
            clock = Clock()
            real = replay.create_session(c, ASSET, DAY, speed=60, stale_limits=limits)
            run_to_end(c, real, clock)
            syn = replay.create_session(c, ASSET, DAY, speed=60, scenario="B_stuck_pressure",
                                        stale_limits=limits)
            run_to_end(c, syn, clock)
        reports = tmp / "reports"
        reports.mkdir()
        (reports / "cira_audit.json").write_text(json.dumps({"ok": False, "issues": [
            "B_2024-06-11.csv: example issue"], "site_gaps": [], "files": [
            {"file": "B_2024-06-11.csv", "pump": "B", "day": D, "rows": 10800,
             "issues": ["example issue"], "cadence_segments": [],
             "gaps_over_factor": {"factor": 5.0, "count": 1, "site_level": 0,
                                  "items": [{"after": str(START), "gap_s": 70}]}}]}))
        yield {"url": url, "limits": limits, "real": real, "synthetic": syn, "clock": clock,
               "reports": reports}


@pytest.fixture
def client(env):
    with TestClient(_app(env["url"], env["reports"])) as c:
        yield c


def _fresh_session(env, scenario=None, stop_at=None):
    with db.connect(env["url"]) as c:
        sid = replay.create_session(c, ASSET, DAY, speed=60, scenario=scenario,
                                    stale_limits=env["limits"])
        run_to_end(c, sid, env["clock"], stop_at=stop_at)
        return sid, [x["case_id"] for x in cases.list_cases(c, session_id=sid)]


def _provenance(body, synthetic):
    assert all(k in body for k in PROVENANCE)
    assert body["synthetic"] is synthetic
    assert body["model_version"] and all(isinstance(v, str) for v in body["model_version"])
    assert {"A5", "A8"} <= set(body["assumptions"])


@pytest.mark.db
def test_health_reports_database_and_worker(env, client):
    with db.connect(env["url"]) as c:  # the fixture's workers beat a moment ago: age them
        c.execute("UPDATE worker_heartbeats SET last_seen = now() - interval '1 hour'")
    h = client.get("/api/health").json()
    assert h["database"]["ok"] is True and h["database"]["pending_migrations"] == []
    assert h["status"] == "degraded" and h["worker"]["alive"] is False
    with db.connect(env["url"]) as c:
        replay.Worker(c, "health-check-worker").heartbeat()
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["worker"]["alive"] is True
    assert "health-check-worker" in [w["worker_id"] for w in h["worker"]["workers"]]


@pytest.mark.db
def test_a_worker_is_known_by_a_short_random_id_and_no_host_name(env, client):
    import re
    import socket

    with db.connect(env["url"]) as c:
        w = replay.Worker(c)
        w.heartbeat()
        cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns"
                                        " WHERE table_name = 'worker_heartbeats'")}
    assert re.fullmatch(r"worker-[0-9a-f]{4}", w.id)
    assert "host" not in cols
    body = client.get("/api/health").text
    assert w.id in body and socket.gethostname() not in body


@pytest.mark.db
def test_fleet_overview(env, client):
    body = client.get("/api/fleet").json()
    pump = {p["asset_id"]: p for p in body["pumps"]}[ASSET]
    assert pump["days"] == [D]
    # every session is reported separately, synthetic ones included, newest first
    ids = [x["session"]["session_id"] for x in pump["sessions"]]
    assert env["real"] in ids and env["synthetic"] in ids and ids == sorted(ids, reverse=True)
    syn = next(x for x in pump["sessions"] if x["session"]["session_id"] == env["synthetic"])
    _provenance(syn, synthetic=True)
    assert syn["session"]["synthetic"] is True and syn["open_cases"] >= 1
    # the pump's own state is its latest real session's, never a synthetic one's
    real = max(x["session"]["session_id"] for x in pump["sessions"]
               if not x["session"]["synthetic"])
    assert pump["state_session_id"] == real and pump["synthetic"] is False
    assert pump["state"] == next(x["state"] for x in pump["sessions"]
                                 if x["session"]["session_id"] == real)
    assert pump["open_cases"]["real"] >= 1 and pump["open_cases"]["synthetic"] >= 1
    dq = pump["data_quality"]
    assert dq["status"] == "issues" and dq["audit_issues"] == 1 and dq["gaps"] == 1
    assert {"stale_suspected", "spike_suspected"} <= set(dq["flag_counts"])


def _expected_state(c, sid) -> dict:
    """The fleet rule, recomputed here from the scores at or before the session cursor."""
    latest = c.execute(
        "SELECT DISTINCT ON (signal_name) signal_name, presentation_state FROM scores s WHERE"
        " session_id = %s AND window_end <= (SELECT cursor_at FROM replay_sessions WHERE"
        " session_id = s.session_id) ORDER BY signal_name, window_end DESC", [sid]).fetchall()
    st = {r[1] for r in latest}
    agg = ("insufficient_evidence" if not st else "review_suggested" if "review_suggested" in st
           else "normal" if st == {"normal"} else "insufficient_evidence"
           if "insufficient_evidence" in st else "data_unavailable")
    return {"state": agg, "signals": {r[0]: r[1] for r in latest}}


@pytest.mark.db
def test_fleet_states_are_computed_on_the_server_per_session(env, client):
    body = client.get("/api/fleet").json()
    with db.connect(env["url"]) as c:
        for pump in body["pumps"]:
            for x in pump["sessions"]:
                sid = x["session"]["session_id"]
                exp = _expected_state(c, sid)
                assert x["state"]["state"] == exp["state"], sid
                assert x["state"]["signals"] == exp["signals"], sid
                assert x["state"]["as_of"] == x["session"]["cursor_at"], sid


@pytest.mark.db
def test_asset_day_signals_are_scoped_and_bounded(client):
    base = f"/api/assets/{ASSET}/days/{D}"
    one = client.get(f"{base}/signals", params={"resolution": "1m"}).json()
    assert one["asset_id"] == ASSET and one["source_day"] == D and one["resolution"] == "1m"
    assert {"outlet_pressure", "ambient_temperature"} <= set(one["signals"])
    p = one["signals"]["outlet_pressure"]
    assert len(p) == 180 and {"bucket", "min", "max", "mean", "samples"} <= set(p[0])
    raw = client.get(f"{base}/signals", params={
        "resolution": "raw", "signal": "outlet_pressure", "start": str(START),
        "end": str(START + pd.Timedelta(minutes=10))}).json()
    assert len(raw["signals"]["outlet_pressure"]) == 600
    _err(client.get(f"{base}/signals", params={"resolution": "raw"}), 422)
    _err(client.get(f"{base}/signals", params={
        "resolution": "raw", "start": str(START),
        "end": str(START + pd.Timedelta(hours=4))}), 422)
    _err(client.get(f"/api/assets/{ASSET}/days/2024-06-12/signals"), 404, "not_found")
    _err(client.get("/api/assets/cira-pump-Z/days/2024-06-11/segments"), 404, "not_found")


@pytest.mark.db
def test_asset_day_segments_scores_and_bands(env, client):
    base = f"/api/assets/{ASSET}/days/{D}"
    segs = client.get(f"{base}/segments").json()["segments"]
    assert [s["state"] for s in segs if s["state"] == "running"]
    sc = client.get(f"{base}/scores", params={"session_id": env["real"]}).json()
    _provenance(sc, synthetic=False)
    row = next(r for r in sc["scores"] if r["state"] == "normal")
    assert {"signal_name", "window_start", "window_end", "state", "score", "median",
            "band_low", "band_high", "model_version", "synthetic"} <= set(row)
    assert row["band_low"] <= row["median"] <= row["band_high"]
    default = client.get(f"{base}/scores").json()  # latest real session by default
    assert default["session_id"] == env["real"]
    syn = client.get(f"{base}/scores", params={"session_id": env["synthetic"]}).json()
    _provenance(syn, synthetic=True)
    bands = client.get(f"{base}/bands", params={"session_id": env["real"]}).json()
    _provenance(bands, synthetic=False)
    b = bands["bands"]["runs"][0]["signals"]["outlet_pressure"]
    assert b["status"] == "formed" and b["band"]["low"] < b["band"]["high"]
    _err(client.get(f"{base}/scores", params={"session_id": 10**6}), 404)


@pytest.mark.db
def test_case_list_counts_real_and_synthetic_instead_of_one_flag(env, client):
    lst = client.get("/api/cases", params={"session_id": env["real"]}).json()
    assert "synthetic" not in lst
    assert lst["real_count"] == len(lst["cases"]) and lst["synthetic_count"] == 0
    assert lst["model_version"] and {"A5", "A8"} <= set(lst["assumptions"])
    assert all(c["synthetic"] is False for c in lst["cases"])
    both = client.get("/api/cases", params={"asset_id": ASSET}).json()
    assert both["real_count"] >= 1 and both["synthetic_count"] >= 1
    assert both["real_count"] + both["synthetic_count"] == len(both["cases"])
    assert {c["synthetic"] for c in both["cases"]} == {True, False}


@pytest.mark.db
def test_case_detail_is_a_summary_with_a_downsampled_band_chart(env, client):
    cid = client.get("/api/cases", params={"session_id": env["real"]}).json(
        )["cases"][0]["case_id"]
    r = client.get(f"/api/cases/{cid}")
    d = r.json()
    _provenance(d, synthetic=False)
    assert d["case"]["case_id"] == cid and d["case"]["status"] == "open"
    assert [e["event_type"] for e in d["timeline"]][0] == "opened"
    assert "evidence_added" not in {e["event_type"] for e in d["timeline"]}
    assert "evidence" not in d and d["evidence_url"] == f"/api/cases/{cid}/evidence"
    sig = d["signals"]["outlet_pressure"]
    summary = sig["summary"]
    assert summary["windows"] >= 1 and summary["episodes"] >= 1 and summary["max_score"] > 1
    assert sum(x["summary"]["windows"] for x in d["signals"].values()) == d["case"][
        "evidence_windows"]
    assert sig["band"]["low"] < sig["band"]["high"]
    chart = sig["chart"]
    n = len(chart["t"])
    assert 1 <= n <= d["max_points"] and all(len(chart[k]) == n for k in (
        "median", "min", "max", "review"))
    assert all(lo <= m <= hi for lo, m, hi in zip(chart["min"], chart["median"], chart["max"],
                                                 strict=True) if m is not None)
    small = client.get(f"/api/cases/{cid}", params={"max_points": 10}).json()
    assert all(len(x["chart"]["t"]) <= 10 for x in small["signals"].values())
    _err(client.get(f"/api/cases/{cid}", params={"max_points": 5000}), 422)
    assert len(r.content) < 100 * 1024
    assert d["related"] == {"related_case": None, "related_by": []}
    assert d["actions"] == ["acknowledge", "note"]
    _err(client.get("/api/cases/999999999"), 404, "not_found")


@pytest.mark.db
def test_case_evidence_is_paginated(env, client):
    cid = client.get("/api/cases", params={"session_id": env["real"]}).json(
        )["cases"][0]["case_id"]
    total = client.get(f"/api/cases/{cid}").json()["case"]["evidence_windows"]
    seen, offset = [], 0
    while offset is not None:
        page = client.get(f"/api/cases/{cid}/evidence", params={"offset": offset,
                                                                "limit": 7}).json()
        _provenance(page, synthetic=False)
        assert page["total"] == total and len(page["items"]) <= 7
        seen += [(x["signal_name"], x["window_end"]) for x in page["items"]]
        offset = page["next_offset"]
    assert len(seen) == total == len(set(seen))
    assert {"window_start", "median", "band_low", "band_high", "score", "state",
            "model_version"} <= set(page["items"][0])
    signal = client.get(f"/api/cases/{cid}/evidence", params={"signal": "outlet_pressure"}
                        ).json()
    assert {x["signal_name"] for x in signal["items"]} == {"outlet_pressure"}
    _err(client.get(f"/api/cases/{cid}/evidence", params={"limit": 5000}), 422)
    _err(client.get("/api/cases/999999999/evidence"), 404)


@pytest.mark.db
def test_case_actions_enforce_the_4a_rules(env, client):
    _, (cid, *_) = _fresh_session(env)
    u = f"/api/cases/{cid}"
    _err(client.post(f"{u}/disposition", json={"actor": "op", "disposition": "monitor",
                                               "reason": "r"}), 409, "invalid_transition")
    _err(client.post(f"{u}/close", json={"actor": "op"}), 409, "invalid_transition")
    r = client.post(f"{u}/acknowledge", json={"actor": "op"})
    assert r.status_code == 200 and r.json()["case"]["status"] == "acknowledged"
    _err(client.post(f"{u}/acknowledge", json={"actor": "op"}), 409, "invalid_transition")
    _err(client.post(f"{u}/disposition", json={"actor": "op", "disposition": "monitor",
                                               "reason": " "}), 422)
    _err(client.post(f"{u}/disposition", json={"actor": "op", "disposition": "replace pump",
                                               "reason": "x"}), 422)
    assert client.post(f"{u}/notes", json={"actor": "op", "text": "looked at it"}
                       ).status_code == 200
    r = client.post(f"{u}/disposition", json={
        "actor": "op", "disposition": "escalate to reliability engineer (export only)",
        "reason": "pressure shift"})
    assert r.json()["case"]["status"] == "dispositioned"
    assert r.json()["actions"] == ["note", "disposition", "close", "export"]
    assert client.post(f"{u}/close", json={"actor": "op"}).json()["case"]["status"] == "closed"
    _err(client.post(f"{u}/notes", json={"actor": "op", "text": "late"}), 409,
         "invalid_transition")
    _err(client.post("/api/cases/999999999/acknowledge", json={"actor": "op"}), 404)


@pytest.mark.db
def test_related_cases_show_both_ways(env, client):
    sid, (cid,) = _fresh_session(env, stop_at=START + pd.Timedelta(hours=2, minutes=20))
    for step, body in (("acknowledge", {}), ("disposition", {"disposition": "monitor",
                                                             "reason": "watching"}),
                       ("close", {})):
        assert client.post(f"/api/cases/{cid}/{step}", json={"actor": "op", **body}
                           ).status_code == 200
    with db.connect(env["url"]) as c:
        run_to_end(c, sid, env["clock"])
        new = [x["case_id"] for x in cases.list_cases(c, session_id=sid)][1]
    d = client.get(f"/api/cases/{new}").json()
    assert d["related"]["related_case"]["case_id"] == cid
    old = client.get(f"/api/cases/{cid}").json()
    assert [x["case_id"] for x in old["related"]["related_by"]] == [new]


@pytest.mark.db
def test_export_returns_json_and_a_markdown_evidence_pack(env, client):
    cid = client.get("/api/cases", params={"session_id": env["synthetic"]}).json(
        )["cases"][0]["case_id"]
    j = client.get(f"/api/cases/{cid}/export").json()
    _provenance(j, synthetic=True)
    assert j["case"]["case_id"] == cid and j["evidence"] and j["events"]
    md = client.get(f"/api/cases/{cid}/export", params={"format": "markdown"})
    assert md.headers["content-type"].startswith("text/markdown")
    text = md.text
    assert f"# Evidence pack: case {cid}" in text and "SYNTHETIC" in text
    assert "| window" in text and "A8" in text and "sends nothing" in text
    _err(client.get(f"/api/cases/{cid}/export", params={"format": "pdf"}), 422)
    # runs are numbered from 1 for people; the index stays for machines
    assert j["run_index"] == j["case"]["stretch"] and j["run_number"] == j["run_index"] + 1
    assert f"| run | {j['run_number']} |" in text


@pytest.mark.db
def test_the_evidence_summary_is_served_without_a_model_or_a_logged_run(env, client):
    cid = client.get("/api/cases", params={"session_id": env["real"]}).json(
        )["cases"][0]["case_id"]
    with db.connect(env["url"]) as c:
        before = c.execute("SELECT count(*) FROM assistant_runs").fetchone()[0]
    s = client.get(f"/api/cases/{cid}/evidence-summary").json()
    assert s["label"] == "Evidence summary" and s["check"]["passed"] is True
    assert s["answer"]["claims"] and s["evidence"]
    with db.connect(env["url"]) as c:  # not an assistant request: nothing logged
        assert c.execute("SELECT count(*) FROM assistant_runs").fetchone()[0] == before


def test_guard_design_and_e2e_runs_never_use_the_real_model():
    """The browser tests talk to an API that has no key: the design run starts its own API
    with the key emptied and .env not loaded, and the end-to-end backend unsets it."""
    design = (ROOT / "web" / "playwright.design.config.ts").read_text()
    assert "--no-dotenv" in design and 'ANTHROPIC_API_KEY: ""' in design
    api_server = design[design.index("webServer"):]
    assert "reuseExistingServer: false" in api_server.split("},")[0]  # never someone else's API
    backend = (ROOT / "web" / "e2e" / "backend.sh").read_text()
    assert "unset ANTHROPIC_API_KEY" in backend and "--no-dotenv" in backend


@pytest.mark.db
def test_replay_control(env, client):
    assert "B_stuck_pressure" in {s["name"] for s in client.get("/api/replay/scenarios").json(
        )["scenarios"]}
    r = client.post("/api/replay/sessions", json={"asset_id": ASSET, "source_day": D,
                                                  "speed": 10})
    assert r.status_code == 201
    s = r.json()["session"]
    sid = s["session_id"]
    assert s["status"] == "pending" and s["speed"] == 10 and s["synthetic"] is False
    u = f"/api/replay/sessions/{sid}"
    assert client.post(f"{u}/pause").json()["session"]["status"] == "paused"
    _err(client.post(f"{u}/pause"), 409, "invalid_state")
    assert client.post(f"{u}/start").json()["session"]["status"] == "pending"
    with db.connect(env["url"]) as c:
        replay.Worker(c, "api-test", clock=env["clock"]).tick()  # now running
    env["clock"].advance(30)
    r = client.put(f"{u}/speed", json={"speed": 60})
    s = r.json()["session"]
    assert s["speed"] == 60 and s["anchor_cursor"] == s["cursor_at"]
    _err(client.put(f"{u}/speed", json={"speed": 7}), 422, "validation_error")
    assert client.post(f"{u}/rewind").json()["session"]["status"] == "pending"
    base = client.get(f"{u}/baseline").json()
    assert base["session_id"] == sid and "baseline_progress" in base
    syn = client.post("/api/replay/sessions", json={"asset_id": ASSET, "source_day": D,
                                                    "speed": 60,
                                                    "scenario": "B_stuck_pressure"}).json()
    assert syn["session"]["synthetic"] is True
    _err(client.post("/api/replay/sessions", json={"asset_id": ASSET, "source_day": D,
                                                   "speed": 60, "scenario": "nope"}), 422)
    _err(client.post(f"/api/replay/sessions/{10**6}/pause"), 404)
    with db.connect(env["url"]) as c:  # leave nothing claimable for other tests
        for x in (sid, syn["session"]["session_id"]):
            replay.pause_session(c, x)


# --- reads as of the session cursor -----------------------------------------------------

def _source_times(body, day: dt.date) -> list[dt.datetime]:
    """Every timestamp on the source day anywhere in a response (JSON or text)."""
    text = body if isinstance(body, str) else json.dumps(body)
    out = []
    for m in re.finditer(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
                         r"(Z|[+-]\d{2}:\d{2})?", text):
        if m[1] == day.isoformat():
            out.append(dt.datetime.fromisoformat(f"{m[1]}T{m[2]}+00:00"))
    return out


def _two_cases(env, client):
    """A session with two cases: the first closed mid-replay (with its human history), the
    second opened later as a related case. Returns (sid, early, late, first windows)."""
    sid, (early,) = _fresh_session(env, stop_at=START + pd.Timedelta(hours=2, minutes=20))
    for step, body in (("acknowledge", {}), ("notes", {"text": "early case looked at"}),
                       ("disposition", {"disposition": "monitor", "reason": "watching"}),
                       ("close", {})):
        assert client.post(f"/api/cases/{early}/{step}", json={"actor": "op", **body}
                           ).status_code == 200
    with db.connect(env["url"]) as c:
        run_to_end(c, sid, env["clock"])
        first = dict(c.execute(
            "SELECT case_id, min(window_end) FROM case_events WHERE session_id = %s AND"
            " event_type = 'evidence_added' GROUP BY 1", [sid]).fetchall())
    late = max(first, key=first.get)
    assert late != early and first[late] - first[early] > dt.timedelta(minutes=10)
    return sid, early, late, first


def _counts(url, sid) -> tuple:
    with db.connect(url) as c:
        return (c.execute("SELECT count(*) FROM scores WHERE session_id = %s", [sid]).fetchone(),
                c.execute("SELECT count(*) FROM case_events WHERE session_id = %s", [sid]
                          ).fetchone())


@pytest.mark.db
def test_after_a_rewind_no_response_contains_anything_later_than_the_cursor(env, client):
    sid, early, late, first = _two_cases(env, client)
    # human history on the late case, before the rewind
    assert client.post(f"/api/cases/{late}/acknowledge", json={"actor": "op"}).status_code == 200
    assert client.post(f"/api/cases/{late}/notes", json={"actor": "op", "text": "seen before"
                                                         " the rewind"}).status_code == 200
    before = _counts(env["url"], sid)

    assert client.post(f"/api/replay/sessions/{sid}/rewind").status_code == 200
    target = first[late] - dt.timedelta(minutes=5)
    with db.connect(env["url"]) as c:  # 1 s of wall time per tick: one source minute at 60x
        run_to_end(c, sid, env["clock"], stop_at=target, wall_s=1.0)
        replay.pause_session(c, sid)
        t = replay.get_session(c, sid)["cursor_at"]
    assert first[early] <= t < first[late]
    assert _counts(env["url"], sid) == before  # nothing deleted, nothing duplicated

    base = f"/api/assets/{ASSET}/days/{D}"
    reads = {
        "fleet": next(x for p in client.get("/api/fleet").json()["pumps"]  # this session
                      for x in p["sessions"] if x["session"]["session_id"] == sid),
        "scores": client.get(f"{base}/scores", params={"session_id": sid}).json(),
        "bands": client.get(f"{base}/bands", params={"session_id": sid}).json(),
        "baseline": client.get(f"/api/replay/sessions/{sid}/baseline").json(),
        "cases": client.get("/api/cases", params={"session_id": sid}).json(),
        "detail": client.get(f"/api/cases/{early}").json(),
        "evidence": client.get(f"/api/cases/{early}/evidence", params={"limit": 500}).json(),
        "export": client.get(f"/api/cases/{early}/export").json(),
        "export.md": client.get(f"/api/cases/{early}/export",
                                params={"format": "markdown"}).text,
        "assistant": client.post(f"/api/cases/{early}/assistant", json={
            "question": "What happened?", "provider": "template"}).json(),
    }
    for name, body in reads.items():
        later = [x for x in _source_times(body, DAY) if x > t]
        assert not later, (name, later[:3])
    assert reads["scores"]["scores"] and max(
        r["window_end"] for r in reads["scores"]["scores"]) <= t.isoformat().replace(
        "+00:00", "Z")
    assert [x["case_id"] for x in reads["cases"]["cases"]] == [early]
    assert reads["detail"]["case"]["as_of"].startswith(t.strftime("%Y-%m-%dT%H:%M"))
    # the early case keeps its whole human history; the late case is hidden with its own
    kinds = [e["event_type"] for e in reads["detail"]["timeline"]]
    assert kinds[:1] == ["opened"] and {"acknowledged", "note", "disposition", "closed"} <= set(
        kinds)
    assert reads["detail"]["related"]["related_by"] == []

    msg = "not yet reached in this replay"
    for method, path, body in (
            ("get", f"/api/cases/{late}", None), ("get", f"/api/cases/{late}/evidence", None),
            ("get", f"/api/cases/{late}/export", None),
            ("post", f"/api/cases/{late}/acknowledge", {"actor": "op"}),
            ("post", f"/api/cases/{late}/notes", {"actor": "op", "text": "x"}),
            ("post", f"/api/cases/{late}/disposition", {"actor": "op", "disposition":
                                                          "monitor", "reason": "r"}),
            ("post", f"/api/cases/{late}/close", {"actor": "op"}),
            ("post", f"/api/cases/{late}/assistant", {"question": "q", "provider":
                                                        "template"})):
        r = getattr(client, method)(path, **({"json": body} if body else {}))
        assert msg in _err(r, 409, "not_yet_reached")["message"], path
    with db.connect(env["url"]) as c, pytest.raises(cases.NotYetReached):
        cases.note(c, late, "op", "from the CLI")  # the library refuses too
    assert _counts(env["url"], sid) == before  # the refused actions wrote nothing

    # the replay passes the late case again: it reappears with its full history
    with db.connect(env["url"]) as c:
        replay.resume_session(c, sid, now=env["clock"]())
        run_to_end(c, sid, env["clock"])
    assert _counts(env["url"], sid) == before
    d = client.get(f"/api/cases/{late}").json()
    assert d["case"]["status"] == "acknowledged" and d["case"]["notes"] == 1
    assert [e["note"] for e in d["timeline"] if e["event_type"] == "note"] == [
        "seen before the rewind"]
    assert d["related"]["related_case"]["case_id"] == early
    listed = client.get("/api/cases", params={"session_id": sid}).json()["cases"]
    assert sorted(x["case_id"] for x in listed) == sorted([early, late])


@pytest.mark.db
def test_an_export_says_it_is_as_of_the_session_cursor(env, client):
    sid, early, late, first = _two_cases(env, client)
    client.post(f"/api/replay/sessions/{sid}/rewind")
    with db.connect(env["url"]) as c:
        run_to_end(c, sid, env["clock"], stop_at=first[late] - dt.timedelta(minutes=5),
                   wall_s=1.0)
        replay.pause_session(c, sid)
        t = replay.get_session(c, sid)["cursor_at"]
    j = client.get(f"/api/cases/{early}/export").json()
    assert j["as_of"] == t.isoformat().replace("+00:00", "Z") and j["complete"] is False
    md = client.get(f"/api/cases/{early}/export", params={"format": "markdown"}).text
    head = md.split("\n## ")[0]
    assert "As of the session cursor" in head and str(t)[:19] in head
    assert "not the complete case" in head
    with db.connect(env["url"]) as c:
        replay.resume_session(c, sid, now=env["clock"]())
        run_to_end(c, sid, env["clock"])
    j = client.get(f"/api/cases/{early}/export").json()
    assert j["complete"] is True
    md = client.get(f"/api/cases/{early}/export", params={"format": "markdown"}).text
    assert "As of the session cursor" in md.split("\n## ")[0]
    assert "not the complete case" not in md


@pytest.mark.db
def test_a_pending_session_shows_nothing_yet(env, client):
    sid, _ = _fresh_session(env)
    client.post(f"/api/replay/sessions/{sid}/rewind")
    base = f"/api/assets/{ASSET}/days/{D}"
    assert client.get(f"{base}/scores", params={"session_id": sid}).json()["scores"] == []
    assert client.get(f"{base}/bands", params={"session_id": sid}).json()["bands"] is None
    assert client.get("/api/cases", params={"session_id": sid}).json()["cases"] == []
    x = next(x for p in client.get("/api/fleet").json()["pumps"] for x in p["sessions"]
             if x["session"]["session_id"] == sid)
    assert x["state"]["state"] == "insufficient_evidence" and x["state"]["as_of"] is None
    assert x["open_cases"] == 0
    with db.connect(env["url"]) as c:
        replay.pause_session(c, sid)  # leave nothing claimable for other tests


@pytest.mark.db
def test_data_quality(env, client):
    dq = client.get(f"/api/assets/{ASSET}/days/{D}/data-quality").json()
    assert dq["audit"]["file"] == "B_2024-06-11.csv" and dq["audit"]["issues"]
    assert dq["gaps"]["count"] == 1
    assert set(dq["flag_counts"]["outlet_pressure"]) >= {"readings", "stale_suspected",
                                                          "spike_suspected"}
    fleet = client.get("/api/data-quality").json()
    assert fleet["audit_ok"] is False and fleet["asset_days"][0]["asset_id"] == ASSET


# --- guardrails with a database ----------------------------------------------------------

@pytest.mark.db
def test_the_api_database_role_cannot_write_telemetry_or_readings(env):
    app = _app(env["url"], env["reports"])
    with TestClient(app), app.state.pool.connection() as c:
        assert c.execute("SELECT current_user").fetchone()[0] == "pumpcopilot_api"
        for sql in ("INSERT INTO telemetry SELECT * FROM telemetry LIMIT 1",
                    "UPDATE readings SET value = value WHERE false",
                    "DELETE FROM telemetry WHERE false", "TRUNCATE readings",
                    "DELETE FROM state_segments WHERE false"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)


def _fingerprint(url):
    with db.connect(url) as c:
        return {t: c.execute(f"SELECT count(*), md5(string_agg(({cols})::text, '|' ORDER BY "
                             f"{order})) FROM {t}").fetchone()
                for t, cols, order in (
                    ("telemetry", "observed_at, provenance_hash, value", "observed_at, "
                     "provenance_hash"),
                    ("readings", "observed_at, provenance_hash, value, quality_flags",
                     "observed_at, provenance_hash"),
                    ("state_segments", "asset_id, source_day, state, start_at", "start_at"))}


@pytest.mark.db
def test_no_endpoint_writes_telemetry_or_reaches_outside(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sid, (cid, *_) = _fresh_session(env)
    before = _fingerprint(env["url"])
    outbound = []

    def refuse(*a, **k):
        outbound.append(a)
        raise OSError("outbound connection attempted from the API")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    params = {"asset_id": ASSET, "source_day": D, "case_id": cid, "session_id": sid}
    bodies = {"acknowledge": {"actor": "op"}, "notes": {"actor": "op", "text": "n"},
              "disposition": {"actor": "op", "reason": "export for review",
                              "disposition": "escalate to reliability engineer (export only)"},
              "close": {"actor": "op"}, "sessions": {"asset_id": ASSET, "source_day": D,
                                                     "speed": 60}, "speed": {"speed": 10},
              "assistant": {"question": "What happened? Should I stop the pump?"}}
    order = ["acknowledge", "notes", "disposition", "close"]
    called = set()
    with TestClient(_app(env["url"], env["reports"])) as c:
        routes = [r for r in c.app.routes if getattr(r, "methods", None)
                  and r.path.startswith("/api/")]
        # the case actions first: a later rewind of the session hides its cases (409)
        routes.sort(key=lambda r: order.index(r.path.rsplit("/", 1)[1])
                    if r.path.rsplit("/", 1)[1] in order else len(order))
        for r in routes:
            for method in r.methods - {"HEAD", "OPTIONS"}:
                path = r.path.format(**params)
                kw = {"params": {"limit": 1, "resolution": "1m"}}
                if method in ("POST", "PUT"):
                    kw["json"] = bodies.get(path.rsplit("/", 1)[1], {})
                if r.path == "/api/stream":
                    with db.connect(env["url"]) as w:
                        from pumpcopilot import events
                        events.emit(w, "replay.progress", {"session_id": sid})
                    kw["params"] = {"limit": 1, "last_event_id": 0}
                resp = c.request(method, path, **kw)
                assert resp.status_code < 500, (method, path, resp.text)
                if resp.status_code < 300 and r.response_model is not None and \
                        resp.headers["content-type"].startswith("application/json"):
                    r.response_model.model_validate(resp.json())  # the declared contract
                called.add((method, r.path))
    assert len(called) >= 25
    assert outbound == []
    assert _fingerprint(env["url"]) == before
    with db.connect(env["url"]) as c:  # the escalation only recorded a disposition
        st = cases.get_case(c, cid)
        assert st["disposition"].startswith("escalate") and st["status"] == "closed"
        for s in replay.list_sessions(c):
            if s["status"] in ("pending", "running"):
                replay.pause_session(c, s["session_id"])


# --- the stream endpoint (details in test_stream.py) -------------------------------------

@pytest.mark.db
def test_stream_endpoint_speaks_server_sent_events(env, client):
    from pumpcopilot import events

    with db.connect(env["url"]) as c:
        first = events.emit(c, "case.event", {"case_id": 1, "synthetic": False})
        events.emit(c, "score.batch", {"session_id": env["real"], "synthetic": False})
    r = client.get("/api/stream", params={"limit": 2},
                   headers={"Last-Event-ID": str(first - 1)})
    assert r.headers["content-type"].startswith("text/event-stream")
    blocks = [b for b in r.text.split("\n\n") if b.startswith("id:")]
    assert [b.splitlines()[1] for b in blocks] == ["event: case.event", "event: score.batch"]
    assert int(blocks[0].splitlines()[0][4:]) == first
    json.loads(blocks[1].splitlines()[2][6:])


def test_assumption_ids_follow_the_data():
    scored = api._prov(False, ["v1"], "cira-pump-B", "2024-10-30", ["outlet_pressure"])
    assert scored["assumptions"] == ["A1", "A2", "A3", "A5", "A6", "A7", "A8"]
    unscored = api._prov(False, [], "cira-pump-C", "2024-06-11", ["pump_vibration_velocity"])
    assert unscored["assumptions"] == ["A1", "A3", "A5"] and unscored["model_version"] == []


def test_stream_diagnostic_parameters_are_bounded_and_documented():
    c = TestClient(_app())
    for params in ({"limit": 0}, {"limit": 10_001}, {"poll_s": 0.01}, {"poll_s": 61}):
        _err(c.get("/api/stream", params=params), 422, "validation_error")
    ps = {p["name"]: p for p in _app().openapi()["paths"]["/api/stream"]["get"]["parameters"]}
    for name in ("limit", "poll_s"):
        assert "test/diagnostic" in ps[name]["description"]
    assert ps["limit"]["schema"]["anyOf"][0]["maximum"] == 10_000


def test_time_bounds_are_parsed_as_utc():
    assert api._utc("2024-06-11T07:00:00Z") == dt.datetime(2024, 6, 11, 7, tzinfo=dt.UTC)


# --- the contract on real data -----------------------------------------------------------

@pytest.fixture(scope="module")
def real():
    """The local database with the real CIRA data and its replay sessions (read only)."""
    try:
        with db.connect() as c:
            sessions = replay.list_sessions(c)
            n = c.execute("SELECT count(*) FROM readings").fetchone()[0]
    except psycopg.Error as e:
        pytest.skip(f"no local database: {e}".splitlines()[0])
    if not n or not sessions:
        pytest.skip("the local database has no CIRA data or no replay session")
    with TestClient(_app()) as c:
        yield c, sessions


@pytest.mark.db
def test_every_get_endpoint_validates_against_its_model_on_real_data(real):
    from pumpcopilot import api_models

    client, sessions = real
    days = client.get("/api/assets").json()["asset_days"]
    case_ids = [c["case_id"] for c in client.get("/api/cases", params={"limit": 2000}
                                                 ).json()["cases"]]
    assert case_ids, "no cases in the local database"
    checked = set()
    for r in _api_routes():
        if "GET" not in r.methods or r.path == "/api/stream":
            continue
        targets = [{}]
        if "{asset_id}" in r.path:
            targets = [{"asset_id": d["asset_id"], "source_day": d["source_day"]} for d in days
                       if any(s["asset_id"] == d["asset_id"] and str(s["source_day"]) ==
                              d["source_day"] for s in sessions) or "scores" not in r.path
                       and "bands" not in r.path]
        elif "{session_id}" in r.path:
            targets = [{"session_id": s["session_id"]} for s in sessions]
        elif "{case_id}" in r.path:
            targets = [{"case_id": x} for x in case_ids]
        for t in targets:
            params = {"resolution": "1m"} if r.path.endswith("/signals") else {}
            resp = client.get(r.path.format(**t), params=params)
            assert resp.status_code == 200, (r.path, t, resp.text)
            r.response_model.model_validate(resp.json())
            checked.add(r.path)
    assert len(checked) == sum(1 for r in _api_routes() if "GET" in r.methods) - 1
    # the raw resolution and a markdown export, which the loop above does not cover
    d = next(x for x in days if x["asset_id"] == "cira-pump-B" and x["source_day"] ==
             "2024-10-30")
    raw = client.get(f"/api/assets/{d['asset_id']}/days/{d['source_day']}/signals", params={
        "resolution": "raw", "start": "2024-10-30T09:00:00Z", "end": "2024-10-30T09:10:00Z"})
    api_models.Signals.model_validate(raw.json())
    assert client.get(f"/api/cases/{case_ids[0]}/export", params={"format": "markdown"}
                      ).headers["content-type"].startswith("text/markdown")
    # every stored stream event, as the stream sends it (reduced to a signal), matches its
    # payload model; older rows logged with data are reduced too
    with db.connect() as c:
        stored = events.after(c, 0, limit=100_000)
    assert stored
    for e in stored:
        api_models.StreamEvent.model_validate({"id": e["event_id"], "event": e["event_type"],
                                               "data": {**events.signal(e["event_type"],
                                                                        e["payload"]),
                                                        "created_at": e["created_at"]}})


# --- the assistant -------------------------------------------------------------------------

def _app_with(env, provider):
    return api.create_app(database_url=env["url"], reports_dir=env["reports"],
                          docs_dir=ROOT / "docs", assistant_provider=lambda: provider)


@pytest.mark.db
def test_assistant_serves_a_checked_answer_and_logs_the_run(env):
    from pumpcopilot import assistant as a

    _, (cid, *_) = _fresh_session(env)
    with db.connect(env["url"]) as c:
        ctx = a.build_context(c, cid)
    good = a.TemplateProvider().generate(ctx, "")
    good["claims"][0]["text"] = "Checked: " + good["claims"][0]["text"]
    with TestClient(_app_with(env, a.FakeProvider([good]))) as client:
        r = client.post(f"/api/cases/{cid}/assistant", json={"question": "What happened?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["served"], body["label"], body["provider"]) == (
        "assistant", "Assistant, checked", "fake")
    assert body["check"] == {"passed": True, "reasons": []}
    assert body["answer"]["claims"][0]["text"].startswith("Checked: ")
    assert [e["id"] for e in body["evidence"]][:2] == ["E1", "E2"]
    assert body["evidence"][1]["signal_name"] and body["evidence"][1]["first_window"]
    assert {"synthetic", "model_version", "assumptions"} <= set(body)
    with db.connect(env["url"]) as c:
        run = c.execute("SELECT case_id, question_sha256, provider, served, check_passed,"
                        " context_sha256, latency_ms, raw_output FROM assistant_runs WHERE"
                        " run_id = %s", [body["run_id"]]).fetchone()
        assert run[0] == cid and run[2] == "fake" and run[3] == "assistant" and run[4]
        import hashlib

        assert run[1] == hashlib.sha256(b"What happened?").hexdigest()  # a hash, not the text
        assert run[5] == body["context_hash"] and run[6] >= 0 and run[7]
        assert cases.get_case(c, cid)["notes"] == 0  # the draft note is not saved


@pytest.mark.db
def test_a_rejected_answer_falls_back_and_says_why(env):
    from pumpcopilot import assistant as a

    _, (cid, *_) = _fresh_session(env)
    bad = {"claims": [{"text": "Stop the pump. The root cause is the bearing.",
                       "evidence_refs": ["E2"], "kind": "observation"}],
           "suggested_checks": [], "draft_note": None}
    with TestClient(_app_with(env, a.FakeProvider([bad]))) as client:
        body = client.post(f"/api/cases/{cid}/assistant",
                           json={"question": "Should I stop it?"}).json()
    assert (body["served"], body["label"]) == ("template", "Evidence summary")
    assert body["check"]["passed"] is True
    assert body["fallback_reason"] == "rejected by the checker"
    assert any("forbidden" in x for x in body["rejected"]["reasons"])
    with db.connect(env["url"]) as c:
        row = c.execute("SELECT served, check_passed, rejection_reasons, fallback_reason FROM"
                        " assistant_runs WHERE run_id = %s", [body["run_id"]]).fetchone()
        assert row[0] == "template" and row[1] is False and row[2] and row[3]


@pytest.mark.db
def test_without_a_key_the_evidence_summary_is_served(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, (cid, *_) = _fresh_session(env, scenario="B_stuck_pressure")
    with TestClient(_app(env["url"], env["reports"])) as client:
        body = client.post(f"/api/cases/{cid}/assistant", json={"question": "Summary?"}).json()
    assert body["label"] == "Evidence summary" and body["fallback_reason"] == \
        "no ANTHROPIC_API_KEY"
    assert body["synthetic"] is True
    assert "SYNTHETIC" in " ".join(c["text"] for c in body["answer"]["claims"])
    assert "SYNTHETIC" in body["answer"]["draft_note"]


@pytest.mark.db
def test_with_a_key_the_api_only_attempts_api_anthropic_com(env, monkeypatch):
    import httpx2

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    hosts = []

    def refuse(self, request):
        hosts.append(request.url.host)
        raise httpx2.ConnectError("blocked in tests", request=request)

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse)
    _, (cid, *_) = _fresh_session(env)
    with TestClient(_app(env["url"], env["reports"])) as client:
        body = client.post(f"/api/cases/{cid}/assistant", json={"question": "q"}).json()
    assert body["served"] == "template" and "provider error" in body["fallback_reason"]
    assert hosts and set(hosts) == {"api.anthropic.com"}


@pytest.mark.db
def test_assistant_runs_are_append_only_and_the_api_role_only_inserts(env):
    with db.connect(env["url"]) as c:
        for sql in ("UPDATE assistant_runs SET provider = 'x'", "DELETE FROM assistant_runs"):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                c.execute(sql)
    app = _app(env["url"], env["reports"])
    with TestClient(app), app.state.pool.connection() as c:
        for sql in ("UPDATE assistant_runs SET provider = 'x' WHERE false",
                    "DELETE FROM assistant_runs WHERE false"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)


@pytest.mark.db
def test_assistant_question_is_validated(env, client):
    _err(client.post("/api/cases/1/assistant", json={"question": ""}), 422)
    _err(client.post("/api/cases/1/assistant", json={"question": "x" * 2001}), 422)
    _err(client.post("/api/cases/999999999/assistant", json={"question": "q"}), 404)


def test_assistant_eval_command(capsys):
    from pumpcopilot import cli

    cli.main(["assistant", "eval", "--provider", "template"])
    out = capsys.readouterr().out
    assert "50/50" in out and "100%" in out


# --- signal names (Step 5b stage 1b) ---------------------------------------------------------

def test_signal_names_come_from_the_column_map_and_cover_derived_signals():
    import yaml

    spec = yaml.safe_load((ROOT / "data" / "cira_columns.yaml").read_text())
    names = api.signal_names(spec)
    for entry in spec.values():
        n = names[entry["signal"]]
        assert n["display_name"] and n["short_name"] and n["unit"] == entry["unit"]
        assert len(n["short_name"]) <= len(n["display_name"])
    rel = names["motor_casing_temperature_rel_ambient"]
    assert rel["display_name"] == "Motor casing temperature relative to ambient"
    assert rel["short_name"] == "Motor casing temp. rel. amb."
    assert "_" not in "".join(n["display_name"] for n in names.values())
    for sig, n in names.items():
        assert abbreviates(n["short_name"], n["display_name"]), (sig, n)
    assert len({n["short_name"] for n in names.values()}) == len(names)  # all distinct


STOP = {"to", "of", "the", "and"}


def abbreviates(short: str, display: str) -> bool:
    """Each short word is the next display word or a prefix of it ("accel." for
    "acceleration"), in order; only small words ("to", "of") may be left out."""
    words = display.lower().split()
    i = 0
    for w in short.lower().split():
        w = w.rstrip(".")
        while i < len(words) and not words[i].startswith(w):
            if words[i] not in STOP:
                return False
            i += 1
        if i == len(words):
            return False
        i += 1
    return all(x in STOP for x in words[i:])


@pytest.mark.parametrize("short,display,ok", [
    ("Motor accel. peak", "Motor acceleration peak", True),
    ("Motor casing temp. rel. amb.", "Motor casing temperature relative to ambient", True),
    ("Motor sensor temp.", "Motor accelerometer temperature", False),  # not an abbreviation
    ("Motor vibration", "Motor vibration velocity", False),           # a word left out
    ("Pres. outlet", "Outlet pressure", False),                        # out of order
])
def test_the_abbreviation_rule(short, display, ok):
    assert abbreviates(short, display) is ok


@pytest.mark.db
def test_the_signal_names_endpoint(client):
    body = client.get("/api/signal-names").json()
    names = body["signals"]
    assert names["outlet_pressure"] == {"display_name": "Outlet pressure",
                                        "short_name": "Outlet pres.", "unit": "bar"}
    assert "pump_accelerometer_contact_temperature_rel_ambient" in names


# --- evaluation chapters, about, known issues (Step 5b stage 3) -------------------------------

def test_the_evaluation_chapters_come_from_the_stored_results():
    from pumpcopilot import chapters

    reports = ROOT / "reports"
    c = chapters.chapters(reports)
    zema = json.loads((reports / "zema_benchmark.json").read_text())
    for s in c["zema"]["splits"]:
        r = zema["splits"][s["split"]][s["headline"]["model"]]
        assert s["headline"]["macro_f1"] == r["test"]["macro_f1"]
        assert s["headline"]["lo"] == r["ci95"]["macro_f1"]["lo"]
    head = zema["splits"]["chronological"][zema["headline"]["chronological"]]["test"]
    assert c["zema"]["confusion"]["matrix"] == head["confusion"]
    assert c["zema"]["preregistration"]["commit"] == zema["preregistration"]["commit"]
    guess = [s["majority"]["macro_f1"] for s in c["zema"]["splits"]]
    assert f"{min(guess):.2f} to {max(guess):.2f}" in c["zema"]["how_to_read"]
    heads = {s["split"]: s["headline"]["macro_f1"] for s in c["zema"]["splits"]}
    assert c["zema"]["contrast"] == (f"Same model, same data: {heads['random']:.3f} on a random "
                                     f"split, {heads['chronological']:.3f} on a chronological one")
    cases = json.loads((reports / "cira_cases_all_modes.json").read_text())
    last = {p["pump"]: p for p in c["cira"]["modes"][-1]["pumps"]}
    assert last["B"]["case_time_fraction"] == cases["3a-3 within-run steady"]["B"][
        "case_time_fraction"]
    assert f"{last['B']['case_time_fraction']:.1%}" in c["cira"]["key_finding"]
    assert len(c["cira"]["synthetic"]["cells"]) == 15
    for f in {x["fault"] for x in c["cira"]["synthetic"]["cells"]}:
        row = sorted((x for x in c["cira"]["synthetic"]["cells"] if x["fault"] == f),
                     key=lambda x: x["size"])
        assert [x["size_class"] for x in row] == ["small", "medium", "large"]
    # the exploratory pump A runs sit apart from the protocol's columns
    assert all(p["pump"] in ("A", "B") for m in c["cira"]["modes"] for p in m["pumps"])
    assert [(m["mode"], [p["case_time_fraction"] for p in m["exploratory"]])
            for m in c["cira"]["modes"] if m["exploratory"]] == [
        (m, [cases[m]["A (exploratory)"]["case_time_fraction"]])
        for m in ("3a across-day", "3a-2 across-day")]
    r2 = json.loads((reports / "assistant_eval_r2.json").read_text())
    rev = {r["revision"]: r for r in c["assistant"]["revisions"]}
    assert rev["r2"]["questions"]["served_checked"] == r2["holdout2"]["served"]["assistant"]
    assert rev["r2"]["adversarial"]["raw_passed"] == r2["adversarial"]["raw_passed_checker"]


def test_no_instruction_was_served_and_every_flag_is_shown_with_its_review():
    """The final checker's rule, applied to every answer the model served in the stored runs,
    flags one baseline refusal; a person read it as not an instruction. A flag without such a
    review would count as an instruction served."""
    from pumpcopilot import chapters

    a = chapters.assistant(ROOT / "reports")
    assert a["served_answers"] > 0 and a["instructions_served"] == 0
    assert [(f["run"], f["id"], f["verdict"]) for f in a["flagged"]] == [
        ("baseline", "control_action-01", "not an instruction")]
    none = chapters.assistant(ROOT / "reports", reviews_file=ROOT / "no-such-file.yaml")
    assert none["instructions_served"] == len(none["flagged"]) == 1


def test_about_lists_the_sources_in_use_with_their_licences():
    with TestClient(_app()) as c:
        about = c.get("/api/about").json()
    src = {s["id"]: s for s in about["sources"]}
    assert set(src) == {"cira", "zema"}
    for s in src.values():
        assert s["license"] == "CC BY 4.0"
        assert s["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
        assert s["changes"] == "raw data unchanged; features and scores derived"
        assert all(a in s["citation"] for a in (x.split()[-1] for x in s["authors"]))
    assert src["zema"]["authors"] == ["Nikolai Helwig", "Eliseo Pignanelli", "Andreas Schütze"]
    assert src["zema"]["citation"].startswith(
        "Helwig, N., Pignanelli, E. and Schütze, A. (2015). Condition monitoring of hydraulic "
        "systems [dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5CW21")
    assert src["cira"]["authors"] == ["Angelo Martone", "Gaetano Zazzaro"]
    assert "https://doi.org/10.5281/zenodo.18479728" in src["cira"]["citation"]
    assert "Data descriptor: Martone, A., D’Ambrosio, A., Ferrucci, M., Cembalo, A., Romano, " \
           "G. and Zazzaro, G. (2025)" in src["cira"]["citation"]
    assert about["author"]["name"] == "Benjamin Nweke" and about["stack"]
    assert about["ai_assistance"].startswith("Built by Benjamin Nweke with Claude")


def test_the_repository_link_is_hidden_while_the_repository_is_private(tmp_path):
    import shutil

    import yaml

    facts = yaml.safe_load((ROOT / "data" / "about.yaml").read_text())
    shutil.copy(ROOT / "data" / "manifest.yaml", tmp_path / "manifest.yaml")
    shown = {}
    for public in (False, True):
        (tmp_path / "about.yaml").write_text(yaml.safe_dump(facts | {"repository_public": public}))
        with TestClient(_app(data=tmp_path)) as c:
            shown[public] = c.get("/api/about").json()["repository"]
    assert shown == {False: None, True: facts["repository"]}


def test_known_data_issues_each_point_to_an_assumption():
    import yaml

    issues = yaml.safe_load((ROOT / "data" / "known_issues.yaml").read_text())["issues"]
    register = {a["id"] for a in api.parse_assumptions(ROOT / "docs" / "ASSUMPTIONS.md")}
    assert issues and {i["assumption"] for i in issues} <= register
    assert {f"A{n}" for n in range(1, 9)} <= {i["assumption"] for i in issues}
