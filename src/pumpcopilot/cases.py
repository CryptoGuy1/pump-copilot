"""Cases: an append-only event log (case_events) and the state derived from it (case_state).

The worker opens cases and adds evidence (3a-3 merge rule, see replay.CaseTracker). People
acknowledge, add notes, give a disposition with a reason, and close. The database checks every
transition (migration 0006); an invalid one raises InvalidTransition here. Nothing is sent
anywhere: "escalate to reliability engineer (export only)" means the case can be exported.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

EVENT_TYPES = ("opened", "evidence_added", "acknowledged", "note", "disposition", "closed")
DISPOSITIONS = ("monitor", "known condition, no action", "data quality issue",
                "escalate to reliability engineer (export only)")
HUMAN_FIELDS = ("note", "disposition", "reason", "related_case_id")


class InvalidTransition(RuntimeError):
    pass


def _case_keys(conn, case_id: int) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        o = cur.execute("SELECT session_id, synthetic, asset_id, source_day, stretch FROM"
                        " case_events WHERE case_id = %s AND event_type = 'opened'",
                        [case_id]).fetchone()
    if o is None:
        raise InvalidTransition(f"invalid case transition: case {case_id} does not exist")
    return o


def append_raw(conn, case_id: int, event_type: str, actor: str, **fields) -> int:
    """Insert one event as given; only the database checks it."""
    extra = {k: v for k, v in fields.items() if k in HUMAN_FIELDS}
    keys = _case_keys(conn, case_id)
    cols = ["case_id", "event_type", "actor", *keys, *extra]
    vals = [case_id, event_type, actor, *keys.values(), *extra.values()]
    return conn.execute(f"INSERT INTO case_events ({', '.join(cols)}) VALUES "
                        f"({', '.join(['%s'] * len(cols))}) RETURNING event_id",
                        vals).fetchone()[0]


def append(conn, case_id: int, event_type: str, actor: str, **fields) -> int:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type!r}")
    if not actor or not actor.strip():
        raise ValueError("an event needs an actor")
    if event_type == "disposition":
        if fields.get("disposition") not in DISPOSITIONS:
            raise ValueError(f"unknown disposition {fields.get('disposition')!r}; allowed: "
                             + "; ".join(DISPOSITIONS))
        if not (fields.get("reason") or "").strip():
            raise ValueError("a disposition requires a reason")
    if event_type == "note" and not (fields.get("note") or "").strip():
        raise ValueError("a note needs text")
    from . import events

    for attempt in range(3):
        try:
            return _append_once(conn, case_id, event_type, actor, events, fields)
        except psycopg.errors.DeadlockDetected:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def _append_once(conn, case_id, event_type, actor, events, fields) -> int:
    try:
        with conn.transaction():
            event_id = append_raw(conn, case_id, event_type, actor, **fields)
            st = get_case(conn, case_id)
            events.emit(conn, "case.event", {
                "case_id": case_id, "session_id": st["session_id"],
                "asset_id": st["asset_id"], "source_day": str(st["source_day"]),
                "synthetic": st["synthetic"], "actor": actor, "event_type": event_type,
                "status": st["status"], "disposition": st["disposition"]})
            return event_id
    except psycopg.errors.RaiseException as e:
        msg = str(e).splitlines()[0]
        if "invalid case transition" in msg:
            raise InvalidTransition(msg) from None
        raise


def acknowledge(conn, case_id: int, actor: str) -> int:
    return append(conn, case_id, "acknowledged", actor)


def note(conn, case_id: int, actor: str, text: str) -> int:
    return append(conn, case_id, "note", actor, note=text)


def dispose(conn, case_id: int, actor: str, disposition: str, reason: str) -> int:
    return append(conn, case_id, "disposition", actor, disposition=disposition, reason=reason)


def close(conn, case_id: int, actor: str) -> int:
    return append(conn, case_id, "closed", actor)


def list_cases(conn, session_id: int | None = None) -> list[dict]:
    q, args = "SELECT * FROM case_state", []
    if session_id is not None:
        q, args = q + " WHERE session_id = %s", [session_id]
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(q + " ORDER BY case_id", args).fetchall()


def get_case(conn, case_id: int) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        c = cur.execute("SELECT * FROM case_state WHERE case_id = %s", [case_id]).fetchone()
    if c is None:
        raise InvalidTransition(f"invalid case transition: case {case_id} does not exist")
    return c


def export(conn, case_id: int) -> dict:
    """Everything about one case, for handing to a reliability engineer outside this system."""
    case = get_case(conn, case_id)
    with conn.cursor(row_factory=dict_row) as cur:
        events = cur.execute("SELECT * FROM case_events WHERE case_id = %s ORDER BY event_id",
                             [case_id]).fetchall()
        evidence = cur.execute(
            "SELECT s.signal_name, s.window_start, s.window_end, s.presentation_state, s.score,"
            " s.median, s.band_low, s.band_high, s.model_id, s.model_version, s.synthetic,"
            " s.scored_evidence FROM case_events e"
            " JOIN scores s USING (session_id, asset_id, signal_name, window_end, model_version)"
            " WHERE e.case_id = %s AND e.event_type = 'evidence_added' ORDER BY s.window_start,"
            " s.signal_name", [case_id]).fetchall()
    return {"case": case, "events": events, "evidence": evidence,
            "note": "exported for review; this system sends nothing and controls nothing"}


def _fmt(x, digits=5) -> str:
    return "-" if x is None else f"{x:.{digits}g}" if isinstance(x, float) else str(x)


def export_markdown(pack: dict, provenance: dict, assumption_titles: dict | None = None) -> str:
    """A readable evidence pack from export(): what the case is, what was done, the evidence."""
    c, titles = pack["case"], assumption_titles or {}
    synthetic = bool(c["synthetic"])
    out = [f"# Evidence pack: case {c['case_id']}", ""]
    if synthetic:
        out += ["> **SYNTHETIC**: this case comes from a replay with an injected fault. It is "
                "not evidence about the real pump.", ""]
    out += ["| | |", "|---|---|",
            f"| asset | {c['asset_id']} |", f"| day | {c['source_day']} |",
            f"| run | {c['stretch']} |", f"| status | {c['status']} |",
            f"| evidence | {c['evidence_start']} to {c['evidence_end']} |",
            f"| signals | {', '.join(sorted(c['signals'] or []))} |",
            f"| episodes / windows | {c['episodes']} / {c['evidence_windows']} |",
            f"| data | {'SYNTHETIC' if synthetic else 'REAL'} |",
            f"| model version | {', '.join(provenance['model_version'])} |",
            f"| related case | {c.get('related_case_id') or '-'} |", ""]
    if c["disposition"]:
        out += [f"**Disposition:** {c['disposition']}. Reason: {c['disposition_reason']}", ""]
    out += ["## Assumptions that apply", ""] + [
        f"- **{a}**: {titles.get(a, '')}".rstrip(": ") for a in provenance["assumptions"]] + [""]
    out += ["## Actions and notes", "", "| when | event | by | detail |", "|---|---|---|---|"]
    n_evidence = 0
    for e in pack["events"]:
        if e["event_type"] == "evidence_added":
            n_evidence += 1
            continue
        detail = e["note"] or (f"{e['disposition']}: {e['reason']}" if e["disposition"] else "")
        if e["event_type"] == "opened" and e.get("related_case_id"):
            detail = f"after case {e['related_case_id']} was closed"
        out.append(f"| {e['recorded_at']} | {e['event_type']} | {e['actor']} | {detail} |")
    out += ["", f"{n_evidence} evidence windows were added by the replay worker.", "",
            "## Evidence", "",
            "| window | signal | median | band | score | state |", "|---|---|---|---|---|---|"]
    for w in pack["evidence"]:
        out.append(f"| {w['window_start']} to {w['window_end']} | {w['signal_name']} | "
                   f"{_fmt(w['median'])} | {_fmt(w['band_low'])} to {_fmt(w['band_high'])} | "
                   f"{_fmt(w['score'], 3)} | {w['presentation_state']} |")
    out += ["", "---", "Exported for review. This system sends nothing and controls nothing; "
            "escalation to a reliability engineer is export-only.", ""]
    return "\n".join(out)
