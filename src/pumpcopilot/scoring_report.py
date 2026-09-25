"""Markdown report for the CIRA scoring protocol: reports/cira_scoring_eval.md."""

from __future__ import annotations

import yaml

CONFIG_HEADER = """\
# FROZEN scoring configuration. Written by `pumpcopilot score tune cira` from B_2024-06-11
# only (within-June time split); see `frozen` below and reports/cira_scoring_eval.md.
# Do not hand-edit after October has been scored: re-tuning would no longer be blind.
"""


def config_yaml(cfg: dict) -> str:
    return CONFIG_HEADER + yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None,
                                          width=100)


def _f(x, nd=3) -> str:
    if x is None:
        return "-"
    return f"{x:.{nd}g}" if isinstance(x, float) else str(x)


def _min(s) -> str:
    return "-" if s is None else f"{s / 60:.1f}"


def _pump_body(e: dict, min_fit_s: float) -> list[str]:
    out = [f"- Model `{e['model_id']}` version `{e['model_version']}` (hashes of the config and "
           "of the fit readings).",
           f"- Fit day running time: {e['fit_running_hours']} h (minimum {min_fit_s / 3600:g} h).",
           f"- Normalisation: {e['normalization']}."]
    if e["abstained"]:
        return out + [f"- **Abstained:** {e['abstained']}. Every window for this pump is "
                      "`insufficient_evidence`; October was not scored."]
    if e["signal_abstentions"]:
        out.append("- Signals without a baseline: " + "; ".join(
            f"{s} ({why})" for s, why in e["signal_abstentions"].items()))
    out += ["", "Baseline bands (fit on June running windows):", "",
            "| signal | center | band low | band high | MAD of window medians | fit readings"
            " | min fresh per window |", "|---|---|---|---|---|---|---|"]
    for s, b in e["bands"].items():
        out.append(f"| {s} | {_f(b['center'], 5)} | {_f(b['low'], 5)} | {_f(b['high'], 5)}"
                   f" {b['unit']} | {_f(b['mad'], 3)} | {b['readings']} | {b['min_fresh']} |")
    eps = e["review_episodes"]
    out += ["", f"**October, scored once:** {e['score_running_hours']} running h, "
            f"{e['windows']} signal-windows ({e['scored_evidence']} ScoredEvidence records). "
            f"**{len(eps)} unlabelled review episodes = "
            f"{e['unlabelled_reviews_per_running_hour']} per running hour.**", "",
            "| signal | normal | review_suggested | insufficient_evidence | data_unavailable |",
            "|---|---|---|---|---|"]
    for s, c in e["states_by_signal"].items():
        out.append(f"| {s} | {c.get('normal', 0)} | {c.get('review_suggested', 0)} | "
                   f"{c.get('insufficient_evidence', 0)} | {c.get('data_unavailable', 0)} |")
    d = e.get("diagnostics") or {}
    if d:
        out += ["", "**Diagnostics (post-hoc, computed after the one October scoring; not used "
                "for tuning):** typical time between readings and median level of the eligible "
                "readings, June fit day vs October.", "",
                "| signal | June interval s | October interval s | June median |"
                " October median | October median vs June band |", "|---|---|---|---|---|---|"]
        for s, x in d.items():
            b = e["bands"].get(s)
            where = "-" if not b or x["score_median"] is None else (
                "inside" if b["low"] <= x["score_median"] <= b["high"] else "outside")
            out.append(f"| {s} | {_f(x['fit_interval_s'])} | {_f(x['score_interval_s'])} | "
                       f"{_f(x['fit_median'], 4)} | {_f(x['score_median'], 4)} | {where} |")
    if eps:
        out += ["", "| review episode: signal | start | end | windows | max band-relative"
                " score |", "|---|---|---|---|---|"]
        for ep in eps:
            out.append(f"| {ep['signal_name']} | {ep['start'][11:19]} | {ep['end'][11:19]}"
                       f" | {ep['windows']} | {_f(ep['max_score'], 3)} |")
    return out


def markdown(cfg: dict, tuning: list[dict], results: dict) -> str:
    fz = cfg["frozen"]
    sel = fz["selected"]
    out = [
        "# CIRA baseline scoring: protocol and evaluation", "",
        "Every result below is labelled **REAL** (measured CIRA data, scored as recorded) or "
        "**SYNTHETIC** (faults injected into in-memory copies of real readings, provenance "
        "`synthetic_injection`, never written to the database). CIRA has no fault labels (the "
        "descriptor describes normal operation), so a review on REAL data can be neither "
        "confirmed nor refuted: we count them as unlabelled review episodes.", "",
        "## Protocol", "",
        "1. **Tuning data: B_2024-06-11 only.** Running time is split in time: the first "
        f"{cfg['tuning']['split_fraction']:.0%} fits the baseline, the rest validates "
        f"({fz['split']['validation_running_hours']} running h). The tuning code loads no "
        "other pump-day (enforced by `test_tuning_only_ever_loads_b_june`).",
        "2. **Grid:** " + ", ".join(f"{k} {v}" for k, v in fz["grid"].items()) + f"; "
        f"{fz['starts_per_fault']} synthetic fault starts per fault type and size on the June "
        "validation part.",
        f"3. **Objective:** {fz['objective']}.",
        "4. **Freeze** the selected settings in `data/scoring_config.yaml`.",
        "5. **Fit on B June, score B October once** (REAL unlabelled review episodes per running "
        "hour).",
        "6. **Same for A** if its June fit passes the minimum-fit rule, otherwise record the "
        "abstention.",
        "7. **Synthetic injections on B October** with the frozen settings (SYNTHETIC).", "",
        "**Disclosure.** Earlier steps (audit, operating-state report, Table 1 checks) showed "
        "summary statistics of the October files, so the analyst was not blind to October "
        "before tuning. The tuning *procedure* never reads October data. The minimum fit "
        f"duration ({cfg['baseline']['min_fit_running_s'] / 3600:g} h) was chosen with pump A's "
        "June running time already known; see the exploratory A run below.", "",
        "## Frozen configuration", "",
        "```yaml", yaml.safe_dump({k: v for k, v in cfg.items() if k != "frozen"},
                                  sort_keys=False, default_flow_style=None, width=100).rstrip(),
        "```", "",
        f"Selected on June validation: window {sel['window_s']} s, step {sel['step_s']} s, "
        f"k = {sel['k']}, N = {sel['consecutive_windows']}: {sel['unlabelled_reviews']} "
        f"unlabelled review episodes ({sel['unlabelled_reviews_per_running_hour']} per running "
        "hour), mean synthetic "
        f"detection rate {sel['mean_detection_rate']:.0%}, median delay "
        f"{_min(sel['median_delay_s'])} min over {sel['injections']} injections.", "",
        "### Tuning table (June validation, best 12 of " + str(len(tuning)) + ")", "",
        "| window s | k | N | unlabelled reviews | per running h | mean detection"
        " | median delay min |",
        "|---|---|---|---|---|---|---|",
    ]
    ranked = sorted(tuning, key=lambda r: (r["unlabelled_reviews_per_running_hour"],
                                           -r["mean_detection_rate"],
                                           r["median_delay_s"] or 1e18))
    for r in ranked[:12]:
        out.append(f"| {r['window_s']} | {r['k']} | {r['consecutive_windows']} | "
                   f"{r['unlabelled_reviews']} | {r['unlabelled_reviews_per_running_hour']} | "
                   f"{r['mean_detection_rate']:.0%} | {_min(r['median_delay_s'])} |")

    for pump, e in results["pumps"].items():
        out += ["", f"## REAL: pump {pump} (fit {e['fit_day']}, score {e['score_day']})", ""]
        out += _pump_body(e, cfg["baseline"]["min_fit_running_s"])
        exp = e.get("exploratory")
        if exp:
            out += ["", f"### EXPLORATORY (REAL data, outside the protocol): pump {pump} with a "
                    f"{exp['min_fit_running_s'] / 3600:g} h minimum fit", "",
                    f"The protocol's {cfg['baseline']['min_fit_running_s'] / 3600:g} h minimum "
                    f"fit was chosen with pump {pump}'s June running time "
                    f"({e['fit_running_hours']} h) already known, so the abstention above was "
                    "foreseeable when the rule was set. This run lowers the minimum only to show "
                    "what the model would do. It is not a protocol result and was not used for "
                    "tuning.", ""]
            out += _pump_body(exp, exp["min_fit_running_s"])

    syn = results.get("synthetic")
    out += ["", "## SYNTHETIC: fault injection on B October", ""]
    if not syn:
        out.append("Not run: pump B has no baseline.")
        return "\n".join(out) + "\n"
    inj = cfg["injection"]
    out += [
        f"{syn['injections']} injections into in-memory copies of B_2024-10-30 readings "
        f"(provenance `{syn['provenance']}`), {syn['starts_per_fault']} start times per fault "
        "and size, each scored against the June baseline and compared with the clean October "
        "scoring. Signals: " + ", ".join(inj["signals"]) + ". Offsets are multiples of the "
        "signal's robust sigma on the fit day (1.4826 x MAD of June running readings).", "",
        "Faults: **step** (offset from t0), **ramp** (linear to full size over "
        f"{inj['ramp_s'] / 60:g} min), **drift** (linear over {inj['drift_s'] / 3600:g} h), "
        "**stuck** (no new readings for the duration; the last value is held), **dropout** "
        "(readings and samples removed for the duration).", "",
        "Detected means: step/ramp/drift -> `review_suggested` for that signal; stuck -> "
        "`insufficient_evidence` (too few fresh readings); dropout -> `data_unavailable` or "
        "`insufficient_evidence`; in each case in a window that was not already in that state "
        "in the clean scoring, and decided within the fault's horizon. Delay is from fault "
        "start to the end of the deciding window.", "",
        "| fault | size | injections | detected | rate | median delay min |",
        "|---|---|---|---|---|---|"]
    for c in syn["cells"]:
        out.append(f"| {c['fault']} | {c['size_label']} | {c['injections']} | {c['detected']} |"
                   f" {c['detection_rate']:.0%} | {_min(c['median_delay_s'])} |")
    share = syn.get("clean_normal_share", {})
    out += ["", "By signal (rate / median delay min). A fault can only be detected where the "
            "clean scoring was not already in the target state, so the share of clean October "
            "windows normal for each signal bounds what this table can show:", "",
            "| fault | size | " + " | ".join(syn["by_signal"]) + " |",
            "|---|---|" + "---|" * len(syn["by_signal"]),
            "| *clean October windows normal* | | " + " | ".join(
                f"*{share[s]:.0%}*" if s in share else "-" for s in syn["by_signal"]) + " |"]
    keys = [(c["fault"], c["size"], c["size_label"]) for c in syn["cells"]]
    for fault, size, label in keys:
        cells = []
        for rows in syn["by_signal"].values():
            m = next((r for r in rows if r["fault"] == fault and r["size"] == size), None)
            cells.append("-" if m is None else
                         f"{m['detection_rate']:.0%} / {_min(m['median_delay_s'])}")
        out.append(f"| {fault} | {label} | " + " | ".join(cells) + " |")
    out += ["", "**Limits of the synthetic track.** It measures this pipeline and its "
            "persistence rule, not real fault detection. Operating state, stale and spike flags "
            "come from the real recording and are not recomputed after injection, so an "
            "injected pressure fault never changes the running/off state, and an injected step "
            "is never itself flagged as a spike."]
    return "\n".join(out) + "\n"


# --- 3a-2: post-hoc revision after 3a results --------------------------------------------

LABEL = "post-hoc revision after 3a results"


def _rate(e: dict | None) -> str:
    if not e:
        return "-"
    if e.get("abstained"):
        return "abstained"
    return f"{len(e['review_episodes'])} / {e['unlabelled_reviews_per_running_hour']}"


def _hours(e: dict | None) -> str:
    if not e or e.get("abstained"):
        return "-"
    return _f(e.get("scored_running_hours", e.get("score_running_hours")))


def _shares(e: dict | None) -> str:
    if not e or e.get("abstained"):
        return "-"
    tot = {}
    for c in e["states_by_signal"].values():
        for k, v in c.items():
            tot[k] = tot.get(k, 0) + v
    n = sum(tot.values()) or 1
    return " / ".join(f"{100 * tot.get(k, 0) / n:.0f}%" for k in
                      ("normal", "review_suggested", "insufficient_evidence", "data_unavailable"))


def _cell(cells: list[dict] | None, fault: str, size: float) -> str:
    m = next((c for c in (cells or []) if c["fault"] == fault and c["size"] == size), None)
    return "-" if m is None else f"{m['detection_rate']:.0%} / {_min(m['median_delay_s'])}"


def _tuned(cfg: dict) -> dict:
    keep = {"features": cfg["features"], "baseline": cfg["baseline"], "review": cfg["review"]}
    if "within_run" in cfg:
        keep["within_run"] = cfg["within_run"]
    return keep


def revision_markdown(across: dict, within: dict, res: dict,
                      baseline_3a: dict | None = None) -> str:
    fa, fw = across["frozen"], within["frozen"]
    ad, wr = res["across_day"], res["within_run"]
    out = [
        "", f"# 3a-2: {LABEL}", "",
        f"**Everything in this part is a {LABEL}.** The changes were designed after the 3a "
        "October results were seen, so October is not an unseen test set for them. Tuning "
        "still used B June only and October was scored once per mode, but these numbers are "
        "weaker evidence than a blind result.", "",
        f"## What changed ({LABEL})", "",
        "- **Per-signal windows.** A window spans `window_readings` typical reading intervals of "
        "that signal on that day (rounded up to whole minutes), so it can hold enough fresh "
        "readings whatever the sensor's update rate. The fresh-reading minimum is a fixed "
        f"statistical {across['features']['min_fresh_readings']} readings, no longer derived "
        "from June's update rate (which excluded all October motor signals in 3a).",
        "- **Stuck sensors go through the stale flag.** A window overlapping a reading held past "
        "its stale limit (from `data/operating_rules.yaml`) is `insufficient_evidence` with "
        "reason `stale_suspected`. Stuck injections recompute that flag as the pipeline would, "
        "and only a stale decision counts as detecting them.",
        "- **Within-run mode.** Each running segment is scored against a band fit on its own "
        "first `baseline_s` seconds after the transition window, with a configurable minimum. "
        "It needs no other day, so it also scores pump A.", "",
        f"## Frozen configurations ({LABEL})", "",
        "Both tuned on B_2024-06-11 only and frozen under `revision_3a2` in "
        "`data/scoring_config.yaml`.", "",
        f"Across-day: {fa['objective']}. Grid " + ", ".join(
            f"{k} {v}" for k, v in fa["grid"].items()) + ".", "",
        "```yaml", yaml.safe_dump(_tuned(across), sort_keys=False, default_flow_style=None,
                                  width=100).rstrip(), "```", "",
        f"Within-run ({fw.get('mode', '')}): {fw['objective']}. Grid " + ", ".join(
            f"{k} {v}" for k, v in fw["grid"].items()) + ".", "",
        "```yaml", yaml.safe_dump(_tuned(within), sort_keys=False, default_flow_style=None,
                                  width=100).rstrip(), "```", "",
        f"## REAL: October scored once per mode ({LABEL})", "",
        "Unlabelled review episodes / per running hour (CIRA has no fault labels). Within-run "
        "hours exclude each run's baseline period. Window shares are normal / review / "
        "insufficient / unavailable over all signal-windows.", "",
        "| mode | pump | running h scored | unlabelled reviews / per h | window shares |",
        "|---|---|---|---|---|"]
    rows = []
    if baseline_3a:
        for pump, e in baseline_3a["pumps"].items():
            rows.append(("3a across-day (committed)", pump, e))
    for pump, e in ad["pumps"].items():
        rows.append(("3a-2 across-day", pump, e))
        if e.get("exploratory"):
            rows.append(("3a-2 across-day, EXPLORATORY 1 h minimum", pump, e["exploratory"]))
    for pump, e in wr["pumps"].items():
        rows.append(("3a-2 within-run", pump, e))
    for mode, pump, e in rows:
        out.append(f"| {mode} | {pump} | {_hours(e)} | {_rate(e)} | {_shares(e)} |")

    for title, e in (("3a-2 across-day, B", ad["pumps"]["B"]),
                     ("3a-2 within-run, B", wr["pumps"]["B"]),
                     ("3a-2 within-run, A", wr["pumps"].get("A"))):
        if not e or e.get("abstained"):
            continue
        out += ["", f"### {title}: states per signal ({LABEL})", "",
                "| signal | normal | review_suggested | insufficient_evidence | data_unavailable |",
                "|---|---|---|---|---|"]
        for s, c in e["states_by_signal"].items():
            out.append(f"| {s} | {c.get('normal', 0)} | {c.get('review_suggested', 0)} | "
                       f"{c.get('insufficient_evidence', 0)} | {c.get('data_unavailable', 0)} |")
        if e["review_episodes"]:
            out += ["", "| review episode: signal | start | end | windows | max score |",
                    "|---|---|---|---|---|"]
            for ep in e["review_episodes"]:
                out.append(f"| {ep['signal_name']} | {ep['start'][11:19]} | {ep['end'][11:19]}"
                           f" | {ep['windows']} | {_f(ep['max_score'], 3)} |")

    sa, sw = ad.get("synthetic"), wr.get("synthetic")
    s3 = (baseline_3a or {}).get("synthetic")
    out += ["", f"## SYNTHETIC: B October injections, both modes ({LABEL})", "",
            "Detection rate / median delay in minutes, all injected signals together. Same fault "
            "definitions as 3a, except that stuck must now be decided by the stale flag.", "",
            "| fault | size | across-day (3a-2) | within-run (3a-2) |"
            + (" 3a (committed) |" if s3 else ""),
            "|---|---|---|---|" + ("---|" if s3 else "")]
    keys = [(c["fault"], c["size"], c["size_label"]) for c in (sa or sw or {"cells": []})["cells"]]
    for fault, size, label in keys:
        row = (f"| {fault} | {label} | {_cell(sa and sa['cells'], fault, size)} | "
               f"{_cell(sw and sw['cells'], fault, size)} |")
        if s3:
            row += f" {_cell(s3['cells'], fault, size)} |"
        out.append(row)
    signals = list((sa or sw)["by_signal"]) if (sa or sw) else []
    if signals:
        out += ["", "By signal, across-day | within-run (rate / median delay min); the first row "
                "is the share of clean October windows that were normal, which bounds what can be "
                "detected:", "",
                "| fault | size | " + " | ".join(f"{s} across | {s} within" for s in signals)
                + " |", "|---|---|" + "---|---|" * len(signals),
                "| *clean October windows normal* | | " + " | ".join(
                    f"*{sa['clean_normal_share'].get(s, 0):.0%}* | "
                    f"*{sw['clean_normal_share'].get(s, 0):.0%}*" if sa and sw else "- | -"
                    for s in signals) + " |"]
        for fault, size, label in keys:
            cells = []
            for s in signals:
                cells.append(_cell(sa and sa["by_signal"].get(s), fault, size))
                cells.append(_cell(sw and sw["by_signal"].get(s), fault, size))
            out.append(f"| {fault} | {label} | " + " | ".join(cells) + " |")
    sv = res.get("stuck_via_stale", {})
    out += ["", f"**Stuck injections re-run ({LABEL}):** " + "; ".join(
        f"{mode}: {v['detected']} of {v['injections']} detected, {v['decided_by_stale_flag']} "
        "of them decided by the stale flag" for mode, v in sv.items()) + ".", ""]
    return "\n".join(out) + "\n"


# --- 3a-3: pre-registered final revision -------------------------------------------------

PREREG = "3a-3: pre-registered final revision"
FUTURE_WORK = [
    "Multi-day baselines per pump (fit on several running days) instead of one day or one run.",
    "Condition bands on operating point (pressure regime, flow) so load changes are not "
    "reviews.",
    "Per-device update-rate metadata (WirelessHART burst periods) instead of inferring them.",
    "Seasonal ambient handling beyond subtracting ambient temperature.",
    "Case feedback from operators, to turn unlabelled cases into labelled ones.",
    "A real labelled centrifugal-pump benchmark (the 4TU / Tata Steel candidate in ADR-0001).",
    "Online replay of the full pipeline, to measure end-to-end decision delay causally.",
]


ATTEMPT_1 = [
    "### First tuning attempt (discarded before October was scored)", "",
    "The first B June tuning used the fewest cases per running hour as its objective and "
    "onset limits derived from B June itself. It saturated: **89 of 90 settings tied at "
    "exactly 1 case**, because B June is one 6 h run and review episodes less than 15 min "
    "apart chained into a single case from 07:55 to 13:07 (44 episodes over 6 signals). The "
    "objective therefore selected nothing; the tie-break (highest synthetic detection) picked "
    "the most sensitive corner of the grid (k 3, N 2, baseline 20 min). The same-day onset "
    "limits also let outlet pressure's baseline start at 07:11, right at the run start.", "",
    "The objective and the onset rule were changed for these reasons **before October was "
    "scored** with any 3a-3 setting (the 3a and 3a-2 October results above predate 3a-3). The "
    "attempt's tuning "
    "table is kept in `reports/cira_tuning_3a3_attempt1.json`. Tuning was then re-run once, "
    "on B June only.", ""]


CORRECTIONS = [
    "## Post-pre-registration corrections", "",
    "Two changes were made to `src/pumpcopilot/scoring.py` after the pre-registration commit "
    "and after October was scored, while building the Step 4a replay worker. Neither is a "
    "detector revision.", "",
    "1. **`score_within_run_steady` no longer crashes on a run with no baseline yet.** With no "
    "signal's baseline complete, the per-signal cut times were all missing, and comparing "
    "window starts with them raised an error. Replay reaches that state early in every run; "
    "batch scoring of the scored days never did. The cut times are now typed as timestamps, "
    "so such a run simply scores nothing yet.",
    "2. **The stale-evidence reason reports the hold known at the window end.** It said "
    "`reading held N s` with the full hold of the reading, which is only known once the hold "
    "ends. It now gives the hold up to the window's end. Only the text changes: the window's "
    "state (`insufficient_evidence`, stale_suspected) is decided as before.", "",
    "**All states and cases above are unchanged.** October was re-scored with the corrected "
    "code for 3a, 3a-2 and 3a-3, in memory and without overwriting anything. States per "
    "signal, review episodes, cases per mode and the synthetic detections (detected or not, "
    "delay, detected-as state) all equal the stored results. The only differences are in the "
    "`reasons` text of synthetic stuck detections in the stored JSON results: 89 in 3a-2 and "
    "45 in 3a-3 now give the shorter, as-known hold. The stored JSON keeps the text as "
    "scored; this report does not show it. **This report regenerates identically** from the "
    "stored results; this section is the only addition.", "",
    "`pumpcopilot score eval-3a3 --prereg 385263d` now refuses to run, because "
    "`src/pumpcopilot` has changed since that commit. That is intended: the pre-registered "
    "scoring happened once, before these corrections.", ""]


def _protocol_3a3(cfg: dict) -> list[str]:
    fz = cfg["frozen"]
    o, c, wr = cfg["onset"], cfg["cases"], cfg["within_run"]
    q = fz.get("qualification", {})
    settle = ", ".join(f"{k} {v / 60:g} min" for k, v in o["settling_s"].items()
                       if v is not None)
    return [
        "## Protocol (written and committed before October was scored)", "",
        "1. **Tuning data: B_2024-06-11 only** (the tuning code loads no other pump-day).",
        f"2. **Settling times** per signal type, fixed in advance: {settle} after the run "
        "start. These are engineering assumptions, not tuned values (A7 in "
        "`docs/ASSUMPTIONS.md`).",
        "3. **Baseline** per signal and run: from the later of its settling time and its first "
        f"fresh reading in the run, for `baseline_s` (minimum {wr['min_baseline_s'] / 60:g} "
        f"min, {wr['min_baseline_readings']} readings, {wr['min_baseline_windows']} windows); "
        "only later windows of that signal are scored.",
        f"4. **Cases:** review episodes on the same asset and run less than "
        f"{c['gap_s'] / 60:g} min apart are one case. Case time is the sum of case durations. "
        "Rates use the day's total running hours for every mode. Cases per running hour are "
        "a reported result, not the objective.",
        f"5. **Qualification:** {q.get('starts', '-')} {q.get('size_sigma', 6):g}-sigma "
        f"{q.get('fault', 'step')} injections per injected signal, placed in B June's "
        f"validation portion (running time after the first "
        f"{cfg['tuning']['split_fraction']:.0%}, from {str(q.get('validation_from', '-'))[11:19]}"
        f" UTC). A setting qualifies with at least {q.get('min_detection', 0.8):.0%} of them "
        "detected.",
        "6. **Objective:** among qualifying settings, the least fraction of B June running "
        "time covered by a case. Tie-breaks: fewer cases, then the less sensitive setting "
        "(larger k, then larger N, then larger baseline_s and window_readings). If nothing "
        "qualifies: the highest qualification detection, the same tie-breaks, the requirement "
        "recorded as not met, and the grid not widened. Grid: " + ", ".join(
            f"{k} {v}" for k, v in fz["grid"].items()) + ".",
        "7. **Freeze** under `revision_3a3` in `data/scoring_config.yaml`, **commit** code, "
        "config and this protocol (tag `prereg-3a3`), then score **B October and A October "
        "once** with `pumpcopilot score eval-3a3 --prereg <commit>`, which checks that code and "
        "config are unchanged since that commit.",
        "8. **Also recompute cases for 3a and 3a-2** from their stored results (no re-tuning, "
        "no re-scoring), and run the same synthetic injections on B October.", ""] + ATTEMPT_1


def markdown_3a3(cfg: dict, prereg: dict | None, results: dict | None,
                 cases: dict | None) -> str:
    fz = cfg["frozen"]
    tuned = {k: cfg[k] for k in ("features", "baseline", "review", "within_run", "onset",
                                 "cases")}
    out = ["", f"# {PREREG}", "",
           "Unlike 3a-2, this revision was specified, tuned on B June and committed before any "
           "October scoring, and October is scored once.", ""]
    out += _protocol_3a3(cfg)
    sel = fz.get("selected", {})
    out += ["## Frozen configuration (`revision_3a3`)", "",
            "```yaml", yaml.safe_dump(tuned, sort_keys=False, default_flow_style=None,
                                      width=100).rstrip(), "```", ""]
    if sel:
        met = ("met" if fz.get("requirement_met") else
               "**NOT met: no setting qualified; fallback to the highest detection**")
        out += [f"Qualification requirement {met} ({fz.get('qualified')} of "
                f"{fz.get('candidates')} settings qualified).", "",
                f"Selected on B June: window_readings {sel.get('window_readings')}, baseline "
                f"{sel.get('baseline_s', 0) / 60:g} min, k = {sel.get('k')}, N = "
                f"{sel.get('consecutive_windows')}: 6-sigma step detection "
                f"{sel.get('qualify_detection_rate') or 0:.0%} of "
                f"{sel.get('qualify_injections')}; {sel.get('case_time_fraction', 0):.1%} of "
                f"running time in a case; {sel.get('cases')} cases "
                f"({sel.get('cases_per_running_hour')} per running hour); mean synthetic "
                f"detection {sel.get('mean_detection_rate', 0):.0%} over "
                f"{sel.get('injections')} injections (reported, not optimised).", ""]
    out += ["## Pre-registration", ""]
    if prereg is None:
        out += ["Pre-registration commit: this section is committed before scoring. **October: "
                "not yet scored.**", ""]
        return "\n".join(out) + "\n"
    ok = "unchanged" if prereg["unchanged"] else f"CHANGED: {', '.join(prereg['changed'])}"
    out += [f"Pre-registration commit: **`{prereg['commit']}`**. Checked before scoring: "
            f"{', '.join(prereg.get('paths', []))} {ok} since that commit.", ""]
    if results:
        out += ["## REAL: October scored once", "",
                "| pump | running h | unlabelled review episodes | cases | cases per run |"
                " cases per running hour | window shares normal / review / insufficient |",
                "|---|---|---|---|---|---|---|"]
        for pump, e in results["pumps"].items():
            cs = e["case_summary"]
            tot = {}
            for c in e["states_by_signal"].values():
                for k, v in c.items():
                    tot[k] = tot.get(k, 0) + v
            n = sum(tot.values()) or 1
            share = " / ".join(f"{100 * tot.get(k, 0) / n:.0f}%" for k in
                               ("normal", "review_suggested", "insufficient_evidence"))
            out.append(f"| {pump} | {e['running_hours']} | {len(e['review_episodes'])} | "
                       f"{cs['cases']} | {cs['cases_per_run']} | {cs['cases_per_running_hour']}"
                       f" | {share} |")
        for pump, e in results["pumps"].items():
            out += ["", f"### Pump {pump}: baselines per signal", "",
                    "| signal | first fresh reading | settled | baseline | band |",
                    "|---|---|---|---|---|"]
            for run in e["runs"]:
                for s, b in run["baselines"].items():
                    out.append(f"| {s} | {b['first_fresh'][11:19]} | {b['onset'][11:19]} | "
                               f"{b['baseline_start'][11:16]}-{b['baseline_end'][11:16]} | "
                               f"{_f(b['low'], 5)}-{_f(b['high'], 5)} {b['unit']} |")
                for s, why in run["signal_abstentions"].items():
                    out.append(f"| {s} | - | - | abstained: {why} | - |")
            out += ["", "| signal | normal | review_suggested | insufficient_evidence |"
                    " data_unavailable |", "|---|---|---|---|---|"]
            for s, c in e["states_by_signal"].items():
                out.append(f"| {s} | {c.get('normal', 0)} | {c.get('review_suggested', 0)} | "
                           f"{c.get('insufficient_evidence', 0)} | {c.get('data_unavailable', 0)}"
                           " |")
            if e["cases"]:
                out += ["", "| case | start | end | signals | episodes | max score |",
                        "|---|---|---|---|---|---|"]
                for i, c in enumerate(e["cases"], 1):
                    out.append(f"| {i} | {c['start'][11:19]} | {c['end'][11:19]} | "
                               f"{', '.join(c['signals'])} | {c['episodes']} | "
                               f"{_f(c['max_score'], 3)} |")
        syn = results.get("synthetic")
        if syn:
            out += ["", "## SYNTHETIC: B October injections", "",
                    "| fault | size | injections | detected | rate | median delay min |",
                    "|---|---|---|---|---|---|"]
            for c in syn["cells"]:
                out.append(f"| {c['fault']} | {c['size_label']} | {c['injections']} | "
                           f"{c['detected']} | {c['detection_rate']:.0%} | "
                           f"{_min(c['median_delay_s'])} |")
            share = syn["clean_normal_share"]
            out += ["", "By signal (rate / median delay min), with the share of clean October "
                    "windows that were normal:", "",
                    "| fault | size | " + " | ".join(syn["by_signal"]) + " |",
                    "|---|---|" + "---|" * len(syn["by_signal"]),
                    "| *clean October windows normal* | | " + " | ".join(
                        f"*{share.get(s, 0):.0%}*" for s in syn["by_signal"]) + " |"]
            for c in syn["cells"]:
                out.append(f"| {c['fault']} | {c['size_label']} | " + " | ".join(
                    _cell(rows, c["fault"], c["size"]) for rows in syn["by_signal"].values())
                    + " |")
            sv = results.get("stuck_via_stale")
            if sv:
                out += ["", f"Stuck: {sv['detected']} of {sv['injections']} detected, "
                        f"{sv['decided_by_stale_flag']} decided by the stale flag."]
    if cases:
        out += ["", "## Cases for every mode", "",
                f"Review episodes merged into cases (same asset and run, less than "
                f"{cfg['cases']['gap_s'] / 60:g} min apart). 3a and 3a-2 are recomputed from "
                "their stored results, not re-tuned or re-scored. Denominator: the day's total "
                "running hours for every mode, so cases per running hour are comparable.", "",
                "| mode | pump | cases | cases per run | cases per running hour |"
                " running time in a case |", "|---|---|---|---|---|---|"]
        for mode, pumps in cases.items():
            for pump, cs in pumps.items():
                if cs.get("abstained"):
                    out.append(f"| {mode} | {pump} | abstained | - | - | - |")
                else:
                    out.append(f"| {mode} | {pump} | {cs['cases']} | {cs['cases_per_run']} | "
                               f"{cs['cases_per_running_hour']} | "
                               f"{cs['case_time_fraction']:.1%} |")
    if results:
        out += [""] + CORRECTIONS
        out += ["## This is the last detector revision", "",
                "3a-3 is the last detector revision for CIRA. The detector code and "
                "configuration are frozen as scored above; further ideas are recorded here as "
                "future work and are not implemented.", "",
                "## Future work", ""] + [f"- {x}" for x in FUTURE_WORK]
    return "\n".join(out) + "\n"
