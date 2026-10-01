"""Step 4b: the read-mostly HTTP API and the live stream (`pumpcopilot api`).

* Bound to 127.0.0.1, no auth, CORS for a local frontend dev server only.
* Runs as the database role `pumpcopilot_api` (migration 0008): it can read everything and
  write only replay control, case events and stream events. No endpoint can write telemetry
  or readings, and nothing here opens a connection outside the app and its database;
  escalation is a disposition plus an export.
* Telemetry is only read through db.QUERY_FUNCTIONS, which are all scoped by
  (asset_id, source_day).
* Responses that carry scores or cases say whether the data is synthetic, which model
  versions produced it and which assumptions (docs/ASSUMPTIONS.md) apply.
* Every read scoped to a replay session is as of that session's cursor (source time):
  scores, states and cases later than the cursor are not returned (after a rewind they
  reappear as the replay passes them; nothing is deleted). A case the cursor has not reached
  can be neither read nor acted on (409 not_yet_reached).
* Errors have one shape: {"error": {"status", "code", "message", "details"}}.
* /api/stream is server-sent events (replay.progress, score.batch, case.event) from the
  stream_events log, woken by LISTEN/NOTIFY; Last-Event-ID resumes without gaps.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Annotated, Literal

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import api_models as M
from . import assistant, cases, db, events, replay, scoring

ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
API_ROLE = "pumpcopilot_api"
CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173",
                "http://localhost:3000", "http://127.0.0.1:3000"]
WORKER_STALE_S = 10.0
RAW_MAX = dt.timedelta(hours=3)
STATES = ("review_suggested", "insufficient_evidence", "data_unavailable", "normal")
MODES = {"3a": ("cira_scoring_eval.json", "3a: first protocol, tuned on B June only"),
         "3a-2": ("cira_scoring_eval_3a2.json", scoring.REVISION_LABEL),
         "3a-3": ("cira_scoring_eval_3a3.json", scoring.PREREG_LABEL)}


# --- errors ------------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


class ErrorBody(BaseModel):
    status: int
    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": jsonable_encoder(
        {"status": status, "code": code, "message": message, "details": details})})


CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 409: "conflict",
         422: "validation_error", 503: "unavailable"}
ERRORS = {s: {"model": ErrorResponse} for s in (404, 409, 422)}


# --- request bodies ----------------------------------------------------------------------

class Actor(BaseModel):
    actor: str = Field(min_length=1, description="who is acting")


class NoteIn(Actor):
    text: str = Field(min_length=1)


class DispositionIn(Actor):
    disposition: Literal[cases.DISPOSITIONS]  # type: ignore[valid-type]
    reason: str = Field(min_length=1, description="required")


class SessionIn(BaseModel):
    asset_id: str
    source_day: dt.date
    speed: Literal[1, 10, 60] = 60
    scenario: str | None = Field(None, description="synthetic scenario, applied in memory")


class SpeedIn(BaseModel):
    speed: Literal[1, 10, 60]


# --- helpers -----------------------------------------------------------------------------

def _utc(t) -> dt.datetime:
    t = dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")) if isinstance(t, str) else t
    return t.replace(tzinfo=dt.UTC) if t.tzinfo is None else t.astimezone(dt.UTC)


def assumptions_for(asset_id: str | None, source_day=None, signals=(), scored: bool = True
                    ) -> list[str]:
    """Assumption IDs (docs/ASSUMPTIONS.md) that apply to CIRA data, scores and cases."""
    ids = {"A1", "A3", "A5"}
    if scored:
        ids |= {"A7", "A8"}
    if not signals or any(str(s).startswith(("outlet_pressure", "motor_casing_temperature"))
                          for s in signals):
        ids.add("A2")
    if asset_id == "cira-pump-B" and str(source_day) == "2024-10-30":
        ids.add("A6")
    return sorted(ids, key=lambda a: int(a[1:]))


def _prov(synthetic: bool, versions, asset_id, source_day, signals=()) -> dict:
    """Provenance for scores and cases; the scoring assumptions apply only if there are any."""
    versions = sorted({v for v in versions if v})
    return {"synthetic": bool(synthetic), "model_version": versions,
            "assumptions": assumptions_for(asset_id, source_day, signals, scored=bool(versions))}


def _rows(conn, sql: str, args=()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, list(args)).fetchall()


def _one(conn, sql: str, args=()) -> dict | None:
    rows = _rows(conn, sql, args)
    return rows[0] if rows else None


def _require_asset_day(conn, asset_id: str, source_day: dt.date) -> None:
    if not _one(conn, "SELECT 1 FROM state_segments WHERE asset_id = %s AND source_day = %s"
                      " LIMIT 1", [asset_id, source_day]):
        raise ApiError(404, "not_found", f"no stored day {source_day} for {asset_id}")


def _session(conn, session_id: int) -> dict:
    try:
        return replay.get_session(conn, session_id)
    except ValueError:
        raise ApiError(404, "not_found", f"no replay session {session_id}") from None


def _session_out(s: dict) -> dict:
    keep = ("session_id", "asset_id", "source_day", "speed", "status", "cursor_at",
            "source_start", "source_end", "scenario", "synthetic", "config_name",
            "config_sha256", "anchor_cursor", "anchor_wall", "claimed_by", "heartbeat_at",
            "error", "created_at", "day_constants")
    return {k: s[k] for k in keep}


def _progress_at(s: dict) -> dict | None:
    """The stored baseline progress, if it was computed at or before the session cursor (a
    rewind leaves the later snapshot in place until the replay passes it again)."""
    p = s.get("baseline_progress")
    if not p or s.get("cursor_at") is None or "cursor" not in p:
        return None
    return p if _utc(p["cursor"]) <= _utc(s["cursor_at"]) else None


def aggregate_state(states: list[str]) -> str:
    """A session's state from its signals' latest states at the cursor."""
    if not states:
        return "insufficient_evidence"
    return ("review_suggested" if "review_suggested" in states else "normal"
            if set(states) == {"normal"} else "insufficient_evidence"
            if "insufficient_evidence" in states else "data_unavailable")


def signal_names(column_map: dict) -> dict[str, dict]:
    """The names people see, per signal id (data/cira_columns.yaml), with the signals scored
    relative to ambient named by rule: "<display_name> relative to ambient"."""
    out = {}
    for spec in column_map.values():
        n = {"display_name": spec["display_name"], "short_name": spec["short_name"],
             "unit": spec["unit"]}
        out[spec["signal"]] = n
        if "temperature" in spec["signal"] and not spec["signal"].startswith("ambient"):
            out[f"{spec['signal']}_rel_ambient"] = {
                "display_name": f"{n['display_name']} relative to ambient",
                "short_name": f"{n['short_name']} rel. amb.",
                "unit": "K"}  # a temperature difference: one form, K, everywhere
    return dict(sorted(out.items()))


def _versions(conn, session_id: int) -> list[str]:
    return [r["model_version"] for r in _rows(
        conn, "SELECT DISTINCT model_version FROM scores WHERE session_id = %s", [session_id])]


def _audit(reports: Path) -> dict | None:
    f = reports / "cira_audit.json"
    return json.loads(f.read_text()) if f.exists() else None


def _audit_file(audit: dict | None, asset_id: str, source_day) -> dict | None:
    if not audit:
        return None
    name = f"{asset_id.rsplit('-', 1)[-1]}_{source_day}.csv"
    return next((f for f in audit.get("files", []) if f.get("file") == name), None)


def parse_assumptions(path: Path) -> list[dict]:
    """docs/ASSUMPTIONS.md as data: id, title and the four standard parts."""
    text = path.read_text()
    parts = {"Assumption.": "assumption", "Evidence.": "evidence",
             "Impact if wrong.": "impact_if_wrong", "How to revisit.": "how_to_revisit"}
    out = []
    for block in re.split(r"\n(?=## A\d+:)", text)[1:]:
        head, _, body = block.partition("\n")
        m = re.match(r"## (A\d+): (.*)", head)
        item = {"id": m[1], "title": m[2].strip(), **{v: "" for v in parts.values()}}
        key = None
        for line in body.split("\n"):
            hit = next((k for k in parts if line.startswith(f"**{k}**")), None)
            if hit:
                key = parts[hit]
                line = line[len(hit) + 4:].strip()
            if line.strip() == "---":
                key = None
                continue
            if key:
                item[key] += line + "\n"
        out.append({k: v.strip() if isinstance(v, str) else v for k, v in item.items()})
    return out


def _actions(status: str) -> list[str]:
    return {"open": ["acknowledge", "note"], "acknowledged": ["note", "disposition"],
            "dispositioned": ["note", "disposition", "close", "export"],
            "closed": ["export"]}[status]


def _case_versions(conn, case_ids) -> list[str]:
    return [r["model_version"] for r in _rows(
        conn, "SELECT DISTINCT model_version FROM case_events WHERE case_id = ANY(%s) AND"
              " event_type = 'evidence_added'", [list(case_ids)])]


# --- the app -----------------------------------------------------------------------------

def citation(src: dict) -> str:
    """A source's citation from its manifest metadata: the dataset, then its data descriptor
    (APA-like; authors as family name and initials)."""
    def names(authors: list[dict]) -> str:
        n = [f"{a['family']}, {'. '.join(g[0] for g in a['given'].split())}." for a in authors]
        return n[0] if len(n) == 1 else f"{', '.join(n[:-1])} and {n[-1]}"

    version = f" (version {src['version']})" if src.get("version") else ""
    text = (f"{names(src['authors'])} ({src['year']}). {src['cited_title']}{version} [dataset]. "
            f"{src['publisher']}. https://doi.org/{src['doi']}")
    if d := src.get("descriptor"):
        text += (f". Data descriptor: {names(d['authors'])} ({d['year']}). {d['title']}. "
                 f"{d['journal']} {d['volume']}({d['issue']}), {d['article']}. "
                 f"https://doi.org/{d['doi']}")
    return text


def create_app(database_url: str | None = None, reports_dir: Path | None = None,
               docs_dir: Path | None = None, cors_origins: list[str] | None = None,
               role: str = API_ROLE, data_dir: Path | None = None,
               assistant_provider=None) -> FastAPI:
    url = database_url or db.database_url()
    reports = Path(reports_dir or ROOT / "reports")
    docs = Path(docs_dir or ROOT / "docs")
    data = Path(data_dir or ROOT / "data")
    provider_for = assistant_provider or (lambda: assistant.AnthropicProvider())

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = ConnectionPool(url, min_size=1, max_size=8, open=False,
                              kwargs={"autocommit": True},
                              configure=lambda c: c.execute(f"SET ROLE {role}"))
        pool.open(wait=True, timeout=10)
        app.state.pool = pool
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(title="pump-copilot API", version="4b",
                  description="Read-only pump decision support on public CIRA data. No alarm "
                              "states, no control actions; escalation is export-only.",
                  lifespan=lifespan, responses=ERRORS)
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins or CORS_ORIGINS,
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["*"],
                       expose_headers=["*"])

    @app.exception_handler(ApiError)
    async def _api_error(_, e: ApiError):
        return _error(e.status, e.code, e.message, e.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_, e: StarletteHTTPException):
        return _error(e.status_code, CODES.get(e.status_code, "http_error"), str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_, e: RequestValidationError):
        return _error(422, "validation_error", "the request is not valid", e.errors())

    @app.exception_handler(cases.InvalidTransition)
    async def _transition(_, e: cases.InvalidTransition):
        msg = str(e).removeprefix("invalid case transition: ")
        if "does not exist" in msg:
            return _error(404, "not_found", msg)
        return _error(409, "invalid_transition", msg)

    @app.exception_handler(cases.NotYetReached)
    async def _not_yet_reached(_, e: cases.NotYetReached):
        return _error(409, "not_yet_reached", str(e))

    @app.exception_handler(Exception)
    async def _internal(_, e: Exception):
        return _error(500, "internal_error", "internal error", type(e).__name__)

    @contextlib.contextmanager
    def conn_for(request: Request):
        with request.app.state.pool.connection() as c:
            yield c

    # -- health --

    @app.get("/api/health", tags=["health"], response_model=M.Health)
    def health(request: Request):
        out = {"status": "down", "database": {"ok": False}, "worker": {"alive": False,
                                                                        "workers": []}}
        try:
            with request.app.state.pool.connection(timeout=2) as c:
                t0 = time.perf_counter()
                c.execute("SELECT 1")
                applied = {r["filename"] for r in _rows(c, "SELECT filename FROM"
                                                           " schema_migrations")}
                pending = sorted(p.name for p in db.MIGRATIONS.glob("*.sql")
                                 if p.name not in applied)
                out["database"] = {"ok": True, "latency_ms": round(
                    (time.perf_counter() - t0) * 1000, 2), "pending_migrations": pending}
                workers = _rows(c, "SELECT worker_id, host, pid, status, started_at, last_seen,"
                                   " sessions_stepped, last_session_id, extract(epoch FROM"
                                   " clock_timestamp() - last_seen) AS age_s FROM"
                                   " worker_heartbeats ORDER BY last_seen DESC")
        except Exception as e:
            out["database"]["error"] = type(e).__name__
            return JSONResponse(status_code=503, content=jsonable_encoder(out))
        for w in workers:
            w["age_s"] = float(w["age_s"])
            w["alive"] = w["status"] == "running" and w["age_s"] <= WORKER_STALE_S
        alive = any(w["alive"] for w in workers)
        out["worker"] = {"alive": alive, "stale_after_s": WORKER_STALE_S, "workers": workers}
        out["status"] = "ok" if alive and not pending else "degraded"
        return out

    # -- fleet overview --

    def _session_state(c, sess: dict) -> dict:
        """One session as the fleet shows it: its state, open cases and provenance, all as
        of its cursor."""
        sid, cursor = sess["session_id"], sess["cursor_at"]
        latest = _rows(c, "SELECT DISTINCT ON (signal_name) signal_name,"
                          " presentation_state AS state, window_end, model_version"
                          " FROM scores WHERE session_id = %s AND window_end <= %s"
                          " ORDER BY signal_name, window_end DESC", [sid, cursor])
        st = [x["state"] for x in latest]
        if cursor is None:
            state = {"state": "insufficient_evidence", "reason": "replay not started",
                     "as_of": None}
        elif not st:
            state = {"state": "insufficient_evidence", "reason": "no scores yet",
                     "as_of": cursor}
        else:
            state = {"state": aggregate_state(st), "reason": None, "as_of": cursor}
        state["signals"] = {x["signal_name"]: x["state"] for x in latest}
        n_open = _one(c, "SELECT count(*) AS n FROM case_state_visible WHERE session_id = %s"
                         " AND status <> 'closed'", [sid])["n"]
        return {"session": {k: sess[k] for k in ("session_id", "source_day", "status",
                                                  "cursor_at", "speed", "synthetic",
                                                  "scenario")},
                "state": state, "open_cases": n_open,
                **_prov(sess["synthetic"], [x["model_version"] for x in latest],
                        sess["asset_id"], sess["source_day"], [x["signal_name"] for x in latest])}

    @app.get("/api/fleet", tags=["fleet"], response_model=M.Fleet)
    def fleet(request: Request):
        """Each pump with every replay session reported separately (synthetic ones included),
        each with its state computed here, as of its cursor. The pump's own state is its
        latest real session's: a synthetic session never stands for the pump."""
        audit = _audit(reports)
        pumps = []
        with conn_for(request) as c:
            for a in _rows(c, "SELECT asset_id, array_agg(DISTINCT source_day ORDER BY"
                              " source_day) AS days FROM state_segments GROUP BY asset_id"
                              " ORDER BY asset_id"):
                asset, days = a["asset_id"], a["days"]
                sessions = [_session_state(c, x) for x in _rows(
                    c, "SELECT * FROM replay_sessions WHERE asset_id = %s ORDER BY"
                       " session_id DESC", [asset])]
                real = next((x for x in sessions if not x["session"]["synthetic"]), None)
                if real:
                    state, day = real["state"], real["session"]["source_day"]
                else:
                    state = {"state": "data_unavailable", "reason": "no real replay session",
                             "as_of": None, "signals": {}}
                    day = sessions[0]["session"]["source_day"] if sessions else days[-1]
                n_open = {"real": sum(x["open_cases"] for x in sessions
                                      if not x["session"]["synthetic"]),
                          "synthetic": sum(x["open_cases"] for x in sessions
                                           if x["session"]["synthetic"])}
                flags = db.fetch_flag_counts(c, asset, day)
                totals = {k: sum(v.get(k, 0) for v in flags.values())
                          for k in ("readings", "stale_suspected", "spike_suspected")}
                af = _audit_file(audit, asset, day)
                issues = len(af.get("issues", [])) if af else None
                gaps = (af.get("gaps_over_factor") or {}).get("count", 0) if af else None
                pumps.append({
                    "asset_id": asset, "days": [str(d) for d in days],
                    "state": state,
                    "state_session_id": real["session"]["session_id"] if real else None,
                    "sessions": sessions, "open_cases": n_open,
                    "data_quality": {"source_day": str(day), "status": "issues" if (
                        issues or gaps) else "ok" if af else "not_audited",
                        "audit_issues": issues, "gaps": gaps, "flag_counts": totals},
                    "synthetic": False,
                    "model_version": real["model_version"] if real else [],
                    "assumptions": real["assumptions"] if real else assumptions_for(
                        asset, day, scored=False)})
        return {"pumps": pumps}

    # -- asset-day view --

    @app.get("/api/assets", tags=["asset-day"], response_model=M.AssetDays)
    def assets(request: Request):
        with conn_for(request) as c:
            rows = _rows(c, "SELECT asset_id, source_day, min(start_at) AS start_at,"
                            " max(end_at) AS end_at, sum(extract(epoch FROM end_at - start_at))"
                            " FILTER (WHERE state = 'running') / 3600.0 AS running_hours"
                            " FROM state_segments GROUP BY 1, 2 ORDER BY 1, 2")
        return {"asset_days": rows}

    @app.get("/api/assets/{asset_id}/days/{source_day}", tags=["asset-day"],
             response_model=M.AssetDaySummary)
    def asset_day(request: Request, asset_id: str, source_day: dt.date):
        with conn_for(request) as c:
            _require_asset_day(c, asset_id, source_day)
            sessions = _rows(c, "SELECT session_id, status, speed, synthetic, scenario,"
                                " cursor_at FROM replay_sessions WHERE asset_id = %s AND"
                                " source_day = %s ORDER BY session_id", [asset_id, source_day])
            signals = sorted({r["signal_name"] for r in db.fetch_day_1m(c, asset_id,
                                                                        source_day)})
        return {"asset_id": asset_id, "source_day": str(source_day), "signals": signals,
                "sessions": sessions,
                "assumptions": assumptions_for(asset_id, source_day, signals, scored=False)}

    @app.get("/api/assets/{asset_id}/days/{source_day}/signals", tags=["asset-day"],
             response_model=M.Signals)
    def signals(request: Request, asset_id: str, source_day: dt.date,
                resolution: Literal["1m", "raw"] = "1m",
                signal: Annotated[list[str] | None, Query()] = None,
                start: dt.datetime | None = None, end: dt.datetime | None = None):
        with conn_for(request) as c:
            _require_asset_day(c, asset_id, source_day)
            out: dict[str, list] = {}
            if resolution == "1m":
                for r in db.fetch_day_1m(c, asset_id, source_day, signal):
                    out.setdefault(r["signal_name"], []).append({
                        "bucket": r["bucket"], "min": r["min_value"], "max": r["max_value"],
                        "mean": r["mean_value"], "samples": r["sample_count"],
                        "readings": r["reading_count"]})
            else:
                if start is None:
                    raise ApiError(422, "validation_error", "raw resolution needs start"
                                   f" (and end, at most {RAW_MAX} after it)")
                lo = _utc(start)
                hi = _utc(end) if end else lo + dt.timedelta(hours=1)
                if hi <= lo or hi - lo > RAW_MAX:
                    raise ApiError(422, "validation_error",
                                   f"raw window must be positive and at most {RAW_MAX}")
                try:
                    rows = db.fetch_raw(c, asset_id, source_day, lo, hi, signal)
                except ValueError as e:
                    raise ApiError(422, "validation_error", str(e)) from None
                for r in rows:
                    out.setdefault(r["signal_name"], []).append({
                        "t": r["observed_at"], "value": r["value"],
                        "state": r["operating_state"], "flags": r["quality_flags"],
                        "is_reading": r["is_reading"]})
        return {"asset_id": asset_id, "source_day": str(source_day), "resolution": resolution,
                "signals": out, "assumptions": assumptions_for(asset_id, source_day, list(out),
                                                               scored=False)}

    @app.get("/api/assets/{asset_id}/days/{source_day}/segments", tags=["asset-day"],
             response_model=M.Segments)
    def segments(request: Request, asset_id: str, source_day: dt.date):
        with conn_for(request) as c:
            _require_asset_day(c, asset_id, source_day)
            segs = db.fetch_segments(c, asset_id, source_day)
        return {"asset_id": asset_id, "source_day": str(source_day), "segments": segs,
                "assumptions": assumptions_for(asset_id, source_day, scored=False)}

    def _day_session(c, asset_id, source_day, session_id) -> dict:
        if session_id is not None:
            s = _session(c, session_id)
            if (s["asset_id"], s["source_day"]) != (asset_id, source_day):
                raise ApiError(404, "not_found", f"session {session_id} is not for "
                                                 f"{asset_id} {source_day}")
            return s
        s = _one(c, "SELECT * FROM replay_sessions WHERE asset_id = %s AND source_day = %s"
                    " ORDER BY synthetic, (status = 'completed') DESC, session_id DESC LIMIT 1",
                 [asset_id, source_day])
        if s is None:
            raise ApiError(404, "not_found", f"no replay session for {asset_id} {source_day}")
        return s

    @app.get("/api/assets/{asset_id}/days/{source_day}/scores", tags=["asset-day"],
             response_model=M.Scores)
    def scores(request: Request, asset_id: str, source_day: dt.date,
               session_id: int | None = None,
               signal: Annotated[list[str] | None, Query()] = None):
        with conn_for(request) as c:
            _require_asset_day(c, asset_id, source_day)
            s = _day_session(c, asset_id, source_day, session_id)
            q = ("SELECT signal_name, stretch, window_start, window_end, presentation_state AS"
                 " state, score, abstention_reason, median, band_low, band_high, model_id,"
                 " model_version, synthetic FROM scores WHERE session_id = %s AND asset_id = %s"
                 " AND source_day = %s AND window_end <= %s")  # as of the cursor
            args = [s["session_id"], asset_id, source_day, s["cursor_at"]]
            if signal:
                q, args = q + " AND signal_name = ANY(%s)", [*args, signal]
            rows = _rows(c, q + " ORDER BY signal_name, window_start", args)
        return {"asset_id": asset_id, "source_day": str(source_day),
                "session_id": s["session_id"], "scores": rows,
                **_prov(s["synthetic"], [r["model_version"] for r in rows], asset_id,
                        source_day, {r["signal_name"] for r in rows})}

    @app.get("/api/assets/{asset_id}/days/{source_day}/bands", tags=["asset-day"],
             response_model=M.Bands)
    def bands(request: Request, asset_id: str, source_day: dt.date,
              session_id: int | None = None):
        with conn_for(request) as c:
            _require_asset_day(c, asset_id, source_day)
            s = _day_session(c, asset_id, source_day, session_id)
            versions = _versions(c, s["session_id"])
        return {"asset_id": asset_id, "source_day": str(source_day),
                "session_id": s["session_id"], "bands": _progress_at(s),
                **_prov(s["synthetic"], versions, asset_id, source_day,
                        (s["day_constants"] or {}).get("signals", []))}

    @app.get("/api/assets/{asset_id}/days/{source_day}/data-quality", tags=["data-quality"],
             response_model=M.AssetDayQuality)
    def asset_day_quality(request: Request, asset_id: str, source_day: dt.date):
        audit = _audit(reports)
        af = _audit_file(audit, asset_id, source_day)
        with conn_for(request) as c:
            _require_asset_day(c, asset_id, source_day)
            flags = db.fetch_flag_counts(c, asset_id, source_day)
        site = [g for g in (audit or {}).get("site_gaps", []) if str(source_day) in json.dumps(
            g, default=str)]
        return {"asset_id": asset_id, "source_day": str(source_day),
                "audit": None if af is None else {k: af.get(k) for k in (
                    "file", "rows", "blank_rows", "issues", "span", "cadence_segments",
                    "timestamp_assumption", "duplicate_timestamps", "non_monotonic_steps")},
                "gaps": (af or {}).get("gaps_over_factor"), "site_gaps": site,
                "flag_counts": flags,
                "assumptions": assumptions_for(asset_id, source_day, list(flags),
                                               scored=False)}

    @app.get("/api/data-quality", tags=["data-quality"], response_model=M.DataQuality)
    def data_quality(request: Request):
        audit = _audit(reports)
        rows = []
        with conn_for(request) as c:
            for a in _rows(c, "SELECT DISTINCT asset_id, source_day FROM state_segments ORDER"
                              " BY 1, 2"):
                flags = db.fetch_flag_counts(c, a["asset_id"], a["source_day"])
                af = _audit_file(audit, a["asset_id"], a["source_day"])
                rows.append({"asset_id": a["asset_id"], "source_day": str(a["source_day"]),
                             "audit_issues": None if af is None else len(af.get("issues", [])),
                             "gaps": None if af is None else (af.get("gaps_over_factor") or {}
                                                              ).get("count", 0),
                             "flag_counts": {k: sum(v.get(k, 0) for v in flags.values())
                                             for k in ("readings", "stale_suspected",
                                                       "spike_suspected")}})
        import yaml

        issues = yaml.safe_load((data / "known_issues.yaml").read_text())["issues"] \
            if (data / "known_issues.yaml").exists() else []
        return {"known_issues": issues, "audit_ok": None if audit is None else audit.get("ok"),
                "audit_issues": None if audit is None else audit.get("issues", []),
                "asset_days": rows}

    # -- cases --

    @app.get("/api/cases", tags=["cases"], response_model=M.CaseList)
    def case_list(request: Request, session_id: int | None = None,
                  asset_id: str | None = None, source_day: dt.date | None = None,
                  status: Literal["open", "acknowledged", "dispositioned", "closed"] | None =
                  None, synthetic: bool | None = None, limit: int = Query(200, ge=1, le=2000)):
        conds, args = [], []
        for col, val in (("session_id", session_id), ("asset_id", asset_id),
                         ("source_day", source_day), ("status", status),
                         ("synthetic", synthetic)):
            if val is not None:
                conds.append(f"{col} = %s")
                args.append(val)
        where = f" WHERE {' AND '.join(conds)}" if conds else ""
        with conn_for(request) as c:
            rows = _rows(c, f"SELECT * FROM case_state_visible{where} ORDER BY case_id DESC"
                            " LIMIT %s",
                         [*args, limit])
            versions = _case_versions(c, [r["case_id"] for r in rows])
        sigs = {s for r in rows for s in (r["signals"] or [])}
        prov = _prov(False, versions, asset_id, source_day, sigs)
        del prov["synthetic"]  # a list can mix both: count them; each case has its own flag
        n_syn = sum(bool(r["synthetic"]) for r in rows)
        return {"cases": rows, "real_count": len(rows) - n_syn, "synthetic_count": n_syn,
                **prov}

    def _chart(rows: list[dict], max_points: int) -> dict:
        """Downsample windows into at most max_points equal time bins: per bin the median,
        min and max of the window medians, and whether any window in it was reviewed."""
        out: dict[str, list] = {"t": [], "median": [], "min": [], "max": [], "review": []}
        if not rows:
            return out
        t0, t1 = rows[0]["window_end"], rows[-1]["window_end"]
        width = max((t1 - t0) / max_points, dt.timedelta(microseconds=1))
        bins: dict[int, list[dict]] = {}
        for r in rows:
            bins.setdefault(min(int((r["window_end"] - t0) / width), max_points - 1),
                            []).append(r)
        for _, g in sorted(bins.items()):
            m = sorted(x["median"] for x in g if x["median"] is not None)
            out["t"].append(g[-1]["window_end"])
            out["median"].append(m[len(m) // 2] if m else None)
            out["min"].append(m[0] if m else None)
            out["max"].append(m[-1] if m else None)
            out["review"].append(any(x["state"] == "review_suggested" for x in g))
        return out

    def _case_detail(c, case_id: int, max_points: int) -> dict:
        case = cases.visible_case(c, case_id)
        at = case["as_of"]
        timeline = _rows(c, "SELECT event_id, event_type, actor, recorded_at, note,"
                            " disposition, reason, related_case_id FROM case_events WHERE"
                            " case_id = %s AND event_type <> 'evidence_added' ORDER BY"
                            " event_id", [case_id])
        summary = {r["signal_name"]: r for r in _rows(
            c, "SELECT signal_name, count(*) AS windows, count(*) FILTER (WHERE"
               " episode_start) AS episodes, min(window_start) AS first_window_start,"
               " max(window_end) AS last_window_end, max(score) AS max_score FROM case_events"
               " WHERE case_id = %s AND event_type = 'evidence_added' AND window_end <= %s"
               " GROUP BY 1", [case_id, at])}
        s = replay.get_session(c, case["session_id"])
        run = next((r for r in (_progress_at(s) or {}).get("runs", [])
                    if r["run"] == case["stretch"]), {"signals": {}})
        margin = dt.timedelta(minutes=30)
        signals = {}
        for sig in sorted(case["signals"] or []):
            rows = _rows(c, "SELECT window_end, median, presentation_state AS state FROM"
                            " scores WHERE session_id = %s AND asset_id = %s AND signal_name"
                            " = %s AND stretch = %s AND window_end >= %s AND window_start <= %s"
                            " AND window_end <= %s ORDER BY window_end",
                         [case["session_id"], case["asset_id"], sig, case["stretch"],
                          case["evidence_start"] - margin, case["evidence_end"] + margin, at])
            signals[sig] = {"summary": {k: v for k, v in summary.get(sig, {}).items()
                                        if k != "signal_name"},
                            "band": run["signals"].get(sig, {}).get("band"),
                            "chart": _chart(rows, max_points)}
        related = None
        if case["related_case_id"] is not None:
            related = _one(c, "SELECT * FROM case_state_visible WHERE case_id = %s",
                           [case["related_case_id"]])
        related_by = _rows(c, "SELECT * FROM case_state_visible WHERE related_case_id = %s"
                              " ORDER BY case_id", [case_id])
        return {"case": case, "timeline": timeline, "signals": signals,
                "max_points": max_points, "chart_margin_s": margin.total_seconds(),
                "evidence_url": f"/api/cases/{case_id}/evidence",
                "related": {"related_case": related, "related_by": related_by},
                "actions": _actions(case["status"]),
                **_prov(case["synthetic"], _case_versions(c, [case_id]), case["asset_id"],
                        case["source_day"], case["signals"] or [])}

    @app.get("/api/cases/{case_id}", tags=["cases"], response_model=M.CaseDetail)
    def case_detail(request: Request, case_id: int,
                    max_points: int = Query(100, ge=2, le=1000, description=(
                        "most points per signal in the band chart"))):
        with conn_for(request) as c:
            return _case_detail(c, case_id, max_points)

    @app.get("/api/cases/{case_id}/evidence", tags=["cases"], response_model=M.EvidencePage)
    def case_evidence(request: Request, case_id: int, offset: int = Query(0, ge=0),
                      limit: int = Query(100, ge=1, le=500),
                      signal: str | None = None):
        with conn_for(request) as c:
            case = cases.visible_case(c, case_id)
            q = (" FROM case_events e JOIN scores s USING (session_id, asset_id, signal_name,"
                 " window_end, model_version) WHERE e.case_id = %s AND e.event_type ="
                 " 'evidence_added' AND e.window_end <= %s")
            args: list = [case_id, case["as_of"]]
            if signal:
                q, args = q + " AND s.signal_name = %s", [*args, signal]
            total = _one(c, "SELECT count(*) AS n" + q, args)["n"]
            items = _rows(c, "SELECT s.signal_name, s.window_start, s.window_end,"
                             " s.presentation_state AS state, s.score, s.median, s.band_low,"
                             " s.band_high, s.model_version, s.synthetic, e.episode_start" + q
                             + " ORDER BY s.window_start, s.signal_name OFFSET %s LIMIT %s",
                          [*args, offset, limit])
        nxt = offset + len(items)
        return {"case_id": case_id, "total": total, "offset": offset, "limit": limit,
                "next_offset": nxt if nxt < total else None, "items": items,
                **_prov(case["synthetic"], {x["model_version"] for x in items} or
                        _case_versions(c, [case_id]), case["asset_id"], case["source_day"],
                        case["signals"] or [])}

    def _act(request: Request, case_id: int, fn) -> dict:
        with conn_for(request) as c:
            try:
                fn(c)
            except ValueError as e:
                raise ApiError(422, "validation_error", str(e)) from None
            case = cases.visible_case(c, case_id)
            versions = _case_versions(c, [case_id])
        return {"case": case, "actions": _actions(case["status"]),
                **_prov(case["synthetic"], versions, case["asset_id"], case["source_day"],
                        case["signals"] or [])}

    @app.post("/api/cases/{case_id}/acknowledge", tags=["cases"], response_model=M.CaseActionResult)
    def acknowledge(request: Request, case_id: int, body: Actor):
        return _act(request, case_id, lambda c: cases.acknowledge(c, case_id, body.actor))

    @app.post("/api/cases/{case_id}/notes", tags=["cases"], response_model=M.CaseActionResult)
    def add_note(request: Request, case_id: int, body: NoteIn):
        return _act(request, case_id, lambda c: cases.note(c, case_id, body.actor, body.text))

    @app.post("/api/cases/{case_id}/disposition", tags=["cases"], response_model=M.CaseActionResult)
    def disposition(request: Request, case_id: int, body: DispositionIn):
        return _act(request, case_id, lambda c: cases.dispose(
            c, case_id, body.actor, body.disposition, body.reason))

    @app.post("/api/cases/{case_id}/close", tags=["cases"], response_model=M.CaseActionResult)
    def close(request: Request, case_id: int, body: Actor):
        return _act(request, case_id, lambda c: cases.close(c, case_id, body.actor))

    @app.get("/api/cases/{case_id}/export", tags=["cases"], response_model=M.CaseExport,
             responses={200: {"content": {"text/markdown": {"schema": {"type": "string"}}},
                              "description": "JSON (format=json) or a Markdown evidence pack"}})
    def export(request: Request, case_id: int, format: Literal["json", "markdown"] = "json"):
        with conn_for(request) as c:
            pack = cases.export(c, case_id)
        case = pack["case"]
        pack["run_index"], pack["run_number"] = case["stretch"], case["stretch"] + 1
        prov = _prov(case["synthetic"], {e["model_version"] for e in pack["evidence"]},
                     case["asset_id"], case["source_day"], case["signals"] or [])
        if format == "markdown":
            titles = {a["id"]: a["title"] for a in parse_assumptions(docs / "ASSUMPTIONS.md")}
            return PlainTextResponse(cases.export_markdown(pack, prov, titles),
                                     media_type="text/markdown; charset=utf-8")
        return {**jsonable_encoder(pack), **prov}

    # -- the copilot assistant --

    @app.post("/api/cases/{case_id}/assistant", tags=["assistant"],
              response_model=M.AssistantResponse)
    def ask_assistant(request: Request, case_id: int, body: M.AssistantQuestion):
        """Answer a question about the case from its evidence. The answer is checked before
        it is shown; otherwise the evidence summary is shown. Nothing is saved to the case:
        a draft note is saved only when the engineer approves it (POST .../notes)."""
        with conn_for(request) as c:
            ctx = assistant.build_context(c, case_id)
        provider = (assistant.TemplateProvider() if body.provider == "template"
                    else provider_for())
        result = assistant.answer(ctx, body.question, provider)
        with conn_for(request) as c:
            run_id = assistant.log_run(c, case_id, body.question, result)
        rej = result.rejected
        return {"case_id": case_id, "run_id": run_id, "served": result.served,
                "label": result.label, "provider": result.provider, "model": result.model,
                "answer": result.answer, "check": {"passed": result.check.passed,
                                                   "reasons": result.check.reasons},
                "rejected": None if rej is None else {
                    "provider": rej["provider"], "model": rej["model"],
                    "reasons": rej["check"]["reasons"]},
                "fallback_reason": result.fallback_reason,
                "evidence": [{k: e.get(k) for k in ("id", "kind", "signal_name",
                                                     "first_window", "last_window", "values",
                                                     "unit", "times")}
                             for e in ctx["evidence"]],
                "calibration_status": ctx["calibration_status"],
                "context_hash": result.context_hash, "latency_ms": result.latency_ms,
                "synthetic": ctx["synthetic"], "model_version": ctx["model_version"],
                "assumptions": [x["id"] for x in ctx["assumptions"]]}

    @app.get("/api/cases/{case_id}/evidence-summary", tags=["assistant"],
             response_model=M.EvidenceSummary)
    def evidence_summary(request: Request, case_id: int):
        """The evidence summary for the case: built from the evidence, no model, not logged
        (it does not depend on the question). The assistant panel shows it at once."""
        with conn_for(request) as c:
            ctx = assistant.build_context(c, case_id)
        result = assistant.answer(ctx, "", assistant.TemplateProvider())
        return {"case_id": case_id, "label": result.label, "answer": result.answer,
                "check": {"passed": result.check.passed, "reasons": result.check.reasons},
                "evidence": [{k: e.get(k) for k in ("id", "kind", "signal_name", "first_window",
                                                     "last_window", "values", "unit", "times")}
                             for e in ctx["evidence"]],
                "calibration_status": ctx["calibration_status"],
                "context_hash": result.context_hash, "synthetic": ctx["synthetic"],
                "model_version": ctx["model_version"],
                "assumptions": [x["id"] for x in ctx["assumptions"]]}

    # -- replay control --

    @app.get("/api/replay/scenarios", tags=["replay"], response_model=M.Scenarios)
    def scenarios():
        return {"scenarios": [{"name": k, **v} for k, v in sorted(replay.SCENARIOS.items())],
                "note": "applied in memory only; every score and case of such a session is"
                        " synthetic"}

    @app.get("/api/replay/sessions", tags=["replay"], response_model=M.SessionList)
    def sessions(request: Request):
        with conn_for(request) as c:
            return {"sessions": [_session_out(s) for s in _rows(
                c, "SELECT * FROM replay_sessions ORDER BY session_id DESC")]}

    @app.post("/api/replay/sessions", tags=["replay"], status_code=201,
              response_model=M.SessionEnvelope)
    def create_session(request: Request, body: SessionIn):
        if body.scenario is not None and body.scenario not in replay.SCENARIOS:
            raise ApiError(422, "validation_error", f"unknown scenario {body.scenario!r}",
                           {"known": sorted(replay.SCENARIOS)})
        with conn_for(request) as c:
            _require_asset_day(c, body.asset_id, body.source_day)
            try:
                sid = replay.create_session(c, body.asset_id, body.source_day, body.speed,
                                            scenario=body.scenario)
            except ValueError as e:
                raise ApiError(422, "validation_error", str(e)) from None
            return {"session": _session_out(replay.get_session(c, sid))}

    @app.get("/api/replay/sessions/{session_id}", tags=["replay"], response_model=M.SessionEnvelope)
    def session(request: Request, session_id: int):
        with conn_for(request) as c:
            return {"session": _session_out(_session(c, session_id))}

    def _control(request: Request, session_id: int, fn) -> dict:
        with conn_for(request) as c:
            _session(c, session_id)
            try:
                fn(c)
            except ValueError as e:
                raise ApiError(409, "invalid_state", str(e)) from None
            return {"session": _session_out(replay.get_session(c, session_id))}

    @app.post("/api/replay/sessions/{session_id}/start", tags=["replay"],
              response_model=M.SessionEnvelope)
    def start(request: Request, session_id: int):
        def go(c):
            st = replay.get_session(c, session_id)["status"]
            if st == "paused":
                replay.resume_session(c, session_id)
            elif st != "pending":
                raise ValueError(f"session {session_id} is {st}; start needs paused or pending"
                                 " (rewind a completed session first)")
        return _control(request, session_id, go)

    @app.post("/api/replay/sessions/{session_id}/pause", tags=["replay"],
              response_model=M.SessionEnvelope)
    def pause(request: Request, session_id: int):
        return _control(request, session_id, lambda c: replay.pause_session(c, session_id))

    @app.post("/api/replay/sessions/{session_id}/rewind", tags=["replay"],
              response_model=M.SessionEnvelope)
    def rewind(request: Request, session_id: int):
        return _control(request, session_id, lambda c: replay.rewind_session(c, session_id))

    @app.put("/api/replay/sessions/{session_id}/speed", tags=["replay"],
             response_model=M.SessionEnvelope)
    def speed(request: Request, session_id: int, body: SpeedIn):
        return _control(request, session_id,
                        lambda c: replay.set_speed(c, session_id, body.speed))

    @app.get("/api/replay/sessions/{session_id}/baseline", tags=["replay"],
             response_model=M.Baseline)
    def baseline(request: Request, session_id: int):
        with conn_for(request) as c:
            s = _session(c, session_id)
            versions = _versions(c, session_id)
        return {"session_id": session_id, "status": s["status"], "cursor_at": s["cursor_at"],
                "baseline_progress": _progress_at(s),
                **_prov(s["synthetic"], versions, s["asset_id"], s["source_day"],
                        (s["day_constants"] or {}).get("signals", []))}

    # -- evaluation and assumptions --

    @app.get("/api/evaluation", tags=["evaluation"], response_model=M.Evaluation)
    def evaluation():
        def load(name):
            f = reports / name
            return json.loads(f.read_text()) if f.exists() else None

        modes = {}
        for mode, (name, label) in MODES.items():
            res = load(name)
            modes[mode] = {"label": label, "file": f"reports/{name}", "results": res,
                           "note": None if res is not None else
                           f"reports/{name} not found: run the evaluation first"}
        import yaml

        from . import zema_bench

        cfg_file = data / "zema_benchmark.yaml"
        cfg = yaml.safe_load(cfg_file.read_text()) if cfg_file.exists() else None
        res = load("zema_benchmark.json")
        if res:
            res = zema_bench.migrate_results(res)

        def graded(split: str, model: str):
            sp = (res or {}).get("splits", {}).get(split, {})
            cal = sp.get(model, {}).get("calibration")
            if not cal or "ece_top_label" not in cal:
                return None
            base = sp.get("majority", {}).get("calibration", {}).get("brier")
            return zema_bench.calibration_grade(cal["brier"], cal["ece_top_label"], base)

        scores = [{**x, "calibration": graded(x["split"], x["model"])}
                  for x in (res or {}).get("scores", [])]
        zema = {"scope_note": zema_bench.SCOPE_NOTE,
                "output_label": zema_bench.OUTPUT_LABEL.format(k="k"),
                "status": "evaluated" if res else "pre-registered, not yet evaluated" if cfg
                else "not_tuned",
                "preregistration_tag": (res or {}).get("preregistration", {}).get("tag")
                or (cfg or {}).get("preregistration_tag") or "prereg-3b-r2",
                "config": cfg, "results": res, "scores": scores,
                "report": "reports/zema_benchmark.md"}
        return {"modes": modes, "cases_all_modes": load("cira_cases_all_modes.json"),
                "report": "reports/cira_scoring_eval.md", "zema": zema,
                "labels": "REAL results are unlabelled review cases, not confirmed faults;"
                          " SYNTHETIC results come from in-memory injections"}

    @app.get("/api/evaluation/chapters", tags=["evaluation"],
             response_model=M.EvaluationChapters)
    def evaluation_chapters():
        """The evaluation page's three chapters (ZeMA, the CIRA detector, the assistant), from
        the stored results: every number the page shows, how to read it, and where it comes
        from (report, pre-registration or holdout commit)."""
        from . import chapters

        return chapters.chapters(reports)

    @app.get("/api/about", tags=["evaluation"], response_model=M.About)
    def about():
        """The data sources (data/manifest.yaml, in use only) and the About page's facts."""
        import yaml

        manifest = yaml.safe_load((data / "manifest.yaml").read_text())["sources"]
        facts = yaml.safe_load((data / "about.yaml").read_text())
        sources = [{"id": k, "title": v["title"],
                    "authors": [f"{a['given']} {a['family']}" for a in v["authors"]],
                    "citation": citation(v), "landing_page": v["landing_page"],
                    "license": "CC BY 4.0" if str(v["license"]).lower().replace("-", " ")
                    == "cc by 4.0" else str(v["license"]),
                    "license_url": v.get("license_url"), "changes": v.get("changes"),
                    "role": v["role"]}
                   for k, v in manifest.items() if k in ("cira", "zema")]
        a = facts["author"]
        return {"sources": sources,
                "repository": facts["repository"] if facts.get("repository_public") else None,
                "author": {"name": a["name"], "role": a.get("role") or None,
                           "links": [x for x in a.get("links") or [] if x.get("url")]},
                **{k: facts[k] for k in ("stack", "ai_assistance")}}

    @app.get("/api/signal-names", tags=["asset-day"], response_model=M.SignalNames)
    def signal_names_():
        """The name to show for each signal id (the id stays the technical name)."""
        import yaml

        return {"signals": signal_names(yaml.safe_load((data / "cira_columns.yaml")
                                                       .read_text()))}

    @app.get("/api/assumptions", tags=["evaluation"], response_model=M.Assumptions)
    def assumptions():
        return {"assumptions": parse_assumptions(docs / "ASSUMPTIONS.md"),
                "source": "docs/ASSUMPTIONS.md"}

    # -- live stream --

    @app.get("/api/stream", tags=["stream"],
             responses={200: {"content": {"text/event-stream": {}}}})
    async def stream(request: Request, last_event_id: int | None = Query(None, ge=0),
                     types: str | None = Query(None, description="comma-separated event types"),
                     session_id: int | None = None,
                     limit: int | None = Query(None, ge=1, le=10_000, description=(
                         "test/diagnostic parameter: close the stream after this many events."
                         " Browsers omit it and stay connected.")),
                     poll_s: float = Query(15.0, ge=0.1, le=60.0, description=(
                         "test/diagnostic parameter: seconds to wait for a NOTIFY before"
                         " re-checking the log and sending a keep-alive. Default 15."))):
        header = request.headers.get("last-event-id")
        start = int(header) if header and header.isdigit() else last_event_id
        wanted = [t for t in (types or "").split(",") if t] or None
        if wanted and set(wanted) - set(events.TYPES):
            raise ApiError(422, "validation_error", f"unknown event types: {wanted}")

        async def gen():
            aconn = await psycopg.AsyncConnection.connect(url, autocommit=True)
            try:
                await aconn.execute(f"SET ROLE {role}")
                await aconn.execute(f"LISTEN {events.CHANNEL}")
                last = start
                if last is None:
                    cur = await aconn.execute("SELECT coalesce(max(event_id), 0) FROM"
                                              " stream_events")
                    last = (await cur.fetchone())[0]
                yield "retry: 3000\n\n"
                sent = 0
                while True:
                    rows = await events.after_async(aconn, last, 500, wanted, session_id)
                    for r in rows:
                        last = r["event_id"]
                        data = json.dumps({**events.signal(r["event_type"], r["payload"]),
                                           "created_at": str(r["created_at"])}, default=str)
                        yield f"id: {last}\nevent: {r['event_type']}\ndata: {data}\n\n"
                        sent += 1
                        if limit and sent >= limit:
                            return
                    if len(rows) == 500:
                        continue
                    if await request.is_disconnected():
                        return
                    woke = 0
                    async for _ in aconn.notifies(timeout=poll_s, stop_after=1):
                        woke += 1
                    if not woke:
                        yield ": keep-alive\n\n"
            finally:
                await aconn.close()

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    base_openapi = app.openapi

    def openapi() -> dict:
        """FastAPI's schema, plus the event payloads of the server-sent /api/stream."""
        if app.openapi_schema:
            return app.openapi_schema
        schema = base_openapi()
        ev = M.StreamEvent.model_json_schema(ref_template="#/components/schemas/{model}")
        comps = schema.setdefault("components", {}).setdefault("schemas", {})
        comps.update(ev.pop("$defs", {}))
        comps["StreamEvent"] = ev
        ok = schema["paths"]["/api/stream"]["get"]["responses"]["200"]
        ok["content"] = {"text/event-stream": {"schema": {"$ref":
                                                          "#/components/schemas/StreamEvent"}}}
        ok["description"] = ("server-sent events: each has `id:` (the event id), `event:` "
                             "(the type) and `data:` (the payload as JSON)")
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app
