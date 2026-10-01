"""The evaluation page's three chapters (ZeMA, the CIRA detector, the assistant), built from the
stored results only: nothing is re-scored, and every number the page shows comes from here.

Each chapter says how to read it, and links its report and its pre-registration (or holdout)
commit. The "instructions served" count applies the checker's own control-instruction rule to
every answer that was served to a user in the stored real-model runs.
"""

from __future__ import annotations

import json
from pathlib import Path

SPLITS = ("random", "grouped", "chronological")
MODES = ("3a across-day", "3a-2 across-day", "3a-2 within-run", "3a-3 within-run steady")
# Plain names for the detector modes; the internal label stays alongside as a tag.
MODE_NAMES = {
    "3a across-day": "Band from another day, first version",
    "3a-2 across-day": "Band from another day, revised",
    "3a-2 within-run": "Band from the start of each run",
    "3a-3 within-run steady": "Band from each run's steady start (pre-registered)",
}
SIZE_CLASSES = ("small", "medium", "large")


def _load(reports: Path, name: str):
    f = reports / name
    return json.loads(f.read_text()) if f.exists() else None


def zema(reports: Path) -> dict | None:
    from . import zema_bench

    res = _load(reports, "zema_benchmark.json")
    if not res:
        return None
    res = zema_bench.migrate_results(res)
    heads = res.get("headline", {})

    def model(split: str, name: str) -> dict:
        r = res["splits"][split][name]
        ci = r.get("ci95", {}).get("macro_f1", {})
        return {"model": name, "macro_f1": r["test"]["macro_f1"], "lo": ci.get("lo"),
                "hi": ci.get("hi"), "n_blocks": r.get("ci95", {}).get("n_blocks")}

    splits = [{"split": s, "headline": model(s, heads[s]), "majority": model(s, "majority")}
              for s in SPLITS if s in res.get("splits", {}) and s in heads]
    head = res["splits"]["chronological"][heads["chronological"]]
    base = res["splits"]["chronological"].get("majority", {}).get("calibration", {})
    cal = head.get("calibration", {})
    sc = res.get("shortcut_analysis", {})
    pre = res.get("preregistration", {})
    guess = [x["majority"]["macro_f1"] for x in splits]
    by = {x["split"]: x["headline"] for x in splits}
    contrast = None
    if "random" in by and "chronological" in by:
        r, c = by["random"], by["chronological"]
        same = "Same model, same data" if r["model"] == c["model"] \
            else f"Same data ({r['model']} and {c['model']})"
        contrast = (f"{same}: {r['macro_f1']:.3f} on a random split, {c['macro_f1']:.3f} on a "
                    "chronological one")
    return {
        "contrast": contrast,
        "how_to_read": "Macro-F1 averages the three leakage states equally (1.0 is perfect; "
                       "always guessing the most common state scores "
                       f"{min(guess):.2f} to {max(guess):.2f} here). Bars are 95% intervals over "
                       "leakage runs. The chronological split is the honest one: it tests on "
                       "later cycles than it trains on.",
        "scope_note": zema_bench.SCOPE_NOTE,
        "splits": splits,
        "confusion": {"split": "chronological", "model": heads["chronological"],
                      "labels": [str(x) for x in head["test"]["confusion_labels"]],
                      "matrix": head["test"]["confusion"], "n": head["test"]["n"]},
        "shortcut": {"table": sc.get("table"), "axes": sc.get("table_axes"),
                     "n_cycles": sc.get("n_cycles"),
                     "mutual_information_bits": sc.get("mutual_information_bits"),
                     "share_of_leakage_entropy": sc.get("normalized_by_leakage_entropy")},
        "calibration": zema_bench.calibration_grade(cal["brier"], cal["ece_top_label"],
                                                    base.get("brier")) if cal else None,
        "report": "reports/zema_benchmark.md",
        "preregistration": {"tag": pre.get("tag"), "commit": pre.get("commit")},
    }


def cira(reports: Path) -> dict | None:
    cases = _load(reports, "cira_cases_all_modes.json")
    s3 = _load(reports, "cira_scoring_eval_3a3.json")
    if not cases or not s3:
        return None
    modes = []
    for m in MODES:
        pumps, exploratory = [], []
        for pump, v in (cases.get(m) or {}).items():
            row = {"pump": pump.replace("(exploratory)", "").strip(),
                   "abstained": v.get("abstained"), "cases": v.get("cases"),
                   "cases_per_running_hour": v.get("cases_per_running_hour"),
                   "case_time_fraction": v.get("case_time_fraction")}
            (exploratory if "exploratory" in pump else pumps).append(row)
        modes.append({"mode": m, "name": MODE_NAMES.get(m, m), "pumps": pumps,
                      "exploratory": exploratory})
    syn = s3["synthetic"]
    sizes = {f: sorted({c["size"] for c in syn["cells"] if c["fault"] == f})
             for f in {c["fault"] for c in syn["cells"]}}
    cells = [{k: c[k] for k in ("fault", "size", "size_label", "injections", "detected",
                                "detection_rate", "median_delay_s")}
             | {"size_class": SIZE_CLASSES[sizes[c["fault"]].index(c["size"])]
                if len(sizes[c["fault"]]) == len(SIZE_CLASSES) else None}
             for c in syn["cells"]]
    last = {p["pump"]: p for p in modes[-1]["pumps"]}
    step6 = next((c for c in cells if c["fault"] == "step" and c["size"] == 6.0), None)
    always = sorted({c["fault"] for c in cells}
                    - {c["fault"] for c in cells if c["detection_rate"] < 1.0})
    finding = ("On real October data the pre-registered 3a-3 detector keeps "
               f"{last['B']['case_time_fraction']:.1%} of pump B's and "
               f"{last['A']['case_time_fraction']:.1%} of pump A's running time inside a review "
               "case, so a case means \"look here\", not \"fault\"; on synthetic injections, "
               f"{' and '.join(always)} faults are always detected, but a 6-sigma step only in "
               f"{step6['detected']} of {step6['injections']}.") if "A" in last and "B" in last \
        and step6 else None
    return {
        "how_to_read": "Each mode is one detector revision on the same real days. Running time "
                       "in a case is the share of the pump's running time inside an open review "
                       "case: lower is quieter, but CIRA has no fault labels, so quieter is not "
                       "proven better. The heat map shows how often a synthetic fault of each "
                       "type and size was detected.",
        "modes": modes, "synthetic": {"day": syn["day"], "cells": cells,
                                      "starts_per_fault": syn["starts_per_fault"]},
        "key_finding": finding,
        "exploratory_note": "Exploratory, outside the protocol: pump A with a 1 h minimum fit "
                            "instead of the pre-registered 2 h, so that the across-day modes "
                            "do not abstain on it (see the report).",
        "report": "reports/cira_scoring_eval.md",
        "preregistration": {"tag": "prereg-3a3", "commit": s3["preregistration"]["commit"]},
    }


def _served_texts(name: str, run: dict) -> list[dict]:
    """Every text of every answer the model served in a stored run: claims are read as claims,
    suggested checks and draft notes strictly."""
    out = []
    for key, part in run.items():
        if not isinstance(part, dict) or "records" not in part:
            continue
        for r in part["records"]:
            raw = r.get("raw_output") or {}
            if r.get("served") != "assistant" or not isinstance(raw, dict):
                continue
            base = {"run": name, "set": key, "id": r.get("id")}
            out += [{**base, "where": "claim", "text": c.get("text", ""), "strict": False}
                    for c in raw.get("claims", []) if isinstance(c, dict)]
            out += [{**base, "where": "check", "text": t, "strict": True}
                    for t in raw.get("suggested_checks") or []]
            if raw.get("draft_note"):
                out.append({**base, "where": "note", "text": raw["draft_note"], "strict": True})
    return out


def assistant(reports: Path, reviews_file: Path | None = None) -> dict | None:
    import yaml

    from . import assistant as a

    runs = {"baseline": _load(reports, "assistant_eval_baseline.json"),
            "r1": _load(reports, "assistant_eval_r1.json"),
            "r2": _load(reports, "assistant_eval_r2.json")}
    if not all(runs.values()):
        return None
    rescored = (_load(reports, "assistant_eval_r2_rescored.json") or {}).get("adversarial", {})
    first_set = {"baseline": ("benign", "benign set (seen while revising)"),
                 "r1": ("holdout", "holdout (written blind)"),
                 "r2": ("holdout2", "second holdout (written blind)")}
    revisions = []
    served_answers = 0
    for i, (name, run) in enumerate(runs.items()):
        key, label = first_set[name]
        s = run[key]
        adv = run["adversarial"]
        rev = {"revision": name, "checker_version": run.get("checker_version", i),
               "questions": {"set": label, "total": s["total"],
                             "served_checked": s["served"]["assistant"]},
               "holdout_commit": run.get("holdout_commit"),
               "adversarial": {"total": adv["total"], "raw_passed": adv["raw_passed_checker"],
                               "final_passed": adv["passed"]}}
        if name == "r2" and rescored.get("corrected"):
            rev["adversarial"]["final_passed_corrected"] = rescored["corrected"]["passed"]
        revisions.append(rev)
        served_answers += sum(1 for part in run.values() if isinstance(part, dict)
                              for r in part.get("records", []) if r.get("served") == "assistant")
    rf = reviews_file or (Path(__file__).resolve().parents[2] / "data"
                          / "assistant_flag_reviews.yaml")
    reviews = {(r["run"], r["set"], r["id"], r["where"]): r
               for r in (yaml.safe_load(rf.read_text()) or {}).get("reviews", [])} \
        if rf.exists() else {}
    flagged = []
    for name, run in runs.items():
        for t in _served_texts(name, run):
            hit = a._control_instruction(t["text"], t["strict"])
            if hit:
                rv = reviews.get((t["run"], t["set"], t["id"], t["where"]), {})
                flagged.append({k: t[k] for k in ("run", "set", "id", "where", "text")}
                               | {"rule_hit": hit, "verdict": rv.get("verdict"),
                                  "why": rv.get("why"), "reviewed_by": rv.get("reviewed_by")})
    served_instructions = sum(1 for f in flagged if f["verdict"] != "not an instruction")
    return {
        "how_to_read": "The raw pass rate is how often the model's own answer passed the "
                       "checker; the final pass rate counts what users saw, the checked answer "
                       "or the evidence summary. Holdout questions were written and committed "
                       "before each run. Checker revision 2 is the final one.",
        "revisions": revisions,
        "instructions_served": served_instructions,
        "served_answers": served_answers,
        "flagged": flagged,
        "instructions_rule": "the checker's control-instruction rule (sentence form; checks and "
                             "notes read strictly) applied to every answer served by the model",
        "report": "reports/assistant_eval.md",
    }


def chapters(reports: Path) -> dict:
    return {"zema": zema(reports), "cira": cira(reports), "assistant": assistant(reports)}
