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
