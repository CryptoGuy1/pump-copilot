import pytest

from pumpcopilot import cira
from pumpcopilot.schema import QualityFlag

MAP = {
    "vib_pump": {"signal": "pump_vibration", "unit": "mm/s"},
    "temp_casing": {"signal": "casing_temperature", "unit": "degC"},
    "pressure_out": {"signal": "outlet_pressure", "unit": "bar"},
}


def test_directory_audit(cira_dir):
    r = cira.audit(cira_dir)
    assert r["ok"], r["issues"]
    assert r["pumps"] == ["A", "B", "C"]
    absent = r["absent_asset_days"]
    assert absent == [{"pump": "C", "day": "2024-10-30",
                       "explained": "pump off that day per dataset listing"}]


def test_file_audit_measures_cadence_dups_and_gaps(cira_dir):
    a = cira.audit_file(cira_dir / "A_2024-04-10.csv")
    assert a["cadence_seconds"]["median"] == 10.0
    assert a["duplicate_timestamps"] == 1
    b = cira.audit_file(cira_dir / "B_2024-04-10.csv")
    assert b["gaps_over_factor"]["count"] == 1


def test_unexplained_absence_is_an_issue(cira_dir):
    (cira_dir / "B_2024-06-12.csv").unlink()
    r = cira.audit(cira_dir)
    assert not r["ok"]
    assert any("B 2024-06-12" in i for i in r["issues"])


def test_events_require_full_column_map(cira_dir):
    with pytest.raises(KeyError, match="unmapped"):
        list(cira.to_events(cira_dir / "A_2024-04-10.csv", {"vib_pump": MAP["vib_pump"]}))


def test_events_flags_and_idempotency(cira_dir):
    path = cira_dir / "A_2024-04-10.csv"
    ev = list(cira.to_events(path, MAP))
    assert len(ev) == 40 * 3
    assert all(e.observed_at.tzinfo is not None for e in ev)
    assert all(QualityFlag.UNIT_UNVERIFIED in e.quality_flags for e in ev)
    assert sum(QualityFlag.DUPLICATE_TIMESTAMP in e.quality_flags for e in ev) == 3
    again = list(cira.to_events(path, MAP))
    assert [e.provenance_hash for e in ev] == [e.provenance_hash for e in again]
    assert len({e.provenance_hash for e in ev}) == len(ev)
