"""Download source datasets into data/raw/<source>/ and record what was actually retrieved.

Nothing here redistributes data. The lock file records URL, size, digest and retrieval time so
every later report can name the exact bytes it was computed from. If a pinned hash in
manifest.yaml does not match, acquisition stops (scope Week 1 stop rule).
"""

from __future__ import annotations

import json
import shutil
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .provenance import file_digest

UA = {"User-Agent": "pumpcopilot-acquire/0.1 (portfolio research)"}


class AcquisitionError(RuntimeError):
    pass


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _entry(url: str, path: Path, **extra) -> dict:
    return {
        "url": url,
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": file_digest(path),
        "retrieved_at": datetime.now(UTC).isoformat(),
        **extra,
    }


def acquire_url(name: str, spec: dict, raw_root: Path) -> dict:
    url = spec["download"]["url"]
    dest = raw_root / name / Path(url.split("?")[0]).name.replace("+", "_")
    if not dest.exists():
        _download(url, dest)
    entry = _entry(url, dest)
    pinned = spec.get("expected_sha256")
    if pinned and pinned != entry["sha256"]:
        raise AcquisitionError(f"{name}: sha256 {entry['sha256']} != pinned {pinned}")
    if zipfile.is_zipfile(dest):
        out = raw_root / name / "extracted"
        with zipfile.ZipFile(dest) as z:
            for member in z.namelist():  # refuse path traversal
                if member.startswith("/") or ".." in Path(member).parts:
                    raise AcquisitionError(f"unsafe path in archive: {member}")
            z.extractall(out)
            entry["members"] = sorted(z.namelist())
    return {"source": name, "files": [entry]}


def _zenodo_versions(conceptrecid: str) -> list[dict]:
    """All versions of a Zenodo concept record, oldest first (sorted by relations index)."""
    # size is capped at 25 for anonymous requests (larger values return HTTP 400)
    url = (f"https://zenodo.org/api/records?q=conceptrecid:{conceptrecid}"
           "&allversions=true&sort=version&size=25")
    hits = []
    while url:
        page = _fetch_json(url)
        hits += page["hits"]["hits"]
        url = page.get("links", {}).get("next")
    versions = []
    for h in hits:
        md = h.get("metadata", {})
        rel = (md.get("relations", {}).get("version") or [{}])[0]
        index = rel.get("index")
        versions.append({
            "record_id": str(h["id"]),
            "doi": h.get("doi"),
            "version": md.get("version"),
            "publication_date": md.get("publication_date"),
            "version_number": None if index is None else index + 1,
            "is_latest": rel.get("is_last"),
            "files": len(h.get("files", [])),
        })
    return sorted(versions, key=lambda v: (v["version_number"] is None, v["version_number"]))


def acquire_zenodo(name: str, spec: dict, raw_root: Path) -> dict:
    rec_id = str(spec["download"]["record_id"])
    meta = _fetch_json(f"https://zenodo.org/api/records/{rec_id}")
    md = meta.get("metadata", {})
    conceptrecid = meta.get("conceptrecid")
    versions = _zenodo_versions(conceptrecid) if conceptrecid else []
    ours = next((v for v in versions if v["record_id"] == rec_id), {})
    files = []
    for f in meta.get("files", []):
        url = f["links"]["self"]
        dest = raw_root / name / f["key"]
        if not dest.exists():
            _download(url, dest)
        algo, _, expected = f.get("checksum", "md5:").partition(":")
        observed = file_digest(dest, algo or "md5")
        if expected and observed != expected:
            raise AcquisitionError(f"{name}/{f['key']}: {algo} mismatch")
        files.append(_entry(url, dest, zenodo_checksum=f.get("checksum")))
    return {
        "source": name,
        "zenodo": {
            "record_id": rec_id,
            "doi": meta.get("doi"),
            "version": md.get("version"),
            "title": md.get("title"),
            "license": (md.get("license") or {}).get("id", "NOT STATED"),
            "access_right": md.get("access_right"),
            "conceptrecid": conceptrecid,
            "conceptdoi": meta.get("conceptdoi"),
            "versions": versions,
            "downloaded": {
                "record_id": rec_id,
                "version_number": ours.get("version_number"),
                "of": len(versions),
                "is_latest": ours.get("is_latest"),
            },
        },
        "files": files,
    }


def acquire(manifest_path: Path, raw_root: Path, only: list[str] | None = None) -> dict:
    manifest = yaml.safe_load(manifest_path.read_text())
    lock_path = manifest_path.with_name("manifest.lock.json")
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    for name, spec in manifest["sources"].items():
        if only and name not in only:
            continue
        kind = spec["download"]["kind"]
        if kind == "url":
            lock[name] = acquire_url(name, spec, raw_root)
        elif kind == "zenodo":
            lock[name] = acquire_zenodo(name, spec, raw_root)
        else:
            print(f"[skip] {name}: manual download ({spec.get('landing_page')})")
            continue
        print(f"[ok] {name}: {len(lock[name]['files'])} file(s)")
    lock_path.write_text(json.dumps(lock, indent=2))
    return lock
