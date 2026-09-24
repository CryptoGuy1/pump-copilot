from pathlib import Path

import pandas as pd
import pytest
import yaml

from pumpcopilot import cira
from pumpcopilot.schema import QualityFlag

HEADERS = ["ACR_Mot.PV", "ACR_Mot.SV", "ACR_Mot.TV", "ACR_Pmp.PV", "ACR_Pmp.SV", "ACR_Pmp.TV",
           "Pres.PV", "Temp.PV", "Barometer", "Temperature"]
MAP = {h: {"signal": h.lower().replace(".", "_"), "unit": "x"} for h in HEADERS}
COLUMN_MAP = Path(__file__).resolve().parents[1] / "data" / "cira_columns.yaml"
Z_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _write(df: pd.DataFrame, path: Path, **kw) -> Path:
    df.to_csv(path, index=False, **kw)
    return path


def test_directory_audit(cira_dir):
    r = cira.audit(cira_dir)
    assert r["ok"], r["issues"]
    assert r["pumps"] == ["A", "B", "C"]
    absent = r["absent_asset_days"]
    assert absent == [{"pump": "C", "day": "2024-10-30",
                       "explained": "pump off that day per dataset listing"}]


def test_file_audit_measures_cadence_dups_and_gaps(cira_dir):
    a = cira.audit_file(cira_dir / "A_2024-04-10.csv")
    assert [s["cadence_s"] for s in a["cadence_segments"]] == [10.0]
    assert a["duplicate_timestamps"] == 1
    b = cira.audit_file(cira_dir / "B_2024-04-10.csv")
    assert b["gaps_over_factor"]["count"] == 1


def test_unexplained_absence_is_an_issue(cira_dir):
    (cira_dir / "B_2024-06-12.csv").unlink()
    r = cira.audit(cira_dir)
    assert not r["ok"]
    assert any("B 2024-06-12" in i for i in r["issues"])


# --- format sniffing ---------------------------------------------------------------------

def test_sniffs_semicolon_delimiter_and_decimal_comma(tmp_path, make_cira_frame):
    df = make_cira_frame("A", "2024-10-30 10:30:00", 40, ts_fmt=Z_FMT)
    p = _write(df, tmp_path / "A_2024-10-30.csv", sep=";", decimal=",")
    a = cira.audit_file(p)
    assert a["format"] == {"delimiter": ";", "decimal": ","}
    assert a["issues"] == []
    assert len(a["columns"]) == 10
    assert a["columns"]["A_Pres.PV"]["min"] == pytest.approx(df["A_Pres.PV"].min())


def test_sniffs_comma_delimiter_and_decimal_point(cira_dir):
    a = cira.audit_file(cira_dir / "A_2024-04-10.csv")
    assert a["format"] == {"delimiter": ",", "decimal": "."}


# --- strict audit checks -----------------------------------------------------------------

def test_wrong_column_count_is_an_issue(cira_dir):
    p = cira_dir / "A_2024-04-10.csv"
    _write(pd.read_csv(p).drop(columns=["Barometer"]), p)
    a = cira.audit_file(p)
    assert any("1 timestamp + 10 measurement columns" in i for i in a["issues"])
    r = cira.audit(cira_dir)
    assert not r["ok"]
    assert any(i.startswith("A_2024-04-10.csv:") for i in r["issues"])


@pytest.mark.parametrize("n_bad, flagged", [(1, False), (3, True)])
def test_unparseable_timestamp_share_over_1pct_is_an_issue(
    tmp_path, make_cira_frame, n_bad, flagged
):
    df = make_cira_frame("A", "2024-04-10 08:00:00", 200)
    df.loc[: n_bad - 1, "Timestamp"] = "not a time"
    a = cira.audit_file(_write(df, tmp_path / "A_2024-04-10.csv"))
    assert a["unparseable_timestamps"] == n_bad
    assert any("unparseable" in i for i in a["issues"]) is flagged


@pytest.mark.parametrize("start, flagged", [
    ("2024-04-09 22:00:00", False),   # 2 h before the day: inside the margin
    ("2024-04-09 20:00:00", True),    # 4 h before
    ("2024-04-11 02:30:00", False),   # ends 02:36:30 next day: inside the margin
    ("2024-04-11 03:30:00", True),
    ("1970-01-01 00:00:00", True),
])
def test_span_off_filename_day_is_an_issue(tmp_path, make_cira_frame, start, flagged):
    df = make_cira_frame("A", start, 40, ts_fmt=Z_FMT)
    a = cira.audit_file(_write(df, tmp_path / "A_2024-04-10.csv"))
    assert any("span" in i for i in a["issues"]) is flagged


# --- blank rows --------------------------------------------------------------------------

def test_blank_rows_are_dropped_and_counted_separately(cira_dir):
    p = cira_dir / "A_2024-04-10.csv"
    with open(p, "a") as f:
        f.write("," * 10 + "\n")
    a = cira.audit_file(p)
    assert a["blank_rows"] == 1
    assert a["unparseable_timestamps"] == 0
    assert a["rows"] == 40


# --- timezones ---------------------------------------------------------------------------

def test_utc_marker_is_not_relocalized(tmp_path, make_cira_frame):
    df = make_cira_frame("A", "2024-04-10 12:00:00", 40, ts_fmt=Z_FMT)
    a = cira.audit_file(_write(df, tmp_path / "A_2024-04-10.csv"))
    assert a["timestamp_assumption"] == (
        "UTC marker present; README says no timezone; confirm with authors"
    )
    assert a["span"][0] == "2024-04-10 12:00:00+00:00"


def test_naive_timestamps_default_to_utc(cira_dir):
    a = cira.audit_file(cira_dir / "A_2024-04-10.csv")
    assert a["timestamp_assumption"] == (
        "naive timestamps treated as UTC (data descriptor sec. 3.1: gateway timestamps in UTC)"
    )
    assert a["span"][0] == "2024-04-10 08:00:00+00:00"


def test_naive_timezone_can_be_overridden(cira_dir):
    df, ts_col, info = cira.load(cira_dir / "A_2024-04-10.csv", tz="Europe/Rome")
    assert str(df[ts_col].iloc[0]) == "2024-04-10 06:00:00+00:00"  # 08:00 CEST
    assert info["timestamp_assumption"] == "naive timestamps localized to Europe/Rome (verify)"


# --- cadence -----------------------------------------------------------------------------

def test_cadence_is_reported_as_segments(tmp_path, make_cira_frame):
    t0 = pd.Timestamp("2024-04-10 12:00:00")
    slow = [t0 + pd.Timedelta(seconds=5 * i) for i in range(100)]
    fast = [slow[-1] + pd.Timedelta(seconds=1 + i) for i in range(200)]
    after_gap = [fast[-1] + pd.Timedelta(seconds=60 + i) for i in range(100)]
    df = make_cira_frame("A", timestamps=slow + fast + after_gap, ts_fmt=Z_FMT)
    a = cira.audit_file(_write(df, tmp_path / "A_2024-04-10.csv"))
    segs = a["cadence_segments"]
    assert [(s["cadence_s"], s["steps"]) for s in segs] == [(5.0, 99), (1.0, 300)]
    assert segs[0]["start"] == "2024-04-10 12:00:00+00:00"
    assert segs[1]["start"] == "2024-04-10 12:08:15+00:00"
    # the 5 s steps are that segment's cadence, not gaps; the 60 s jump is one gap
    assert a["gaps_over_factor"]["count"] == 1
    assert a["gaps_over_factor"]["items"][0]["seconds"] == 60.0


# --- site-level gaps ---------------------------------------------------------------------

def test_gap_shared_by_all_pumps_is_recorded_once_at_site_level(cira_dir, make_cira_frame):
    for pump in "ABC":
        df = make_cira_frame(pump, "2024-04-10 08:00:00", 40, gap_at=1)
        _write(df, cira_dir / f"{pump}_2024-04-10.csv")
    r = cira.audit(cira_dir)
    assert r["site_gaps"] == [{"day": "2024-04-10", "after": "2024-04-10 08:06:30+00:00",
                               "seconds": 600.0, "pumps": ["A", "B", "C"]}]
    by_file = {f["file"]: f["gaps_over_factor"] for f in r["files"]}
    for pump in "ABC":
        assert by_file[f"{pump}_2024-04-10.csv"]["count"] == 0
        assert by_file[f"{pump}_2024-04-10.csv"]["site_level"] == 1
    # only B has a gap on the other days: it stays a pump-level gap
    assert by_file["B_2024-06-12.csv"]["count"] == 1
    assert by_file["B_2024-06-12.csv"]["site_level"] == 0


# --- events and column map ---------------------------------------------------------------

def test_events_require_full_column_map(cira_dir):
    with pytest.raises(KeyError, match="unmapped"):
        list(cira.to_events(cira_dir / "A_2024-04-10.csv", {"Pres.PV": MAP["Pres.PV"]}))


def test_events_keep_unprefixed_headers_intact(cira_dir):
    # "Barometer" starts with the pump letter B; it must not become "arometer"
    ev = list(cira.to_events(cira_dir / "B_2024-04-10.csv", MAP))
    assert {e.signal_name for e in ev} == {spec["signal"] for spec in MAP.values()}


def test_events_flags_and_idempotency(cira_dir):
    path = cira_dir / "A_2024-04-10.csv"
    ev = list(cira.to_events(path, MAP))
    assert len(ev) == 40 * 10
    assert all(e.observed_at.tzinfo is not None for e in ev)
    assert all(QualityFlag.UNIT_UNVERIFIED in e.quality_flags for e in ev)
    assert sum(QualityFlag.DUPLICATE_TIMESTAMP in e.quality_flags for e in ev) == 10
    again = list(cira.to_events(path, MAP))
    assert [e.provenance_hash for e in ev] == [e.provenance_hash for e in again]
    assert len({e.provenance_hash for e in ev}) == len(ev)


def test_column_map_matches_real_headers_and_descriptor():
    spec = yaml.safe_load(COLUMN_MAP.read_text())
    assert set(spec) == set(HEADERS)
    assert all(v["signal"] and v["unit"] for v in spec.values())
    # Table 2 of the descriptor names these two X_Temp.SV / X_Pres.SV, not .PV
    verified = {k for k, v in spec.items() if v["unit_verified"] is True}
    assert verified == set(HEADERS) - {"Temp.PV", "Pres.PV"}


def test_column_map_converts_every_pump(cira_dir):
    spec = yaml.safe_load(COLUMN_MAP.read_text())
    for pump in "ABC":
        ev = list(cira.to_events(cira_dir / f"{pump}_2024-04-10.csv", spec))
        assert len({e.signal_name for e in ev}) == 10
