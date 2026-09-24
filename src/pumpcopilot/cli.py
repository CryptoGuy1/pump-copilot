"""pumpcopilot acquire | audit zema | audit cira"""

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
