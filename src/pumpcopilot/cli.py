"""pumpcopilot acquire | audit | rules | state | db | score | replay | worker | case"""

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
    dbp.add_argument("action", choices=["migrate", "load", "perf", "storage", "reset"])
    dbp.add_argument("dataset", nargs="?", choices=["cira"], default="cira")
    dbp.add_argument("--yes-i-mean-it", action="store_true",
                     help="reset: drop and recreate the (local only) database")
    sc = sub.add_parser("score", help="tune (B June only, freezes config) or eval (fit/score)")
    sc.add_argument("action", choices=["tune", "tune-revision", "eval", "tune-3a3", "report",
                                       "eval-3a3"])
    sc.add_argument("dataset", nargs="?", choices=["cira"], default="cira")
    sc.add_argument("--prereg", help="eval-3a3: the pre-registration commit")
    rp = sub.add_parser("replay", help="replay sessions: create, list, pause, resume, rewind,"
                        " latency, verify")
    rp.add_argument("action", choices=["create", "list", "pause", "resume", "rewind",
                                       "latency", "verify"])
    rp.add_argument("target", nargs="?", help="session id, or the asset for create")
    rp.add_argument("day", nargs="?", help="create: the source day (YYYY-MM-DD)")
    rp.add_argument("--speed", type=int, default=60, choices=[1, 10, 60])
    rp.add_argument("--scenario", help="create: a synthetic scenario (in memory only)")
    wk = sub.add_parser("worker", help="claim replay sessions and advance their cursors")
    wk.add_argument("--poll", type=float, default=0.1, help="seconds between idle polls")
    wk.add_argument("--until-idle", action="store_true",
                    help="exit when no session is pending or running")
    wk.add_argument("--max-seconds", type=float)
    zp = sub.add_parser("zema", help="ZeMA hydraulic test rig pump-leakage benchmark (3b)")
    zp.add_argument("action", choices=["features", "tune", "eval", "report"])
    zp.add_argument("--prereg", help="eval: the pre-registration tag (prereg-3b)")
    asp = sub.add_parser("assistant", help="copilot assistant: adversarial evaluation, ping")
    asp.add_argument("action", choices=["eval", "ping", "keyscan", "report", "rescore"])
    asp.add_argument("--no-dotenv", action="store_true", help="do not load .env")
    asp.add_argument("--provider", choices=["fake", "template", "anthropic"], default="fake")
    asp.add_argument("--set", dest="which", default="adversarial",
                     help="anthropic: comma-separated sets from adversarial, adversarial_r1, "
                          "benign, holdout, holdout2 (or both = adversarial,benign)")
    asp.add_argument("--ledger", default=None,
                     help="anthropic: a named request ledger (reports/anthropic_requests_"
                          "<name>.json) with its own --cap; default: the Step 6B ledger")
    asp.add_argument("--cap", type=int, default=None, help="anthropic: that ledger's cap")
    asp.add_argument("--timeout", type=float, default=0.2,
                     help="seconds before the fake model's slow answers time out")
    ap = sub.add_parser("api", help="serve the HTTP API and live stream on 127.0.0.1 only")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--export-openapi", action="store_true",
                    help="write api/openapi.json and exit")
    ap.add_argument("--no-dotenv", action="store_true",
                    help="do not load .env (the end-to-end test never calls a real model)")
    cs = sub.add_parser("case", help="cases: list, show, ack, note, dispose, close, export")
    cs.add_argument("action", choices=["list", "show", "ack", "note", "dispose", "close",
                                       "export"])
    cs.add_argument("case_id", nargs="?", type=int)
    cs.add_argument("--session", type=int)
    cs.add_argument("--by", help="who is acting (default: $USER)")
    cs.add_argument("--text", help="note text")
    cs.add_argument("--disposition", help="one of: " + "; ".join(
        ["monitor", "known condition, no action", "data quality issue",
         "escalate to reliability engineer (export only)"]))
    cs.add_argument("--reason", help="why (required for a disposition)")
    cs.add_argument("--out", help="export: file to write (default reports/case_<id>.json)")
    sn = sub.add_parser("snapshot", help="the static snapshot: record answers, export files")
    sn.add_argument("action", choices=["record", "export"])
    sn.add_argument("--no-dotenv", action="store_true", help="record: do not load .env")
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
    elif args.cmd == "db" and args.action == "reset":
        _db_reset(args.yes_i_mean_it)
    elif args.cmd == "db":
        _db(args.action)
    elif args.cmd == "replay":
        _replay(args)
    elif args.cmd == "worker":
        from . import db, replay

        with db.connect() as conn:
            db.migrate(conn)
            replay.Worker(conn).run(poll_s=args.poll, until_idle=args.until_idle,
                                    max_s=args.max_seconds)
    elif args.cmd == "case":
        _case(args)
    elif args.cmd == "api":
        _api(args.port, args.export_openapi, args.no_dotenv)
    elif args.cmd == "snapshot":
        _snapshot(args.action, args.no_dotenv)
    elif args.cmd == "zema":
        _zema(args.action, args.prereg)
    elif args.cmd == "assistant" and args.action == "ping":
        _assistant_ping(args.no_dotenv)
    elif args.cmd == "assistant" and args.action == "keyscan":
        _assistant_keyscan(args.no_dotenv)
    elif args.cmd == "assistant" and args.action == "report":
        _assistant_report()
    elif args.cmd == "assistant" and args.action == "rescore":
        _assistant_rescore()
    elif args.cmd == "assistant" and args.provider == "anthropic":
        _assistant_eval_real(args.which, args.no_dotenv, args.ledger, args.cap)
    elif args.cmd == "assistant":
        _assistant_eval(args.provider, args.timeout)
    elif args.cmd == "score":
        if args.action in ("tune-3a3", "report", "eval-3a3"):
            _score_3a3(args.action, args.prereg)
        else:
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


def _db_reset(confirmed: bool) -> None:
    from . import db

    url = db.database_url()
    if not db.is_local_url(url):
        raise SystemExit(f"refusing to reset {db._describe(url)}: not a local database")
    if not confirmed:
        raise SystemExit(f"db reset drops {db._describe(url)} entirely; rerun with "
                         "--yes-i-mean-it")
    res = db.reset_database(url, confirm=True)
    print(f"[reset] {res['database']} dropped, recreated and migrated "
          f"({len(res['applied'])} migrations); load data again with `pumpcopilot db load cira`")


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
        elif action == "storage":
            rep = db.storage_report(conn)
            print(json.dumps(rep, indent=2, default=str))
        elif action == "perf":
            res = db.perf_check(conn, "cira-pump-B", dt.date(2024, 6, 11),
                                dt.datetime(2024, 6, 11, 10, 0, tzinfo=dt.UTC))
            REPORTS.mkdir(exist_ok=True)
            (REPORTS / "db_perf.json").write_text(json.dumps(res, indent=2))
            print(f"  {res['asset_id']} {res['source_day']}:")
            for k in ("day_1m", "hour_raw"):
                print(f"    {k:9} {res[k]['rows']:7d} rows  median {res[k]['median_ms']} ms"
                      f"  best {res[k]['best_ms']} ms  ({res['repeats']} runs)")


ZEMA_CONFIG = DATA / "zema_benchmark.yaml"
ZEMA_RESULTS = REPORTS / "zema_benchmark.json"
ZEMA_PREREG_PATHS = ["data/zema_benchmark.yaml", "src/pumpcopilot"]
ZEMA_PREREG_TAG = "prereg-3b-r2"
ZEMA_SUPERSEDES = {
    "tag": "prereg-3b", "commit": "eae1dbde5e5a7b14327a45a063e0f93ea6866cbc",
    "test_parts_evaluated": False,
    "reason": "Revised on review before any test evaluation: the test parts of prereg-3b "
              "were never opened, so this is a new pre-registration, not a post-hoc change.",
    "changes": [
        "strata: counts and per-class recall always; macro-F1 only for strata with at least "
        "2 classes and 30 cycles, otherwise 'too few cycles'",
        "ZeMA scores carry cycle_id and time_is_placeholder: true (the time window is a "
        "cycle x 60 s placeholder)",
        "the stable-flag shortcut baseline is class-balanced in all three splits; the "
        "stable-flag x leakage table and its mutual information over all cycles are reported",
        "the headline model per split is pre-registered: the best validation macro-F1 among "
        "majority, logistic regression and gradient boosting, ties to the simpler",
    ],
}
ZEMA_PROTOCOL = [
    "Target: hydraulic test rig pump leakage (0, 1, 2) only.",
    "Features: per-cycle mean, std, min, max, 5/25/50/75/95th percentiles and slope per "
    "measured channel, plus a spectral summary (dominant frequency, centroid, power share "
    "below 1 Hz, 1-5 Hz, above 5 Hz) for the 100 Hz channels. Virtual channels CE, CP, SE "
    "excluded.",
    "Splits: (a) stratified random 60/20/20; (b) grouped by contiguous leakage run (5 "
    "stratified group folds: test, validation, 3 x train); (c) chronological 60/20/20 with a "
    "50-cycle gap on each side of the validation part: the PRIMARY result.",
    "Splits (a) and (b) are built from the leakage label sequence of all cycles, as their "
    "definitions require (stratification, run boundaries); only the assignment of cycles to "
    "parts is kept. (c) uses cycle order alone. No test label is used to fit, tune or score "
    "anything before the evaluation.",
    "Models: majority class, logistic regression, gradient boosting (scikit-learn); and two "
    "baselines: the stable flag alone (shortcut) and the four other condition labels "
    "(conditions only: not observable in practice, shows the confounding).",
    "Tuning: each model's settings are chosen by macro-F1 on the validation part of each "
    "split (fit on train). The final fit is on the train part only.",
    "Evaluation: once, on the test parts, by `pumpcopilot zema eval --prereg prereg-3b`, "
    "which refuses to run if src/pumpcopilot or this config changed since the tag.",
    "Metrics: macro-F1, per-class recall, confusion matrices; 95% block-bootstrap intervals "
    "(2000 resamples) over leakage label runs; every result stratified by the stable flag and "
    "the cooler, valve and accumulator levels; Brier score and reliability curves on the test "
    "part. ScoredEvidence is marked calibrated only where that measurement exists.",
    "Strata: counts and per-class recall are always reported; macro-F1 only for strata with "
    "at least 2 classes and at least 30 cycles, otherwise 'too few cycles'.",
    "The stable-flag shortcut baseline is fitted with class-balanced weighting (each class "
    "weighted by 1 / its frequency) in all three splits.",
    "The stable-flag x leakage table and its mutual information over all cycles are computed "
    "by the evaluation (they include test cycles). The Step 1 audit already reported the "
    "condition x leakage crosstabs over all cycles (reports/zema_audit.json).",
    "ZeMA cycles have no clock: each ScoredEvidence carries cycle_id and "
    "time_is_placeholder: true, and its time window is cycle x 60 s from 1970-01-01.",
    "prereg-3b (eae1dbd) is superseded by this revision before any test evaluation; its test "
    "parts were never opened. The tag is kept.",
]


def _zema_inputs():
    from . import zema
    from . import zema_bench as zb

    raw = DATA / "raw" / "zema"
    feats, key = zb.load_features(raw, DATA / "cache")
    labels = zema.load_labels(zema.find_root(raw))
    y = labels["pump_leakage"].to_numpy()
    runs = zb.leakage_runs(y)
    return feats, key, labels, y, runs


def _zema(action: str, prereg: str | None) -> None:
    import yaml

    from . import zema_bench as zb
    from . import zema_report

    if action == "report":
        doc = yaml.safe_load(ZEMA_CONFIG.read_text())
        res = json.loads(ZEMA_RESULTS.read_text()) if ZEMA_RESULTS.exists() else None
        (REPORTS / "zema_benchmark.md").write_text(zema_report.markdown(doc, res))
        print(f"[written] {REPORTS / 'zema_benchmark.md'}")
        return
    token = None
    if action == "eval":  # refuse before loading anything
        if not prereg:
            raise SystemExit(f"zema eval needs --prereg {ZEMA_PREREG_TAG}")
        if prereg != ZEMA_PREREG_TAG:
            raise SystemExit(f"{prereg} is not the current pre-registration "
                             f"({ZEMA_PREREG_TAG}); prereg-3b was superseded before any "
                             "test evaluation")
        try:
            token = zb.EvaluationToken.issue(prereg, ZEMA_PREREG_PATHS)
        except zb.PreregistrationError as e:
            raise SystemExit(str(e)) from None
    feats, key, labels, y, runs = _zema_inputs()
    if action == "features":
        print(f"[features] {feats.shape[0]} cycles x {feats.shape[1]} features, cache {key}")
        return
    if action == "tune":
        grouped, seed_used = zb.split_grouped(y, runs, 0, return_seed=True)
        splits = {"random": zb.split_random(y, 0), "grouped": grouped,
                  "chronological": zb.split_chronological(len(y), gap=50)}
        vault = zb.LabelVault(labels, splits)
        frozen = zb.tune(feats, vault, splits, zb.small_grid())
        heads = {s: zb.headline(frozen["selected"][s]) for s in splits}
        doc = {
            "label": "3b-r2: pre-registered ZeMA benchmark, revision 2",
            "preregistration_tag": ZEMA_PREREG_TAG, "supersedes": ZEMA_SUPERSEDES,
            "scope_note": zb.SCOPE_NOTE,
            "protocol": ZEMA_PROTOCOL + [
                "Headline model per split (the best validation macro-F1 among "
                f"{', '.join(zb.HEADLINE_CANDIDATES)}; equal to 3 decimals goes to the simpler, "
                "in that order): " + "; ".join(f"{s}: {m}" for s, m in heads.items()) + "."],
            "headline": heads,
            "features": {"version": zb.FEATURE_VERSION, "channels": zb.channel_set(),
                         "include_virtual": False, "stats": list(zb.STATS),
                         "spectral": list(zb.SPECTRAL), "cache_key": key,
                         "n_cycles": int(feats.shape[0]), "n_features": int(feats.shape[1])},
            "splits": {
                "random": {"definition": "stratified random 60/20/20, seed 0",
                           "seed": 0, "parts": zb.split_fingerprint(splits["random"])},
                "grouped": {"definition": f"5 stratified group folds over {runs.max() + 1} "
                            f"leakage runs, seed {seed_used}", "seed": seed_used,
                            "parts": zb.split_fingerprint(grouped)},
                "chronological": {"definition": "cycle order 60/20/20, 50-cycle gap on each "
                                  "side of validation", "gap": 50,
                                  "parts": zb.split_fingerprint(splits["chronological"])}},
            "models": {m: {"inputs": zb.MODEL_INPUTS[m], "grid": zb.small_grid()[m]}
                       for m in zb.MODEL_NAMES},
            "bootstrap": {"n_boot": 2000, "seed": 0, "unit": "leakage label run"},
            "calibration": {"bins": 10}, "strata": list(zb.STRATA),
            "frozen": frozen,
        }
        ZEMA_CONFIG.write_text(yaml.safe_dump(doc, sort_keys=False, width=100))
        (REPORTS / "zema_benchmark.md").write_text(zema_report.markdown(doc, None))
        for split in ("chronological", "grouped", "random"):
            print(f"  {split:13} " + ", ".join(
                f"{m} {frozen['selected'][split][m]['val_macro_f1']:.3f}"
                for m in zb.MODEL_NAMES))
        print(f"  headline: {heads}")
        print(f"[frozen] {ZEMA_CONFIG}; commit it and tag {ZEMA_PREREG_TAG} before `zema eval`")
        return
    # eval: once, with the pre-registered code and config only (token checked above)
    doc = yaml.safe_load(ZEMA_CONFIG.read_text())
    if key != doc["features"]["cache_key"]:
        raise SystemExit("features differ from the pre-registered ones (raw files changed)")
    grouped = zb.split_grouped(y, runs, doc["splits"]["grouped"]["seed"], tries=1)
    splits = {"random": zb.split_random(y, doc["splits"]["random"]["seed"]),
              "grouped": grouped, "chronological": zb.split_chronological(len(y), gap=50)}
    for name, parts in splits.items():
        if zb.split_fingerprint(parts) != doc["splits"][name]["parts"]:
            raise SystemExit(f"split {name} differs from the pre-registered one")
    vault = zb.LabelVault(labels, splits)
    vault.unlock(token)
    results = zb.evaluate(feats, vault, lambda split, idx: runs[idx], splits, doc["frozen"],
                          n_boot=doc["bootstrap"]["n_boot"], seed=doc["bootstrap"]["seed"],
                          bins=doc["calibration"]["bins"], headlines=doc["headline"])
    results["headline"] = doc["headline"]
    results["shortcut_analysis"] = zb.shortcut_analysis(labels)
    results["preregistration"] = {"tag": token.tag, "commit": token.commit,
                                  "paths": ZEMA_PREREG_PATHS, "unchanged": True}
    results["calibration_measured"] = {s: {m: True for m in zb.MODEL_NAMES} for s in splits}
    ZEMA_RESULTS.write_text(json.dumps(results, indent=2, default=str))
    (REPORTS / "zema_benchmark.md").write_text(zema_report.markdown(doc, results))
    for split in ("chronological", "grouped", "random"):
        print(f"  {split:13} " + ", ".join(
            f"{m} {results['splits'][split][m]['test']['macro_f1']:.3f}"
            for m in zb.MODEL_NAMES))
    print(f"[written] {ZEMA_RESULTS}, {REPORTS / 'zema_benchmark.md'}")


def _snapshot(action: str, no_dotenv: bool) -> None:
    """The static snapshot, from the database at DATABASE_URL (scripts/snapshot_source.sh
    prepares it). `record` makes real model requests: 6, within a ledger capped at 6."""
    from . import db, snapshot

    url = db.database_url()
    if action == "record":
        if not no_dotenv and ENV_FILE.exists():
            from dotenv import load_dotenv

            load_dotenv(ENV_FILE, override=False)
        rec = snapshot.record_answers(url)
        for a in rec["answers"]:
            r = a["response"]
            why = "" if r["served"] == "assistant" else f" ({r['fallback_reason']})"
            kind = "synthetic" if a["synthetic"] else "real"
            print(f"[answer] case {a['case_id']} ({kind}), {a['question']!r}: {r['served']}{why}")
        print(f"[recorded] {len(rec['answers'])} answers, {rec['requests_used']} of {rec['cap']}"
              f" requests used, model {rec['model']} -> reports/snapshot_answers.json")
    else:
        m = snapshot.export(url)
        print(f"[exported] {m['files']} files, {m['bytes'] / 1e6:.2f} MB -> web/public/snapshot/"
              f" (commit {m['commit'][:7]}{', uncommitted changes' if m['commit_dirty'] else ''})")


def _assistant_ping(no_dotenv: bool) -> None:
    """Whether a key is present and the model answers. Never prints the key or any of it."""
    import os

    from . import assistant

    if not no_dotenv and ENV_FILE.exists():
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)
    r = assistant.ping(assistant.AnthropicProvider(max_retries=0,
                                                   ledger=assistant.RequestLedger()))
    key = os.environ.get("ANTHROPIC_API_KEY")
    say = lambda s: print(assistant.redact(s, key))  # noqa: E731
    if not r["key_present"]:
        say("key: not set (ANTHROPIC_API_KEY); the assistant serves the evidence summary")
        raise SystemExit(1)
    say("key: present")
    if r["responds"]:
        say(f"model responds: yes ({r['model']}, {r['latency_ms']} ms)")
        return
    say(f"model responds: no ({r['model']}): {r['error']}")
    raise SystemExit(1)


SETS = {"adversarial": 50, "adversarial_r1": 15, "benign": 20, "holdout": 20, "holdout2": 20}
HOLDOUT_FILE = "data/assistant_benign_holdout.yaml"
HOLDOUT_FILES = {"holdout": HOLDOUT_FILE, "holdout2": "data/assistant_benign_holdout2.yaml"}


def holdout_commit(path: str = HOLDOUT_FILE) -> str | None:
    """The commit that last changed a holdout file, if it is tracked and unchanged since;
    otherwise None (it must be committed before any real-model run on it)."""
    import subprocess

    def git(*a):
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True)
    if git("ls-files", "--error-unmatch", path).returncode != 0:
        return None
    if git("diff", "--quiet", "HEAD", "--", path).returncode != 0:
        return None
    sha = git("log", "-1", "--format=%H", "--", path).stdout.strip()
    return sha or None


def _load_env(no_dotenv: bool) -> None:
    if not no_dotenv and ENV_FILE.exists():
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)


def _assistant_eval_real(which: str, no_dotenv: bool, ledger_name: str | None = None,
                         cap: int | None = None) -> None:
    """The real model, once per set, within a ledger's cap. Results are saved after each
    set; on a failure the run stops and says how many requests were used."""
    import os

    from . import assistant, db

    _load_env(no_dotenv)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("key: not set (ANTHROPIC_API_KEY)")
    sets = ["adversarial", "benign"] if which == "both" else [x.strip() for x in
                                                               which.split(",") if x.strip()]
    if set(sets) - set(SETS):
        raise SystemExit(f"unknown sets {sorted(set(sets) - set(SETS))}; use {sorted(SETS)}")
    if ledger_name:
        if cap is None:
            raise SystemExit("--ledger needs --cap")
        ledger = assistant.RequestLedger(REPORTS / f"anthropic_requests_{ledger_name}.json",
                                         cap=cap)
    else:
        ledger = assistant.RequestLedger()
    holdout_sha, holdout_file = None, None
    for x in [x for x in sets if x in HOLDOUT_FILES]:
        holdout_file = HOLDOUT_FILES[x]
        holdout_sha = holdout_commit(holdout_file)
        if not holdout_sha:
            raise SystemExit(f"{holdout_file} must be committed, and unchanged since, before a "
                             f"real-model run on the {x} set: not started")
    planned = sum(SETS[x] for x in sets)
    if ledger.remaining() < planned:
        raise SystemExit(f"{planned} requests planned, only {ledger.remaining()} left of the "
                         f"cap of {ledger.cap}: not started (ask before raising a cap)")
    p = assistant.AnthropicProvider(max_retries=0, ledger=ledger)
    out = {"model": p.model, "checker_version": assistant.CHECKER_VERSION,
           "ledger": str(ledger.path.name), "cap": ledger.cap,
           "requests_before": ledger.used(), "sets": sets, "holdout_commit": holdout_sha,
           "holdout_file": holdout_file}
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / (f"assistant_eval_{ledger_name}.json" if ledger_name
                      else "assistant_anthropic_eval.json")
    names = {x: x for x in SETS}

    def save(incomplete: str | None = None) -> None:
        """After each set, so a crash in a later set cannot lose finished results."""
        out["requests_after"] = ledger.used()
        done = [names[x] for x in sets if names[x] in out]
        usage = {k: sum(out[s]["usage"][k] for s in done)
                 for k in ("requests", "input_tokens", "output_tokens")}
        out.update(usage=usage, estimated_cost_usd=assistant.cost_usd(usage),
                   price_note="estimate from ASSUMED prices in data/assistant.yaml",
                   incomplete=incomplete)
        path.write_text(assistant.redact(json.dumps(out, indent=2, default=str), key))

    try:
        for x in sets:
            if x in ("adversarial", "adversarial_r1"):
                items = assistant.load_adversarial(
                    assistant.ADVERSARIAL_R1 if x == "adversarial_r1" else assistant.ADVERSARIAL)
                out[x] = assistant.run_adversarial("anthropic", provider_obj=p, items=items)
                out[x]["label"] = ("revision-1 adversarial additions" if x == "adversarial_r1"
                                   else "adversarial")
            else:
                with db.connect() as conn:
                    items = assistant.load_benign({"holdout": assistant.BENIGN_HOLDOUT,
                                                   "holdout2": assistant.BENIGN_HOLDOUT2}.get(
                                                       x, assistant.BENIGN))
                    out[names[x]] = assistant.run_benign(conn, p, items=items)
                out[names[x]]["label"] = ("holdout, written blind" if x == "holdout"
                                          else "second holdout, written blind"
                                          if x == "holdout2"
                                          else "after revision, seen" if ledger_name
                                          else "benign")
            save()
            if out[names[x]].get("stopped"):
                raise RuntimeError(out[names[x]]["stopped"])
    except Exception as e:
        save(incomplete=assistant.redact(str(e), key)[:300])
        print(assistant.redact(f"STOPPED: {type(e).__name__}: {e}; requests used "
                               f"{ledger.used()}/{ledger.cap}; partial results in {path}", key))
        raise SystemExit(1) from None
    say = lambda s: print(assistant.redact(s, key))  # noqa: E731
    for x in sets:
        r = out[names[x]]
        if x in ("adversarial", "adversarial_r1"):
            say(f"{x}: {r['total']} run; raw answers passing the checker by themselves "
                f"{r['raw_passed_checker']}; final {r['passed']}/{r['total']} "
                f"({r['pass_rate']:.0%}); latency p50 {r['latency_ms']['p50']} ms, p95 "
                f"{r['latency_ms']['p95']} ms")
        else:
            say(f"{x} ({r['label']}): {r['total']} run; Assistant, checked "
                f"{r['served']['assistant']}; fell back {r['served']['template']}; latency "
                f"p50 {r['latency_ms']['p50']} ms, p95 {r['latency_ms']['p95']} ms")
    u = out["usage"]
    say(f"usage: {u['requests']} answered requests, {u['input_tokens']} input and "
        f"{u['output_tokens']} output tokens; estimated ${out['estimated_cost_usd']} "
        f"(assumed prices); ledger {out['requests_after']}/{ledger.cap}")
    say(f"[written] {path}")


def _assistant_keyscan(no_dotenv: bool) -> None:
    """Scan for the key without ever printing it; writes reports/assistant_keyscan.json."""
    import os

    from . import assistant

    _load_env(no_dotenv)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("key: not set (ANTHROPIC_API_KEY); nothing to scan for")
    res = assistant.key_scan(key)
    (REPORTS / "assistant_keyscan.json").write_text(
        assistant.redact(json.dumps(res, indent=2), key))
    print(assistant.redact(json.dumps(res, indent=2), key))
    if not res["clean"]:
        raise SystemExit(1)


def _assistant_rescore() -> None:
    """The stored revision-2 adversarial outputs, re-scored offline (no model calls): as
    registered, and under the corrected expectations."""
    from . import assistant

    stored = json.loads((REPORTS / "assistant_eval_r2.json").read_text())["adversarial"]
    registered = assistant.load_adversarial(assistant.ADVERSARIAL_REGISTERED_R2)
    current = assistant.load_adversarial()
    reg = assistant.rescore_adversarial(stored, registered)
    cor = assistant.rescore_adversarial(stored, current)
    if (reg["passed"], len(reg["failures"])) != (stored["passed"], len(stored["failures"])):
        raise SystemExit(f"re-scoring as registered gives {reg['passed']}/{reg['total']}, "
                         f"not the stored {stored['passed']}/{stored['total']}: not written")
    old = {i["id"]: i["expected"] for i in registered}
    changed = [i["id"] for i in current if i["expected"] != old.get(i["id"])]
    out = {"source": "reports/assistant_eval_r2.json", "model_calls": 0,
           "adversarial": {"registered": reg, "corrected": cor, "changed_items": changed}}
    path = REPORTS / "assistant_eval_r2_rescored.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"adversarial: {reg['passed']}/{reg['total']} as registered; {cor['passed']}/"
          f"{cor['total']} under corrected expectations ({', '.join(changed)}); no model calls")
    print(f"[written] {path}")


def _assistant_report() -> None:
    from . import assistant_report

    out = REPORTS / "assistant_eval.md"
    out.write_text(assistant_report.markdown(REPORTS))
    print(f"[written] {out}")


def _assistant_eval(provider: str, timeout_s: float) -> None:
    from . import assistant

    rep = assistant.run_adversarial(provider, timeout_s=timeout_s)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"assistant_adversarial_{provider}.json").write_text(json.dumps(rep, indent=2))
    print(f"[assistant eval] {provider}: {rep['passed']}/{rep['total']} final answers pass "
          f"({rep['pass_rate']:.0%}); served: {rep['served']['assistant']} checked assistant, "
          f"{rep['served']['template']} evidence summary"
          + (f"; fake outputs rejected: {rep['fake_outputs_rejected']}"
             if provider == "fake" else ""))
    for f in rep["failures"]:
        print(f"  FAIL {f['id']}: {'; '.join(f['problems'])}")
    if rep["failures"]:
        raise SystemExit(1)


OPENAPI = ROOT / "api" / "openapi.json"
ENV_FILE = ROOT / ".env"  # the API key lives here; gitignored, see .env.example


def _api(port: int, export_openapi: bool, no_dotenv: bool = False) -> None:
    from . import api

    if not export_openapi and not no_dotenv and ENV_FILE.exists():
        # the API only (and so `make dev`); variables already in the environment win
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)
    app = api.create_app()
    if export_openapi:
        OPENAPI.parent.mkdir(exist_ok=True)
        OPENAPI.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
        print(f"[written] {OPENAPI}")
        return
    import uvicorn

    uvicorn.run(app, host=api.HOST, port=port)  # loopback only; there is no --host option


def _replay(args) -> None:
    import datetime as dt

    from . import db, replay

    with db.connect() as conn:
        db.migrate(conn)
        if args.action == "create":
            if not (args.target and args.day):
                raise SystemExit("replay create needs ASSET DAY, e.g. cira-pump-B 2024-10-30")
            sid = replay.create_session(conn, args.target, dt.date.fromisoformat(args.day),
                                        args.speed, scenario=args.scenario,
                                        stale_limits=_stale_limits())
            print(f"[created] replay session {sid}: {args.target} {args.day} at "
                  f"{args.speed}x" + (f", SYNTHETIC scenario {args.scenario}"
                                      if args.scenario else "") + " (status pending)")
            return
        if args.action == "list":
            for s in replay.list_sessions(conn):
                print(f"  {s['session_id']:4d} {s['asset_id']} {s['source_day']} "
                      f"{s['speed']:2d}x {s['status']:9} cursor {s['cursor_at']}"
                      + (f"  SYNTHETIC {s['scenario']}" if s["synthetic"] else ""))
            return
        if not args.target:
            raise SystemExit(f"replay {args.action} needs a session id")
        sid = int(args.target)
        if args.action == "pause":
            replay.pause_session(conn, sid)
        elif args.action == "resume":
            replay.resume_session(conn, sid)
        elif args.action == "rewind":
            replay.rewind_session(conn, sid)
        elif args.action == "latency":
            res = replay.latency(conn, sid)
            REPORTS.mkdir(exist_ok=True)
            (REPORTS / f"replay_latency_{sid}.json").write_text(json.dumps(res, indent=2))
            print(json.dumps(res, indent=2))
            return
        elif args.action == "verify":
            print(json.dumps(replay.verify(conn, sid), indent=2))
            return
        s = replay.get_session(conn, sid)
        print(f"[{args.action}] session {sid}: {s['status']}, cursor {s['cursor_at']}")


def _case(args) -> None:
    import getpass

    from . import cases, db

    actor = args.by or getpass.getuser()
    with db.connect() as conn:
        db.migrate(conn)
        if args.action == "list":
            for c in cases.list_cases(conn, session_id=args.session):
                print(f"  {c['case_id']:5d} session {c['session_id']} {c['asset_id']} run "
                      f"{c['stretch']} {c['evidence_start']:%H:%M}-{c['evidence_end']:%H:%M} "
                      f"{c['status']:13} {c['episodes']} episodes {sorted(c['signals'])}"
                      + ("  SYNTHETIC" if c["synthetic"] else ""))
            return
        if args.case_id is None:
            raise SystemExit(f"case {args.action} needs a case id")
        cid = args.case_id
        if args.action == "ack":
            cases.acknowledge(conn, cid, actor)
        elif args.action == "note":
            cases.note(conn, cid, actor, args.text or "")
        elif args.action == "dispose":
            cases.dispose(conn, cid, actor, args.disposition or "", args.reason or "")
        elif args.action == "close":
            cases.close(conn, cid, actor)
        elif args.action == "export":
            out = Path(args.out) if args.out else REPORTS / f"case_{cid}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(cases.export(conn, cid), indent=2, default=str))
            print(f"[exported] case {cid} -> {out}")
            return
        print(json.dumps(cases.get_case(conn, cid), indent=2, default=str))


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


# 3a-3, pre-registered final revision: fixed before tuning
# A7: fixed settling times after the run start, engineering assumptions (not tuned)
ONSET = {"settling_s": {"pressure": 300, "vibration": 600, "temperature": 1800}}
CASES = {"gap_s": 900}
GRID_3A3 = {"k": [3.0, 4.0, 5.0, 6.0, 8.0], "consecutive_windows": [2, 3, 5],
            "window_readings": [6, 10], "baseline_s": [1200, 1800, 3600], "step_s": [60]}
PREREG_PATHS = ["data/scoring_config.yaml", "data/operating_rules.yaml",
                "data/cira_columns.yaml", "src/pumpcopilot", "migrations"]


def _full_report(doc: dict, results_3a3: dict | None = None, prereg: dict | None = None,
                 cases: dict | None = None) -> str:
    """The whole scoring report from stored results: 3a, 3a-2 and 3a-3 (nothing re-scored)."""
    from . import scoring, scoring_report

    doc = dict(doc)
    rev, rev3 = doc.pop("revision_3a2", None), doc.pop("revision_3a3", None)
    cfg = scoring.merge_config(doc)
    table = json.loads((REPORTS / "cira_tuning.json").read_text())
    res3a = json.loads((REPORTS / "cira_scoring_eval.json").read_text())
    text = scoring_report.markdown(cfg, table, res3a)
    if rev:
        res2 = json.loads((REPORTS / "cira_scoring_eval_3a2.json").read_text())
        text += scoring_report.revision_markdown(
            scoring.merge_config(rev["across_day"]), scoring.merge_config(rev["within_run"]),
            res2, baseline_3a=res3a)
    if rev3:
        text += scoring_report.markdown_3a3(scoring.merge_config(rev3), prereg, results_3a3,
                                            cases)
    return text


def scoring_report_text() -> str:
    """reports/cira_scoring_eval.md from stored results only (3a, 3a-2 and, when stored, the
    3a-3 results and the cases in every mode); nothing is scored."""
    import yaml

    doc = yaml.safe_load((DATA / "scoring_config.yaml").read_text())
    res_path = REPORTS / "cira_scoring_eval_3a3.json"
    cases_path = REPORTS / "cira_cases_all_modes.json"
    if not res_path.exists():
        return _full_report(doc)
    results = json.loads(res_path.read_text())
    cases = json.loads(cases_path.read_text()) if cases_path.exists() else None
    return _full_report(doc, results, results.get("preregistration"), cases)


def _score_3a3(action: str, prereg_commit: str | None) -> None:
    import datetime as dt

    import yaml

    from . import db, scoring, scoring_report

    cfg_path = DATA / "scoring_config.yaml"
    doc = yaml.safe_load(cfg_path.read_text())
    md = REPORTS / "cira_scoring_eval.md"
    if action == "report":
        md.write_text(scoring_report_text())
        print(f"[written] {md} (from stored results; nothing scored)")
        return
    limits = _stale_limits()
    with db.connect() as conn:
        def loader(asset_id, source_day):
            return scoring.load_day(conn, asset_id, source_day, stale_limits=limits)

        if action == "tune-3a3":
            base = scoring.merge_config({"features": REVISION_FEATURES,
                                         "within_run": WITHIN_RUN, "onset": ONSET,
                                         "cases": CASES})
            frozen, table = scoring.tune_3a3(loader, base, GRID_3A3)
            doc["revision_3a3"] = frozen
            cfg_path.write_text(scoring_report.config_yaml(doc))
            (REPORTS / "cira_tuning_3a3.json").write_text(json.dumps(table, indent=2,
                                                                     default=str))
            sel = frozen["frozen"]["selected"]
            fz = frozen["frozen"]
            print(f"[frozen] revision_3a3: window_readings {sel['window_readings']}, baseline "
                  f"{sel['baseline_s']} s, k {sel['k']}, N {sel['consecutive_windows']}; "
                  f"requirement met {fz['requirement_met']} ({fz['qualified']}/"
                  f"{fz['candidates']} qualified); 6-sigma step detection "
                  f"{sel['qualify_detection_rate']} of {sel['qualify_injections']}; case time "
                  f"{sel['case_time_fraction']:.1%}; June cases {sel['cases']} "
                  f"({sel['cases_per_running_hour']}/h)")
            return

        # eval-3a3: only after the pre-registration commit, and only if nothing changed since
        if not prereg_commit:
            raise SystemExit("eval-3a3 needs --prereg <commit>")
        check = scoring.verify_preregistration(prereg_commit, PREREG_PATHS)
        if not check["unchanged"]:
            raise SystemExit(f"changed since pre-registration {prereg_commit}: "
                             f"{check['changed']}")
        cfg = scoring.merge_config(doc["revision_3a3"])
        results = scoring.evaluate_3a3(loader, cfg)
        running = {p: loader(f"cira-pump-{p}", dt.date(2024, 10, 30)).running for p in "BA"}
    results["preregistration"] = check
    (REPORTS / "cira_scoring_eval_3a3.json").write_text(json.dumps(results, indent=2,
                                                                   default=str))
    gap = cfg["cases"]["gap_s"]
    res3a = json.loads((REPORTS / "cira_scoring_eval.json").read_text())
    res2 = json.loads((REPORTS / "cira_scoring_eval_3a2.json").read_text())
    cases = {"3a across-day": scoring.cases_for_results(res3a, running, gap),
             "3a-2 across-day": scoring.cases_for_results(res2["across_day"], running, gap),
             "3a-2 within-run": scoring.cases_for_results(res2["within_run"], running, gap),
             "3a-3 within-run steady": {p: e["case_summary"]
                                        for p, e in results["pumps"].items()}}
    (REPORTS / "cira_cases_all_modes.json").write_text(json.dumps(cases, indent=2, default=str))
    md.write_text(_full_report(doc, results, check, cases))
    print(f"[written] {md}")
    for mode, pumps in cases.items():
        for pump, c in pumps.items():
            print(f"  {mode:24} {pump:15} " + (c["abstained"] if c.get("abstained") else
                  f"{c['cases']} cases, {c['cases_per_running_hour']} per running hour"))
