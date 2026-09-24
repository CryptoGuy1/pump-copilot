from conftest import N_CYCLES

from pumpcopilot import zema


def test_audit_clean(zema_dir):
    r = zema.audit(zema_dir, expected_cycles=N_CYCLES)
    assert r["ok"], r["issues"]
    assert r["channels"]["PS1"]["shape"] == [N_CYCLES, 6000]
    assert r["channels"]["CE"]["virtual"] is True
    assert r["class_counts"] == {0: 12, 1: 9, 2: 9}
    assert r["leakage_runs"]["n_runs"] == 3
    assert r["unstable_cycles"] == 5


def test_block_layout_breaks_ordered_split(zema_dir):
    # With contiguous class blocks an ordered split cannot hold all classes: the preflight
    # must say so rather than letting training proceed on a meaningless split.
    r = zema.audit(zema_dir, expected_cycles=N_CYCLES)
    assert not any(p["all_classes_everywhere"] for p in r["split_preflight"])


def test_shape_mismatch_reported(zema_dir):
    r = zema.audit(zema_dir, expected_cycles=2205)
    assert not r["ok"]
    assert any("PS1: shape" in i for i in r["issues"])


def test_bad_label_reported(zema_dir):
    prof = next(zema_dir.rglob("profile.txt"))
    lines = prof.read_text().splitlines()
    lines[0] = "100\t100\t7\t130\t0"
    prof.write_text("\n".join(lines) + "\n")
    r = zema.audit(zema_dir, expected_cycles=N_CYCLES)
    assert any("pump_leakage" in i for i in r["issues"])


def test_cycle_records_are_deterministic(zema_dir):
    a = zema.cycle_records(zema_dir)
    b = zema.cycle_records(zema_dir)
    assert len(a) == N_CYCLES
    assert [x.provenance_hash for x in a] == [x.provenance_hash for x in b]
