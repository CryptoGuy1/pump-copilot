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
    args = p.parse_args(argv)

    if args.cmd == "acquire":
        from .acquire import acquire

        lock = acquire(DATA / "manifest.yaml", DATA / "raw", args.only)
        if "cira" in lock:
            print("CIRA record metadata:", json.dumps(lock["cira"].get("zenodo"), indent=2))
    elif args.dataset == "zema":
        _write("zema_audit", zema.audit(DATA / "raw" / "zema"))
    else:
        _write("cira_audit", cira.audit(DATA / "raw" / "cira"))


if __name__ == "__main__":
    main()
