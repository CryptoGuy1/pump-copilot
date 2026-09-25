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
    try:
        with conn.transaction():
            return append_raw(conn, case_id, event_type, actor, **fields)
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
            " s.model_id, s.model_version, s.synthetic, s.scored_evidence FROM case_events e"
            " JOIN scores s USING (session_id, asset_id, signal_name, window_end, model_version)"
            " WHERE e.case_id = %s AND e.event_type = 'evidence_added' ORDER BY s.window_start,"
            " s.signal_name", [case_id]).fetchall()
    return {"case": case, "events": events, "evidence": evidence,
            "note": "exported for review; this system sends nothing and controls nothing"}
