"""Step 4b live stream: event log with monotonic ids in commit order, LISTEN/NOTIFY delivery,
Last-Event-ID resumption, and the events the worker and case actions emit."""
import json
import threading
import time

import pytest
from conftest import pause_policy_jobs
from fastapi.testclient import TestClient
from replay_helpers import ASSET, DAY, Clock, load_replay_day, run_to_end

from pumpcopilot import api, cases, db, events, replay

pytestmark = pytest.mark.db


def _parse(text):
    out = []
    for block in text.split("\n\n"):
        f = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line
                 and not line.startswith(":"))
        if "id" in f:
            out.append((int(f["id"]), f["event"], json.loads(f["data"])))
    return out


@pytest.fixture
def app_client(conn, db_url, tmp_path):
    pause_policy_jobs(conn)
    with TestClient(api.create_app(database_url=db_url, reports_dir=tmp_path)) as c:
        yield c


def test_event_ids_follow_commit_order(conn, db_url):
    """A writer that took an id first but commits later must not be overtaken: readers that
    resume from the highest id they saw would otherwise miss its event."""
    slow_id, go = [], threading.Event()

    def slow_writer():
        with db.connect(db_url) as c, c.transaction():
            slow_id.append(events.emit(c, "case.event", {"n": 1}))
            go.set()
            time.sleep(0.5)

    t = threading.Thread(target=slow_writer)
    t.start()
    go.wait()
    t0 = time.monotonic()
    fast = events.emit(conn, "case.event", {"n": 2})  # waits for the slow commit
    t.join()
    assert time.monotonic() - t0 > 0.3
    assert fast > slow_id[0]
    ids = [e["event_id"] for e in events.after(conn, 0)]
    assert ids == sorted(ids) and len(ids) == 2


def test_stream_resumes_after_last_event_id(conn, app_client):
    ids = [events.emit(conn, "replay.progress", {"i": i}) for i in range(5)]
    got = _parse(app_client.get("/api/stream", params={"limit": 3},
                                headers={"Last-Event-ID": str(ids[1])}).text)
    assert [g[0] for g in got] == ids[2:]
    # the query parameter works too (a first connection cannot set headers with EventSource)
    got = _parse(app_client.get("/api/stream", params={"limit": 1,
                                                       "last_event_id": ids[3]}).text)
    assert [g[0] for g in got] == ids[4:]


def test_stream_filters_by_type_and_session(conn, app_client):
    events.emit(conn, "replay.progress", {"session_id": 1})
    b = events.emit(conn, "score.batch", {"session_id": 2})
    c = events.emit(conn, "case.event", {"session_id": 2})
    got = _parse(app_client.get("/api/stream", params={
        "limit": 2, "last_event_id": 0, "session_id": 2}).text)
    assert [g[0] for g in got] == [b, c]
    got = _parse(app_client.get("/api/stream", params={
        "limit": 1, "last_event_id": 0, "types": "case.event"}).text)
    assert [g[0] for g in got] == [c]


def test_stream_delivers_new_events_through_notify(conn, db_url, app_client):
    start = events.emit(conn, "replay.progress", {"i": "before"})
    result = {}

    def listen():
        r = app_client.get("/api/stream", params={"limit": 2, "last_event_id": start,
                                                  "poll_s": 30})
        result["events"] = _parse(r.text)

    t = threading.Thread(target=listen)
    t0 = time.monotonic()
    t.start()
    time.sleep(0.5)  # the client is waiting on LISTEN
    with db.connect(db_url) as w:
        a = events.emit(w, "score.batch", {"i": 1})
        b = events.emit(w, "case.event", {"i": 2})
    t.join(timeout=10)
    assert [e[0] for e in result["events"]] == [a, b]
    assert time.monotonic() - t0 < 5  # delivered by NOTIFY, not by the 30 s poll


def test_worker_and_case_actions_emit_events(conn, tmp_path):
    pause_policy_jobs(conn)
    limits = load_replay_day(conn, tmp_path)
    sid = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    run_to_end(conn, sid, Clock())
    evs = events.after(conn, 0, limit=100_000)
    types = {e["event_type"] for e in evs}
    assert {"replay.progress", "score.batch", "case.event"} <= types
    batches = [e["payload"] for e in evs if e["event_type"] == "score.batch"]
    assert batches and all(b == {"session_id": sid, "asset_id": ASSET, "source_day": str(DAY),
                                 "synthetic": False} for b in batches)
    progress = [e for e in evs if e["event_type"] == "replay.progress"]
    assert len(progress) >= 2
    cid = cases.list_cases(conn, session_id=sid)[0]["case_id"]
    last = evs[-1]["event_id"]
    cases.acknowledge(conn, cid, actor="op")
    new = events.after(conn, last)
    assert [(e["event_type"], e["payload"]["case_id"], e["payload"]["event_type"])
            for e in new] == [("case.event", cid, "acknowledged")]
    other = replay.create_session(conn, ASSET, DAY, speed=60, stale_limits=limits)
    replay.pause_session(conn, other)
    assert [(e["event_type"], e["payload"]["session_id"]) for e in events.after(
        conn, new[-1]["event_id"])] == [("replay.progress", other)] * 2


def test_event_payloads_are_signals_only(conn, tmp_path):
    """Stream events say what changed, never the data: identifiers and types only, so a page
    can only refetch, and a replayed old event (after a rewind) carries nothing to show."""
    pause_policy_jobs(conn)
    limits = load_replay_day(conn, tmp_path)
    sid = replay.create_session(conn, ASSET, DAY, speed=60, scenario="B_stuck_pressure",
                                stale_limits=limits)
    run_to_end(conn, sid, Clock())
    cid = cases.list_cases(conn, session_id=sid)[0]["case_id"]
    cases.acknowledge(conn, cid, actor="op")
    cases.note(conn, cid, actor="op", text="a note that must not travel")
    replay.rewind_session(conn, sid)
    base = {"session_id", "asset_id", "source_day", "synthetic"}
    allowed = {"replay.progress": base, "score.batch": base,
               "case.event": base | {"case_id", "event_type"}}
    evs = events.after(conn, 0, limit=100_000)
    assert {e["event_type"] for e in evs} == set(allowed)
    for e in evs:
        assert set(e["payload"]) == allowed[e["event_type"]], e
        assert e["payload"]["synthetic"] is True
    assert "must not travel" not in json.dumps([e["payload"] for e in evs])
    # emit() itself strips anything else a caller passes
    eid = events.emit(conn, "score.batch", {"session_id": sid, "asset_id": ASSET,
                                            "source_day": str(DAY), "synthetic": True,
                                            "count": 5, "states": {"normal": 5}})
    assert events.after(conn, eid - 1)[0]["payload"] == {
        "session_id": sid, "asset_id": ASSET, "source_day": str(DAY), "synthetic": True}


def test_the_stream_sends_old_logged_events_as_signals_too(conn, db_url, tmp_path):
    """Events logged before payloads were reduced still go out with identifiers only."""
    from psycopg.types.json import Jsonb

    eid = conn.execute(
        "INSERT INTO stream_events (event_type, payload) VALUES ('score.batch', %s) RETURNING"
        " event_id", [Jsonb({"session_id": 1, "asset_id": ASSET, "source_day": str(DAY),
                             "synthetic": False, "count": 9, "window_end_last":
                             "2024-06-11 09:59:00+00:00", "states": {"review_suggested": 9}})]
    ).fetchone()[0]
    with TestClient(api.create_app(database_url=db_url, reports_dir=tmp_path)) as c:
        r = c.get("/api/stream", params={"last_event_id": eid - 1, "limit": 1})
    (got,) = _parse(r.text)
    assert got[0] == eid and set(got[2]) == {"session_id", "asset_id", "source_day",
                                             "synthetic", "created_at"}


def test_real_server_reconnects_with_last_event_id(conn, db_url, tmp_path):
    """Over a real socket (uvicorn on 127.0.0.1): read two events, drop the connection, emit
    more, reconnect with Last-Event-ID and get exactly the rest."""
    import httpx
    import uvicorn

    pause_policy_jobs(conn)
    app = api.create_app(database_url=db_url, reports_dir=tmp_path)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/api/stream"
    try:
        ids = [events.emit(conn, "score.batch", {"i": i}) for i in range(2)]
        seen = []
        with httpx.stream("GET", url, params={"last_event_id": ids[0] - 1}, timeout=10) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                if line.startswith("id: "):
                    seen.append(int(line[4:]))
                if len(seen) == 2:
                    break  # the browser goes away
        more = [events.emit(conn, "case.event", {"i": i}) for i in range(3)]
        r = httpx.get(url, params={"limit": 3}, headers={"Last-Event-ID": str(seen[-1])},
                      timeout=10)
        assert seen == ids and [e[0] for e in _parse(r.text)] == more
    finally:
        server.should_exit = True
        th.join(timeout=10)
