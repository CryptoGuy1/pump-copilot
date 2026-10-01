"""Everything the repository's results depend on is in the repository: the files the report
generators read, the reports the evaluation page links, and every file linked from the
documents and reports. (conftest.py also fails any test run that reads an untracked file.)"""

import json
import os
import re
from pathlib import Path

import yaml
from conftest import LOCAL_BY_DESIGN, recording_reads, tracked_files, untracked_reads

ROOT = Path(__file__).resolve().parents[1]


def test_report_generators_read_only_tracked_files():
    from pumpcopilot import assistant_report, chapters, cli, zema_report

    with recording_reads() as reads:
        cli.scoring_report_text()
        assistant_report.markdown()
        zema_report.markdown(yaml.safe_load((ROOT / "data" / "zema_benchmark.yaml").read_text()),
                             json.loads((ROOT / "reports" / "zema_benchmark.json").read_text()))
        ch = chapters.chapters(ROOT / "reports")
    assert reads, "nothing was recorded"
    assert untracked_reads(reads) == []
    tracked = tracked_files()
    linked = [c["report"] for c in ch.values() if c] + [
        c["preregistration"].get("report") for c in ch.values() if c and "preregistration" in c]
    assert [r for r in linked if r and r not in tracked] == []


LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def test_every_file_linked_from_docs_and_reports_is_tracked():
    tracked = tracked_files()
    dirs = {str(Path(t).parent) for t in tracked} | {
        "/".join(t.split("/")[:i]) for t in tracked for i in range(1, t.count("/") + 1)}
    docs = sorted(t for t in tracked if t.endswith(".md") and not t.startswith(LOCAL_BY_DESIGN)
                  and not t.startswith("web/node_modules/"))
    missing, checked = [], 0
    for doc in docs:
        for target in LINK.findall((ROOT / doc).read_text()):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue  # external (https:, mailto:) or a link within the page
            path = target.split("#", 1)[0].split("?", 1)[0]
            norm = os.path.normpath(os.path.join(os.path.dirname(doc), path)).replace(os.sep, "/")
            checked += 1
            if norm not in tracked and norm.rstrip("/") not in dirs:
                missing.append(f"{doc} -> {target}")
    assert checked > 50
    assert missing == []
