"""The README states nothing that could drift: its key results are generated from the stored
results, its credits match the About page's facts, and its in-page links have targets."""

import re
from pathlib import Path

import yaml

from pumpcopilot import readme

ROOT = Path(__file__).resolve().parents[1]


def test_the_key_results_are_the_stored_results():
    text = readme.README.read_text()
    assert readme.current_block(text) == readme.results_block()
    assert "0 control instructions served" in readme.current_block(text)


def test_the_ai_note_and_author_match_the_about_page():
    about = yaml.safe_load((ROOT / "data" / "about.yaml").read_text())
    text = " ".join(readme.README.read_text().split())
    assert " ".join(about["ai_assistance"].split()) in text
    assert f"**{about['author']['name']}**" in text


def _slug(heading: str) -> str:
    s = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return s.replace(" ", "-")


def test_in_page_links_have_targets():
    text = readme.README.read_text()
    slugs = {_slug(h) for h in re.findall(r"^#+ (.+)$", text, re.M)}
    anchors = re.findall(r"\]\(#([^)]+)\)", text)
    assert anchors and [a for a in anchors if a not in slugs] == []
