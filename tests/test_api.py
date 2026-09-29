"""Step 4b API: contract, rules for every endpoint, case actions, replay control, guardrails."""
import ast
import datetime as dt
import json
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
def test_fleet_overview(env, client):
    body = client.get("/api/fleet").json()
    pump = {p["asset_id"]: p for p in body["pumps"]}[ASSET]
    assert pump["days"] == [D]
    assert pump["state"]["state"] in ("normal", "review_suggested", "insufficient_evidence",
                                      "data_unavailable")
    latest = pump["latest_session"]
    assert latest["session_id"] == env["synthetic"] and latest["synthetic"] is True
    _provenance(pump, synthetic=True)
    assert pump["open_cases"]["latest_session"] >= 1
    assert pump["open_cases"]["all_sessions"]["real"] >= 1
    assert pump["open_cases"]["all_sessions"]["synthetic"] >= 1
    dq = pump["data_quality"]
    assert dq["status"] == "issues" and dq["audit_issues"] == 1 and dq["gaps"] == 1
    assert {"stale_suspected", "spike_suspected"} <= set(dq["flag_counts"])


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
                                                     "speed": 60}, "speed": {"speed": 10}}
    order = ["acknowledge", "notes", "disposition", "close"]
    called = set()
    with TestClient(_app(env["url"], env["reports"])) as c:
        routes = [r for r in c.app.routes if getattr(r, "methods", None)
                  and r.path.startswith("/api/")]
        routes.sort(key=lambda r: order.index(r.path.rsplit("/", 1)[1])
                    if r.path.rsplit("/", 1)[1] in order else -1)
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
    # every stored stream event matches its payload model
    with db.connect() as c:
        stored = events.after(c, 0, limit=100_000)
    assert stored
    for e in stored:
        api_models.StreamEvent.model_validate({"id": e["event_id"], "event": e["event_type"],
                                               "data": {**e["payload"],
                                                        "created_at": e["created_at"]}})
