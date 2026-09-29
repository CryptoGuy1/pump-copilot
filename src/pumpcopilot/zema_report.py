"""reports/zema_benchmark.md from the frozen config and, once evaluated, the test results."""

from __future__ import annotations

from .zema_bench import MODEL_NAMES, SCOPE_NOTE, STRATA

SPLITS = (("chronological", "(c) chronological 60/20/20, 50-cycle gaps: PRIMARY"),
          ("grouped", "(b) grouped by leakage run"),
          ("random", "(a) stratified random: the naive number"))


def _ci(c: dict) -> str:
    return "-" if c.get("lo") is None else f"{c['lo']:.3f}–{c['hi']:.3f}"


def markdown(doc: dict, results: dict | None) -> str:
    fz = doc.get("frozen", {})
    out = [f"> **Hydraulic test rig only.** {SCOPE_NOTE}", "",
           "# ZeMA benchmark: hydraulic test rig pump leakage", "",
           "Target: pump leakage only. Every output label reads \"hydraulic test rig pump "
           "leakage state: k\" (k = 0 no leakage, 1 weak, 2 severe).", ""]
    if doc.get("protocol"):
        out += ["## Protocol (pre-registered)", ""] + [f"{i}. {p}" for i, p in
                                                        enumerate(doc["protocol"], 1)] + [""]
    if doc.get("splits"):
        out += ["## Splits", "", "| split | train | val | test | definition |",
                "|---|---|---|---|---|"]
        for name, sp in doc["splits"].items():
            parts = sp["parts"]
            out.append(f"| {name} | {parts['train']['n']} | {parts['val']['n']} | "
                       f"{parts['test']['n']} | {sp['definition']} |")
        out.append("")
    if fz.get("selected"):
        out += ["## Validation (tuning), frozen", "",
                "| split | model | selected settings | validation macro-F1 |",
                "|---|---|---|---|"]
        for split, _ in SPLITS:
            for model in MODEL_NAMES:
                s = fz["selected"].get(split, {}).get(model)
                if s:
                    out.append(f"| {split} | {model} | {s['params'] or '-'} | "
                               f"{s['val_macro_f1']:.3f} |")
        out.append("")
    if results is None:
        out += ["## Test", "", "**Not yet evaluated.** The test parts are read once, by "
                "`pumpcopilot zema eval --prereg prereg-3b`, after the pre-registration "
                "commit.", ""]
        return "\n".join(out) + "\n"
    out += [f"## Test (evaluated once, pre-registration `{results['preregistration']['tag']}`"
            f" = `{results['preregistration']['commit'][:12]}`)", "",
            "Intervals are 95% block-bootstrap percentile intervals over leakage label runs "
            "(the number of runs in each test part is given), not over cycles.", "",
            "| split | model | macro-F1 | 95% interval | recall 0 / 1 / 2 | runs | Brier |",
            "|---|---|---|---|---|---|---|"]
    for split, title in SPLITS:
        for model in MODEL_NAMES:
            r = results["splits"][split][model]
            rec = " / ".join("-" if v is None else f"{v:.2f}" for v in
                             r["test"]["recall"].values())
            out.append(f"| {title if model == MODEL_NAMES[0] else ''} | {model} | "
                       f"{r['test']['macro_f1']:.3f} | {_ci(r['ci95']['macro_f1'])} | {rec} | "
                       f"{r['ci95']['n_blocks']} | {r['calibration']['brier']:.3f} |")
    out.append("")
    for split, title in SPLITS:
        out += [f"### {title}: confusion matrices (rows true 0/1/2, columns predicted)", ""]
        for model in MODEL_NAMES:
            cm = results["splits"][split][model]["test"]["confusion"]
            out.append(f"- {model}: {cm}")
        out += ["", f"### {title}: stratified macro-F1", "",
                "| model | " + " | ".join(f"{c}={v}" for c in STRATA for v in
                                          results["splits"][split][MODEL_NAMES[0]]
                                          ["stratified"][c]) + " |",
                "|---|" + "---|" * sum(len(results["splits"][split][MODEL_NAMES[0]]
                                           ["stratified"][c]) for c in STRATA)]
        for model in MODEL_NAMES:
            st = results["splits"][split][model]["stratified"]
            out.append(f"| {model} | " + " | ".join(
                f"{st[c][v]['macro_f1']:.2f} (n {st[c][v]['n']})" for c in STRATA
                for v in st[c]) + " |")
        out.append("")
    return "\n".join(out) + "\n"
