from pumpcopilot import acquire


def _record(rec_id: int, index: int, is_last: bool, version: str | None, date: str) -> dict:
    return {
        "id": rec_id,
        "doi": f"10.5281/zenodo.{rec_id}",
        "conceptrecid": "100",
        "conceptdoi": "10.5281/zenodo.100",
        "metadata": {
            "title": "Centrifugal Pump Dataset",
            "version": version,
            "publication_date": date,
            "license": {"id": "cc-by-4.0"},
            "access_right": "open",
            "relations": {"version": [{"index": index, "is_last": is_last}]},
        },
        "files": [],
    }


def test_zenodo_lists_all_versions_and_marks_the_downloaded_one(tmp_path, monkeypatch):
    v1 = _record(150, 0, False, "1.0", "2025-05-15")
    v2 = _record(200, 1, True, None, "2026-02-04")
    calls = []

    def fake_fetch(url: str) -> dict:
        calls.append(url)
        if "conceptrecid:100" in url:
            return {"hits": {"hits": [v2, v1], "total": 2}}  # API order is not trusted
        assert url.endswith("/records/200")
        return v2

    monkeypatch.setattr(acquire, "_fetch_json", fake_fetch)
    out = acquire.acquire_zenodo("cira", {"download": {"record_id": "200"}}, tmp_path)
    z = out["zenodo"]
    assert z["conceptrecid"] == "100"
    assert z["license"] == "cc-by-4.0"
    assert [(v["record_id"], v["version_number"], v["version"]) for v in z["versions"]] == [
        ("150", 1, "1.0"), ("200", 2, None)]
    assert z["downloaded"] == {"record_id": "200", "version_number": 2, "of": 2,
                               "is_latest": True}
    assert any("allversions=true" in c for c in calls)
