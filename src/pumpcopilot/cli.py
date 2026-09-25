"""pumpcopilot acquire | audit zema|cira | rules cira | state cira | db migrate|load|perf"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import cira, zema

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
REPORTS = ROOT / "reports"


def _write(name: str, report: dict) -> None:
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"{name}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    status = "OK" if report.get("ok") else "ISSUES"
    print(f"[{status}] {out}")
    for issue in report.get("issues", []):
        print(f"  - {issue}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="pumpcopilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("acquire", help="download sources listed in data/manifest.yaml")
    a.add_argument("--only", nargs="*")
    au = sub.add_parser("audit", help="audit a downloaded dataset")
    au.add_argument("dataset", choices=["zema", "cira"])
    ru = sub.add_parser("rules", help="derive operating rules -> data/operating_rules.yaml")
    ru.add_argument("dataset", choices=["cira"])
    st = sub.add_parser("state", help="operating state and quality flags per pump-day")
    st.add_argument("dataset", choices=["cira"])
    dbp = sub.add_parser("db", help="Timescale: migrate, load cira, perf (uses DATABASE_URL)")
    dbp.add_argument("action", choices=["migrate", "load", "perf"])
    dbp.add_argument("dataset", nargs="?", choices=["cira"], default="cira")
    sc = sub.add_parser("score", help="tune (B June only, freezes config) or eval (fit/score)")
    sc.add_argument("action", choices=["tune", "tune-revision", "eval"])
    sc.add_argument("dataset", nargs="?", choices=["cira"], default="cira")
    args = p.parse_args(argv)

    if args.cmd == "acquire":
        from .acquire import acquire

        lock = acquire(DATA / "manifest.yaml", DATA / "raw", args.only)
        if "cira" in lock:
            print("CIRA record metadata:", json.dumps(lock["cira"].get("zenodo"), indent=2))
    elif args.cmd == "rules":
        _derive_rules()
    elif args.cmd == "state":
        _state_report()
    elif args.cmd == "db":
        _db(args.action)
    elif args.cmd == "score":
        _score(args.action)
    elif args.dataset == "zema":
        _write("zema_audit", zema.audit(DATA / "raw" / "zema"))
    else:
        _write("cira_audit", cira.audit(DATA / "raw" / "cira"))


def _derive_rules() -> None:
    """Re-derive thresholds and stale limits; keeps hand-edited settings/stale/spike values."""
    from . import operating

    path = DATA / "operating_rules.yaml"
    old = operating.load_rules(path) if path.exists() else {}
    rules, derivation = operating.derive_rules(
        DATA / "raw" / "cira", settings=old.get("settings"), stale=old.get("stale"),
        spike=old.get("spike"), vibration=old.get("vibration"),
        state_signal=old.get("state_signal", "Pres.PV"))
    operating.write_rules(rules, path)
    report = operating.state_report(DATA / "raw" / "cira", rules)
    REPORTS.mkdir(exist_ok=True)
    md = REPORTS / "cira_operating_rules.md"
    md.write_text(operating.derivation_markdown(rules, derivation, report))
    print(f"[written] {path}\n[written] {md}")
    for pump, r in rules["pumps"].items():
        m = r["vibration"]
        print(f"  {pump}: off below {r['lower_bar']} bar, running above {r['upper_bar']} bar, "
              f"min state {r['min_state_s']} s, transition {r['transition_s']} s; "
              f"motor idle below {m['lower']}, running above {m['upper']} m/s^2")


def _state_report() -> None:
    from . import operating

    rules = operating.load_rules(DATA / "operating_rules.yaml")
    report = operating.state_report(DATA / "raw" / "cira", rules)
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / "cira_state.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"[written] {out}")
    print(f"  {'pump-day':14} {'running':>8} {'off':>8} {'trans':>6} {'unknown':>8} "
          f"{'changes':>7} {'stale':>6} {'spike':>6}")
    for key, d in report["pump_days"].items():
        s = d["seconds"]
        print(f"  {key:14} {s['running']:8.0f} {s['off']:8.0f} {s['transition']:6.0f} "
              f"{s['unknown']:8.0f} {d['state_changes']:7d} {sum(d['stale'].values()):6d} "
              f"{sum(d['spike'].values()):6d}")


if __name__ == "__main__":
    main()


def _db(action: str) -> None:
    import datetime as dt

    import yaml

    from . import db

    with db.connect() as conn:
        applied = db.migrate(conn)
        print(f"[migrate] applied {applied or 'nothing (up to date)'}")
        if action == "load":
            column_map = yaml.safe_load((DATA / "cira_columns.yaml").read_text())
            runs = db.load_cira(conn, DATA / "raw" / "cira", column_map,
                                DATA / "operating_rules.yaml")
            conn.execute("ANALYZE telemetry; ANALYZE readings")
            print(f"  {'file':18} {'telemetry +':>12} {'skipped':>9} {'readings +':>11}"
                  f" {'skipped':>8} {'segments +':>10} {'skipped':>8}")
            for r in runs:
                print(f"  {r['file']:18} {r['telemetry_inserted']:12d} {r['telemetry_skipped']:9d}"
                      f" {r['readings_inserted']:11d} {r['readings_skipped']:8d}"
                      f" {r['segments_inserted']:10d} {r['segments_skipped']:8d}")
        elif action == "perf":
            res = db.perf_check(conn, "cira-pump-B", dt.date(2024, 6, 11),
                                dt.datetime(2024, 6, 11, 10, 0, tzinfo=dt.UTC))
            REPORTS.mkdir(exist_ok=True)
            (REPORTS / "db_perf.json").write_text(json.dumps(res, indent=2))
            print(f"  {res['asset_id']} {res['source_day']}:")
            for k in ("day_1m", "hour_raw"):
                print(f"    {k:9} {res[k]['rows']:7d} rows  median {res[k]['median_ms']} ms"
                      f"  best {res[k]['best_ms']} ms  ({res['repeats']} runs)")


TUNING_GRID = {"k": [3.0, 4.0, 5.0, 6.0, 8.0], "consecutive_windows": [2, 3, 5],
               "window_s": [300, 600, 1200], "step_s": [60]}
# 3a-2, post-hoc revision after 3a results
REVISION_FEATURES = {"window_readings": 10, "min_fresh_readings": 5, "window_floor_s": 60,
                     "stale_via_flag": True, "step_s": 60}
REVISION_GRID = {"k": [3.0, 4.0, 5.0, 6.0, 8.0], "consecutive_windows": [2, 3, 5],
                 "window_readings": [6, 10, 20], "step_s": [60]}
WITHIN_RUN = {"baseline_s": 1800, "min_baseline_s": 900, "min_baseline_readings": 15,
              "min_baseline_windows": 5}
WITHIN_RUN_GRID = {**REVISION_GRID, "baseline_s": [1200, 1800, 3600]}


def _stale_limits() -> dict[str, float]:
    """Per-signal stale limits from the operating rules, keyed by canonical signal name."""
    import yaml

    rules = yaml.safe_load((DATA / "operating_rules.yaml").read_text())
    column_map = yaml.safe_load((DATA / "cira_columns.yaml").read_text())
    per = rules["stale"]["per_signal"]
    return {spec["signal"]: float(per.get(short, rules["stale"]["default_s"]))
            for short, spec in column_map.items()}


def _score(action: str) -> None:
    import yaml

    from . import db, scoring, scoring_report

    cfg_path = DATA / "scoring_config.yaml"
    REPORTS.mkdir(exist_ok=True)
    limits = _stale_limits()
    with db.connect() as conn:
        def loader(asset_id, source_day):
            return scoring.load_day(conn, asset_id, source_day, stale_limits=limits)

        if action == "tune":
            frozen, table = scoring.tune(loader, scoring.merge_config({}), TUNING_GRID)
            cfg_path.write_text(scoring_report.config_yaml(frozen))
            (REPORTS / "cira_tuning.json").write_text(json.dumps(table, indent=2, default=str))
            sel = frozen["frozen"]["selected"]
            print(f"[frozen] {cfg_path}: window {sel['window_s']} s, k {sel['k']}, "
                  f"N {sel['consecutive_windows']}; June validation unlabelled reviews "
                  f"{sel['unlabelled_reviews']}, mean detection {sel['mean_detection_rate']:.0%}")
            return
        if action == "tune-revision":
            base = scoring.merge_config({"features": REVISION_FEATURES})
            across, t_a = scoring.tune(loader, base, REVISION_GRID)
            within, t_w = scoring.tune_within_run(
                loader, scoring.merge_config(base, {"within_run": WITHIN_RUN}), WITHIN_RUN_GRID)
            doc = yaml.safe_load(cfg_path.read_text())
            doc["revision_3a2"] = {"label": scoring.REVISION_LABEL, "across_day": across,
                                   "within_run": within}
            cfg_path.write_text(scoring_report.config_yaml(doc))
            (REPORTS / "cira_tuning_3a2.json").write_text(
                json.dumps({"across_day": t_a, "within_run": t_w}, indent=2, default=str))
            for name, fz in (("across-day", across), ("within-run", within)):
                sel = fz["frozen"]["selected"]
                print(f"[frozen] {name}: " + ", ".join(
                    f"{k} {sel[k]}" for k in ("window_readings", "baseline_s", "k",
                                              "consecutive_windows") if k in sel)
                      + f"; June unlabelled reviews {sel['unlabelled_reviews']}, "
                      f"mean detection {sel['mean_detection_rate']:.0%}")
            return
        doc = yaml.safe_load(cfg_path.read_text())
        rev = doc.pop("revision_3a2", None)
        cfg = scoring.merge_config(doc)
        table = json.loads((REPORTS / "cira_tuning.json").read_text())
        results = scoring.evaluate(loader, cfg)
        revision = None
        if rev:
            across = scoring.merge_config(rev["across_day"])
            within = scoring.merge_config(rev["within_run"])
            revision = scoring.evaluate_revision(loader, across, within)
    (REPORTS / "cira_scoring_eval.json").write_text(json.dumps(results, indent=2, default=str))
    md = REPORTS / "cira_scoring_eval.md"
    text = scoring_report.markdown(cfg, table, results)
    if revision:
        text += scoring_report.revision_markdown(across, within, revision, baseline_3a=results)
        (REPORTS / "cira_scoring_eval_3a2.json").write_text(
            json.dumps(revision, indent=2, default=str))
    md.write_text(text)
    print(f"[written] {md}")
    for pump, e in results["pumps"].items():
        if e["abstained"]:
            print(f"  {pump}: abstained: {e['abstained']}")
        else:
            print(f"  {pump}: {len(e['review_episodes'])} unlabelled review episodes in "
                  f"{e['score_running_hours']} running h = "
                  f"{e['unlabelled_reviews_per_running_hour']} per running hour")
    if revision:
        print(f"  3a-2 ({scoring.REVISION_LABEL}):")
        for mode in ("across_day", "within_run"):
            for pump, e in revision[mode]["pumps"].items():
                what = e["abstained"] if e.get("abstained") else (
                    f"{len(e['review_episodes'])} unlabelled review episodes = "
                    f"{e['unlabelled_reviews_per_running_hour']} per running hour")
                print(f"    {mode} {pump}: {what}")
