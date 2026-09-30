"""The live stream's event log (stream_events) and its NOTIFY channel.

emit() takes a transaction-level advisory lock before it takes an id, so a later writer waits
for an earlier one to commit: ids become visible in increasing order, and a client resuming
after the highest id it has seen (Last-Event-ID) misses nothing. Call it inside the writer's
transaction, as late as possible; the lock is held until that transaction ends.

Events are signals, not data: a payload holds only identifiers and types (FIELDS), so a page
can use an event only as a cue to refetch. An old event replayed after a rewind carries no
scores, states or case details that could show on screen before the refetch clips them.
emit() drops any other field it is given.
"""

from __future__ import annotations

import json

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

CHANNEL = "pumpcopilot_events"
TYPES = ("replay.progress", "score.batch", "case.event")
_LOCK = 72_616_003  # advisory lock: one event writer at a time
_IDS = ("session_id", "asset_id", "source_day", "synthetic")
FIELDS = {"replay.progress": _IDS, "score.batch": _IDS,
          "case.event": (*_IDS, "case_id", "event_type")}


def signal(event_type: str, payload: dict) -> dict:
    """The payload reduced to identifiers and the type (also for events logged before the
    reduction, which the stream still replays)."""
    return {k: payload[k] for k in FIELDS[event_type] if k in payload}


def emit(conn, event_type: str, payload: dict) -> int:
    if event_type not in TYPES:
        raise ValueError(f"unknown event type {event_type!r}")
    body = json.loads(json.dumps(signal(event_type, payload), default=str))
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", [_LOCK])
        event_id = conn.execute("INSERT INTO stream_events (event_type, payload) VALUES"
                                " (%s, %s) RETURNING event_id",
                                [event_type, Jsonb(body)]).fetchone()[0]
        conn.execute("SELECT pg_notify(%s, %s)", [CHANNEL, str(event_id)])
    return event_id


def _query(types, session_id) -> tuple[str, list]:
    q, args = "SELECT event_id, event_type, payload, created_at FROM stream_events" \
              " WHERE event_id > %s", []
    if types:
        q, args = q + " AND event_type = ANY(%s)", [list(types)]
    if session_id is not None:
        q, args = q + " AND payload->>'session_id' = %s", [*args, str(session_id)]
    return q + " ORDER BY event_id LIMIT %s", args


def after(conn, last_id: int, limit: int = 1000, types=None, session_id=None) -> list[dict]:
    q, args = _query(types, session_id)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(q, [last_id, *args, limit]).fetchall()


async def after_async(aconn, last_id: int, limit: int = 1000, types=None,
                      session_id=None) -> list[dict]:
    q, args = _query(types, session_id)
    cur = aconn.cursor(row_factory=dict_row)
    await cur.execute(q, [last_id, *args, limit])
    return await cur.fetchall()
