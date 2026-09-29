"""reports/assistant_eval.md: the real-model runs of the assistant, revision by revision.

The history is part of the evidence: the baseline run (checker revision 0), the changelog of
revision 1, and the revision-1 run. Nothing here is recomputed; it reads the stored results.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def _lat(x: dict) -> str:
    lat = x.get("latency_ms") or {}
    return f"p50 {lat.get('p50')} ms, p95 {lat.get('p95')} ms (n {lat.get('n')})"


def _benign(title: str, b: dict) -> list[str]:
    out = [f"### {title}", "",
           f"{b['total']} questions: **{b['served']['assistant']} served as \"Assistant, "
           f"checked\"**, {b['served']['template']} fell back to the evidence summary. Model "
           f"latency {_lat(b)}.", ""]
    rej = [r for r in b["records"] if r["served"] == "template"]
    if rej:
        out += ["| id | case | question | fallback | reasons |", "|---|---|---|---|---|"]
        for r in rej:
            reasons = "; ".join(x[:140].replace("|", "/") for x in r["rejection_reasons"])
            out.append(f"| {r['id']} | {r['case']} {r['case_id']} | {r['question']} | "
                       f"{r['fallback_reason']} | {reasons or '-'} |")
        out.append("")
    ok = [r["id"] for r in b["records"] if r["served"] == "assistant"]
    out += [f"Served as checked: {', '.join(ok) or 'none'}.", ""]
    return out


def _adversarial(a: dict) -> list[str]:
    return [f"{a['total']} prompts: **{a['raw_passed_checker']} raw model answers passed the "
            f"checker by themselves**, {a['fell_back']} fell back. **Final pass rate after the "
            f"checker and fallback: {a['passed']}/{a['total']}.** Model latency {_lat(a)}."
            + (f" Failures: {a['failures']}." if a["failures"] else ""), ""]


def _usage(run: dict) -> list[str]:
    u = run["usage"]
    return [f"Usage: {u['requests']} answered requests, {u['input_tokens']} input and "
            f"{u['output_tokens']} output tokens; estimated ${run['estimated_cost_usd']} at "
            "ASSUMED prices (data/assistant.yaml). Ledger: "
            f"{run['requests_after']}/{run['cap']} ({run.get('ledger', 'Step 6B ledger')}).", ""]


def markdown(reports: Path = ROOT / "reports", docs: Path = ROOT / "docs") -> str:
    base = _load(reports / "assistant_eval_baseline.json")
    r1 = _load(reports / "assistant_eval_r1.json")
    scan = _load(reports / "assistant_keyscan.json")
    out = ["# Copilot assistant: real-model evaluation", "",
           "Model: `claude-sonnet-5` through tool use. Every answer is checked before it is "
           "shown; otherwise the evidence summary is shown. Contexts: the adversarial set uses "
           "fixed sample contexts; the benign sets use real and synthetic cases in the local "
           "database. The history of what failed and why is part of the evidence, so both runs "
           "are kept.", ""]
    if r1:
        out += ["## Revision 1 (checker revision 1)", ""]
        if r1.get("holdout_commit"):
            out += [f"The holdout set was committed before this run, in `{r1['holdout_commit']}`"
                    " (`data/assistant_benign_holdout.yaml`, unchanged since); the run checked "
                    "this before it started.", ""]
        if "holdout" in r1:
            out += _benign("Holdout benign set (written blind, committed before any run)",
                           r1["holdout"])
        if "benign" in r1:
            out += _benign("Original benign set, after revision (seen)", r1["benign"])
        if "adversarial" in r1:
            out += ["### Adversarial set (the original 50)", ""] + _adversarial(r1["adversarial"])
        if "adversarial_r1" in r1:
            out += ["### Revision-1 adversarial additions (15, both sides of each changed rule)",
                    ""] + _adversarial(r1["adversarial_r1"])
        out += _usage(r1)
        if r1.get("incomplete"):
            out += [f"**Incomplete:** {r1['incomplete']}", ""]
    else:
        out += ["## Revision 1", "", "Not run yet.", ""]
    changelog = docs / "ASSISTANT_CHECKER.md"
    if changelog.exists():
        text = changelog.read_text()
        out += ["## Changelog of revision 1", "",
                text[text.index("## Revision 1"):].split("\n", 2)[2].strip(), ""]
    if base:
        out += ["## Baseline (checker revision 0): the first run", "",
                "The first attempt crashed in the benign set: the evidence-summary fallback "
                "failed its own check on the unit `m/s^2` (a checker false alarm, fixed in the "
                "template). It had used 51 requests (plus 1 ping) and its adversarial results "
                "were not saved. The cap was raised from 120 to 122, with approval, for the "
                "rerun below.", ""]
        if "adversarial" in base:
            out += ["### Adversarial set", ""] + _adversarial(base["adversarial"])
        if "benign" in base:
            out += _benign("Benign set", base["benign"])
        out += _usage(base)
    if scan:
        dbs = ", ".join(f"{k}: {v.get('assistant_runs', '?')} runs, {v.get('matches', '?')} "
                        "matches" for k, v in scan["databases"].items())
        files = ", ".join(f"{Path(k).name}: {v['files']} files, {v['matches']} matches"
                          for k, v in scan["files"].items())
        out += ["## Key safety", "",
                f"The key and every 8-character piece of it ({scan['pieces_checked']}) were "
                f"searched for: assistant_runs ({dbs}); files ({files}); Git index "
                f"({scan['git_index_matches']} matches). "
                f"**{'Clean' if scan['clean'] else 'NOT CLEAN'}.**", ""]
    return "\n".join(out) + "\n"
