"""Hashing helpers. Row hashes double as idempotency keys for ingestion and dedup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CHUNK = 1 << 20
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_path(path: Path | str) -> str:
    """A path as reports record it: relative to the project root, never absolute (a report
    must not carry a home directory). A path outside the project keeps its name only."""
    p = Path(path).resolve()
    try:
        return p.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return p.name


def file_digest(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def record_hash(source_dataset: str, source_file: str, locator: str | int, payload: Any) -> str:
    """Deterministic hash of (source, file, row/cycle locator, payload).

    Same input row -> same hash, so re-ingesting or replaying a duplicate is a no-op.
    """
    body = json.dumps(
        {"d": source_dataset, "f": source_file, "l": str(locator), "p": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()
