"""Step 3b: the ZeMA pump-leakage benchmark (hydraulic test rig only)."""
import subprocess

import numpy as np
import pandas as pd
import pytest

from pumpcopilot import zema
from pumpcopilot import zema_bench as zb
from pumpcopilot.schema import CalibrationStatus, PresentationState, SourceDataset


def _runs_labels(lengths_values):
    return np.concatenate([np.full(n, v) for n, v in lengths_values])


def synthetic(n_runs=24, run_len=25, seed=0):
    """Cycles in contiguous leakage runs; PS1 (100 Hz) carries the leakage, TS1 does not."""
    rng = np.random.default_rng(seed)
    y = _runs_labels([(run_len, i % 3) for i in range(n_runs)])
    n = len(y)
    t = np.arange(6000) / 100.0
    ps1 = (150 + 5 * y[:, None] + rng.normal(0, 1, (n, 6000))
           + np.sin(2 * np.pi * (1 + y[:, None]) * t))
    arrays = {"PS1": ps1, "FS1": rng.normal(8, 0.1, (n, 600)),
              "TS1": 35 + rng.normal(0, 0.1, (n, 60)), "CE": rng.normal(20, 1, (n, 60))}
    labels = pd.DataFrame({
        "cooler_pct": np.where(np.arange(n) < n // 2, 100, 20),
        "valve_pct": 100, "pump_leakage": y, "accumulator_bar": 130,
        "stable_flag": (np.arange(n) % 7 == 0).astype(int)})
    labels.index.name = "cycle_id"
    return arrays, labels


RATES = {"PS1": 100.0, "FS1": 10.0, "TS1": 1.0, "CE": 1.0}


# --- features ----------------------------------------------------------------------------

def test_cycle_features_per_channel_with_spectral_only_for_100hz():
    arrays, _ = synthetic()
    f = zb.cycle_features(arrays, RATES, channels=["PS1", "FS1", "TS1"])
    assert len(f) == len(arrays["PS1"]) and f.index.name == "cycle_id"
    for ch in ("PS1", "FS1", "TS1"):
        for stat in zb.STATS:
            assert f"{ch}__{stat}" in f
    assert {f"PS1__{s}" for s in zb.SPECTRAL} <= set(f)
    assert not [c for c in f if c.startswith(("FS1__", "TS1__")) and
                c.split("__")[1] in zb.SPECTRAL]
    assert not [c for c in f if c.startswith("CE__")]


def test_slope_and_spectral_summary_are_right_on_known_signals():
    t = np.arange(6000) / 100.0
    arrays = {"PS1": np.vstack([2.0 + 0.5 * t, np.sin(2 * np.pi * 3.0 * t)])}
    f = zb.cycle_features(arrays, {"PS1": 100.0}, channels=["PS1"])
    assert f.loc[0, "PS1__slope"] == pytest.approx(0.5)
    assert f.loc[1, "PS1__dominant_hz"] == pytest.approx(3.0, abs=0.05)
    assert f.loc[1, "PS1__power_1_5"] > 0.95  # 3 Hz sits in the 1-5 Hz band
    assert f.loc[0, "PS1__p50"] == pytest.approx(np.median(arrays["PS1"][0]))


def test_virtual_channels_are_excluded_unless_switched_on():
    assert not set(zb.channel_set()) & {"CE", "CP", "SE"}
    assert set(zb.channel_set()) == set(zema.MEASURED)
    assert {"CE", "CP", "SE"} <= set(zb.channel_set(include_virtual=True))


def test_feature_cache_is_parquet_keyed_by_raw_file_hashes(zema_dir, tmp_path):
    cache = tmp_path / "cache"
    f1, key1 = zb.load_features(zema_dir, cache)
    assert (cache / f"zema_features_{key1}.parquet").exists()
    f2, key2 = zb.load_features(zema_dir, cache)
    assert key2 == key1 and f2.equals(f1)
    _, key_v = zb.load_features(zema_dir, cache, include_virtual=True)
    assert key_v != key1
    root = zema.find_root(zema_dir)
    ts1 = root / "TS1.txt"
    ts1.write_text(ts1.read_text().replace("0", "1", 1))  # one changed raw file
    _, key3 = zb.load_features(zema_dir, cache)
    assert key3 != key1


# --- splits ------------------------------------------------------------------------------

def _parts_disjoint_and_cover(parts, n, gaps=0):
    idx = np.concatenate(list(parts.values()))
    assert len(idx) == len(set(idx)) == n - gaps


def test_random_split_is_stratified():
    _, labels = synthetic()
    y = labels.pump_leakage.to_numpy()
    parts = zb.split_random(y, seed=0)
    _parts_disjoint_and_cover(parts, len(y))
    for p, frac in zip(("train", "val", "test"), (0.6, 0.2, 0.2), strict=True):
        assert len(parts[p]) == pytest.approx(frac * len(y), abs=2)
        shares = np.bincount(y[parts[p]], minlength=3) / len(parts[p])
        assert np.allclose(shares, np.bincount(y) / len(y), atol=0.03)


def test_grouped_split_keeps_every_label_run_in_one_part():
    _, labels = synthetic()
    y = labels.pump_leakage.to_numpy()
    runs = zb.leakage_runs(y)
    assert runs.max() + 1 == 24
    parts = zb.split_grouped(y, runs, seed=0)
    _parts_disjoint_and_cover(parts, len(y))
    owner = {}
    for p, idx in parts.items():
        for r in set(runs[idx]):
            assert owner.setdefault(r, p) == p, f"run {r} spans parts"
        assert set(y[idx]) == {0, 1, 2}


def test_chronological_split_60_20_20_with_50_cycle_gaps():
    parts = zb.split_chronological(1000, gap=50)
    assert parts["train"].tolist() == list(range(0, 600))
    assert parts["val"].tolist() == list(range(650, 800))
    assert parts["test"].tolist() == list(range(850, 1000))
    _parts_disjoint_and_cover(parts, 1000, gaps=100)


# --- the label vault: test labels only for the evaluation --------------------------------

def test_label_vault_refuses_test_labels_until_unlocked():
    _, labels = synthetic()
    splits = {"chronological": zb.split_chronological(len(labels))}
    vault = zb.LabelVault(labels, splits)
    chrono = splits["chronological"]
    train = chrono["train"]
    assert (vault.get(train, "chronological") == labels.pump_leakage.to_numpy()[train]).all()
    with pytest.raises(zb.TestLabelAccess):
        vault.get(chrono["test"][:3], "chronological")
    with pytest.raises(zb.TestLabelAccess):  # condition labels of test cycles too
        vault.get(chrono["test"], "chronological", column="stable_flag")
    with pytest.raises(zb.TestLabelAccess):
        vault.unlock("not a token")
    vault.unlock(zb.EvaluationToken._issue_for_tests())
    assert len(vault.get(chrono["test"], "chronological")) == len(chrono["test"])


def test_tuning_reads_no_test_label():
    arrays, labels = synthetic()
    feats = zb.cycle_features(arrays, RATES, channels=["PS1", "FS1", "TS1"])
    y = labels.pump_leakage.to_numpy()
    splits = zb.make_splits(y, zb.leakage_runs(y), seed=0, gap=20)
    vault = zb.LabelVault(labels, splits)
    frozen = zb.tune(feats, vault, splits, zb.small_grid())
    for split, parts in splits.items():  # a cycle in one split's test part may train another
        assert vault.accessed[split] and not vault.accessed[split] & set(parts["test"])
    for split in ("random", "grouped", "chronological"):
        for model in zb.MODEL_NAMES:
            sel = frozen["selected"][split][model]
            assert "val_macro_f1" in sel and "params" in sel
    assert frozen["tuned_on"] == "validation parts only"


# --- metrics -----------------------------------------------------------------------------

def test_block_bootstrap_resamples_label_runs_not_cycles():
    y = _runs_labels([(30, 0), (30, 1), (30, 2), (30, 0), (30, 1), (30, 2)])
    pred = y.copy()
    pred[:30] = 1  # the first run is wrong as a whole
    blocks = zb.leakage_runs(y)
    ci = zb.block_bootstrap(y, pred, blocks, n_boot=400, seed=1)
    point = zb.macro_f1(y, pred)
    assert ci["macro_f1"]["lo"] <= point <= ci["macro_f1"]["hi"]
    assert ci["n_blocks"] == 6 and ci["unit"] == "leakage label run"
    # resampling whole runs: every resample either has run 0 or not; with cycles resampled
    # individually the interval would be far narrower
    naive = zb.block_bootstrap(y, pred, np.arange(len(y)), n_boot=400, seed=1)
    width = lambda c: c["macro_f1"]["hi"] - c["macro_f1"]["lo"]  # noqa: E731
    assert width(ci) > 2 * width(naive)
    again = zb.block_bootstrap(y, pred, blocks, n_boot=400, seed=1)
    assert again == ci


def test_per_class_recall_and_confusion_matrix():
    y = np.array([0, 0, 1, 1, 2, 2])
    pred = np.array([0, 1, 1, 1, 0, 2])
    m = zb.metrics(y, pred)
    assert m["recall"] == {"0": 0.5, "1": 1.0, "2": 0.5}
    assert m["confusion"] == [[1, 1, 0], [0, 2, 0], [1, 0, 1]]
    assert m["confusion_labels"] == [0, 1, 2]


# --- shortcut analysis -------------------------------------------------------------------

def test_shortcut_baseline_is_class_balanced():
    # stable=1 holds 20 of the 25 class-2 cycles but more class-0 cycles (40): unweighted it
    # would say 0; balanced (each class weighted by 1/its frequency) it says 2
    y = np.array([0] * 400 + [0] * 40 + [2] * 20 + [2] * 5 + [1] * 100)
    flag = np.array([0] * 400 + [1] * 40 + [1] * 20 + [0] * 5 + [0] * 100)
    m = zb.build_model("shortcut_stable_flag", {"class_weight": "balanced"})
    m.fit(flag[:, None], y)
    assert m.predict(np.array([[1]]))[0] == 2
    assert zb.small_grid()["shortcut_stable_flag"] == [{"class_weight": "balanced"}]


def test_stable_flag_by_leakage_table_and_mutual_information():
    labels = pd.DataFrame({"stable_flag": [0, 0, 1, 1], "pump_leakage": [0, 0, 2, 2]})
    a = zb.shortcut_analysis(labels)
    assert a["table"] == {"0": {"0": 2, "1": 0, "2": 0}, "1": {"0": 0, "1": 0, "2": 2}}
    assert a["mutual_information_bits"] == pytest.approx(1.0)
    assert a["normalized_by_leakage_entropy"] == pytest.approx(1.0)
    indep = zb.shortcut_analysis(pd.DataFrame({"stable_flag": [0, 1, 0, 1],
                                               "pump_leakage": [0, 0, 2, 2]}))
    assert indep["mutual_information_bits"] == pytest.approx(0.0)
    assert a["n_cycles"] == 4 and a["over"] == "all cycles"


def test_headline_is_the_best_validation_model_with_ties_to_the_simpler():
    sel = {"majority": {"val_macro_f1": 0.2}, "logreg": {"val_macro_f1": 0.997623},
           "gradient_boosting": {"val_macro_f1": 0.997626},
           "shortcut_stable_flag": {"val_macro_f1": 0.9}, "conditions_only": {"val_macro_f1": 1}}
    assert zb.headline(sel) == "logreg"  # equal to 3 decimals: the simpler wins
    sel["gradient_boosting"]["val_macro_f1"] = 0.9990
    assert zb.headline(sel) == "gradient_boosting"
    assert zb.HEADLINE_CANDIDATES == ("majority", "logreg", "gradient_boosting")


def test_the_frozen_config_names_the_headlines_and_supersedes_prereg_3b():
    import yaml

    from pumpcopilot import cli

    doc = yaml.safe_load(cli.ZEMA_CONFIG.read_text())
    assert doc["label"].startswith("3b-r2")
    sup = doc["supersedes"]
    assert sup["tag"] == "prereg-3b" and sup["test_parts_evaluated"] is False
    assert sup["reason"] and sup["changes"]
    for split in ("random", "grouped", "chronological"):
        name = doc["headline"][split]
        assert name in zb.HEADLINE_CANDIDATES
        assert name == zb.headline(doc["frozen"]["selected"][split])
        assert f"{split}: {name}" in " ".join(doc["protocol"])


def test_shortcut_baseline_uses_the_stable_flag_alone():
    _, labels = synthetic()
    m = zb.build_model("shortcut_stable_flag", {})
    x = labels[["stable_flag"]].to_numpy()
    m.fit(x, labels.pump_leakage.to_numpy())
    assert set(m.predict(np.array([[0], [1]]))) <= {0, 1, 2}
    assert zb.MODEL_INPUTS["shortcut_stable_flag"] == ["stable_flag"]
    assert zb.MODEL_INPUTS["conditions_only"] == ["cooler_pct", "valve_pct",
                                                  "accumulator_bar", "stable_flag"]
    assert zb.MODEL_INPUTS["logreg"] == zb.MODEL_INPUTS["gradient_boosting"] == "features"


def test_results_are_stratified_by_stable_flag_and_each_condition():
    _, labels = synthetic()
    y = labels.pump_leakage.to_numpy()
    pred = y.copy()
    pred[labels.stable_flag.to_numpy() == 1] = 0
    s = zb.stratified(y, pred, labels)
    assert set(s) == {"stable_flag", "cooler_pct", "valve_pct", "accumulator_bar"}
    assert set(s["stable_flag"]) == {"0", "1"}
    assert s["stable_flag"]["0"]["macro_f1"] == 1.0 and s["stable_flag"]["1"]["macro_f1"] < 1
    assert s["cooler_pct"]["100"]["n"] + s["cooler_pct"]["20"]["n"] == len(y)


def test_small_or_single_class_strata_report_counts_and_recall_but_no_macro_f1():
    y = np.array([0] * 40 + [1] * 10 + [2] * 20 + [0] * 29)
    pred = y.copy()
    strata = pd.DataFrame({"stable_flag": [0] * 40 + [1] * 30 + [2] * 29,
                           "cooler_pct": 100, "valve_pct": 100, "accumulator_bar": 130})
    s = zb.stratified(y, pred, strata)["stable_flag"]
    one_class = s["0"]  # 40 cycles, all class 0
    assert one_class["macro_f1"] is None and one_class["note"] == "too few cycles"
    assert one_class["class_counts"] == {"0": 40, "1": 0, "2": 0}
    assert one_class["recall"] == {"0": 1.0, "1": None, "2": None}
    ok = s["1"]  # 30 cycles, two classes
    assert ok["macro_f1"] == 1.0 and ok["note"] is None and ok["n"] == 30
    small = s["2"]  # 29 cycles
    assert small["macro_f1"] is None and small["note"] == "too few cycles"
    assert (zb.STRATUM_MIN_CYCLES, zb.STRATUM_MIN_CLASSES) == (30, 2)


# --- calibration and ScoredEvidence ------------------------------------------------------

def test_calibration_brier_and_reliability():
    y = np.array([0, 1, 2, 0])
    perfect = np.eye(3)[y]
    c = zb.calibration(y, perfect, bins=5)
    assert c["brier"] == 0.0
    assert c["reliability"]["top_label"][-1]["observed"] == 1.0
    uniform = np.full((4, 3), 1 / 3)
    assert zb.calibration(y, uniform)["brier"] == pytest.approx(2 / 3)


def test_scored_evidence_says_calibration_measured_only_where_it_was():
    p = np.array([0.1, 0.2, 0.7])
    measured = zb.scored_evidence("gradient_boosting", "chronological", 7, p, "v1",
                                  calibration_measured=True)
    assert measured.confidence_calibration_status == CalibrationStatus.CALIBRATION_MEASURED
    assert measured.model_dump(mode="json")["confidence_calibration_status"] == \
        "calibration_measured"
    assert measured.output_label == "hydraulic test rig pump leakage state: 2"
    assert measured.presentation_state == PresentationState.REVIEW_SUGGESTED
    assert measured.source_dataset == measured.model_domain == SourceDataset.ZEMA
    assert measured.cycle_id == 7 and measured.time_is_placeholder is True
    dumped = measured.model_dump(mode="json")
    assert dumped["cycle_id"] == 7 and dumped["time_is_placeholder"] is True
    unmeasured = zb.scored_evidence("logreg", "random", 7, np.array([0.8, 0.1, 0.1]), "v1",
                                    calibration_measured=False)
    assert unmeasured.confidence_calibration_status == CalibrationStatus.UNCALIBRATED
    assert unmeasured.output_label == "hydraulic test rig pump leakage state: 0"
    assert unmeasured.presentation_state == PresentationState.NORMAL


def test_placeholder_time_fields_are_zema_only_and_leave_cira_scores_unchanged():
    import datetime as dt

    from pumpcopilot.schema import FeatureWindow, ScoredEvidence

    w = FeatureWindow(start=dt.datetime(2024, 10, 30, 9, tzinfo=dt.UTC),
                      end=dt.datetime(2024, 10, 30, 9, 6, tzinfo=dt.UTC))
    cira = ScoredEvidence(asset_id="cira-pump-B", source_dataset=SourceDataset.CIRA,
                          model_id="m", model_version="v", model_domain=SourceDataset.CIRA,
                          feature_window=w, confidence_calibration_status="not_applicable",
                          presentation_state="normal")
    assert cira.cycle_id is None and cira.time_is_placeholder is False
    assert "cycle_id" not in cira.model_dump(mode="json")  # stored CIRA JSON stays as it was
    assert "time_is_placeholder" not in cira.model_dump(mode="json")
    with pytest.raises(ValueError, match="cycle_id"):
        ScoredEvidence(asset_id="zema-test-rig", source_dataset=SourceDataset.ZEMA,
                       model_id="m", model_version="v", model_domain=SourceDataset.ZEMA,
                       feature_window=w, confidence_calibration_status="uncalibrated",
                       presentation_state="normal",
                       output_label="hydraulic test rig pump leakage state: 0")
    with pytest.raises(ValueError, match="CIRA"):
        ScoredEvidence(asset_id="cira-pump-B", source_dataset=SourceDataset.CIRA,
                       model_id="m", model_version="v", model_domain=SourceDataset.CIRA,
                       feature_window=w, confidence_calibration_status="not_applicable",
                       presentation_state="normal", cycle_id=3)


def test_the_status_is_calibration_measured_not_calibrated():
    values = {c.value for c in CalibrationStatus}
    assert "calibration_measured" in values and "calibrated" not in values


@pytest.mark.parametrize("ece,grade", [(0.004, "good"), (0.07, "fair"), (0.15, "poor"),
                                       (0.40, "very poor")])
def test_calibration_grade_in_plain_language(ece, grade):
    g = zb.calibration_grade(brier=0.3, ece=ece, baseline_brier=0.7)
    assert g["grade"] == grade and g["brier"] == 0.3 and g["ece"] == ece
    assert f"{ece:.2f}" in g["text"] and "better than" in g["text"]
    worse = zb.calibration_grade(brier=0.875, ece=0.404, baseline_brier=0.709)
    assert worse["grade"] == "very poor"
    assert "worse than always predicting the class shares" in worse["text"]
    grades = ((0.05, "good"), (0.10, "fair"), (0.20, "poor"), (float("inf"), "very poor"))
    assert grades == zb.CALIBRATION_GRADES


def test_legacy_results_migrate_by_renaming_only():
    old = {"splits": {"random": {"logreg": {"calibration": {"brier": 0.1}}}},
           "calibrated": {"random": {"logreg": True}},
           "scores": [{"split": "random", "model": "logreg", "evidence": {
               "confidence_calibration_status": "calibrated", "score": 0.9}}]}
    new = zb.migrate_results(old)
    assert new["scores"][0]["evidence"]["confidence_calibration_status"] == \
        "calibration_measured"
    assert new["calibration_measured"] == {"random": {"logreg": True}}
    assert "calibrated" not in new and new["splits"] == old["splits"]
    assert new["presentation_migrations"][0]["rename"] == {"calibrated": "calibration_measured"}
    assert zb.numbers(new) == zb.numbers(old)
    assert zb.migrate_results(new) == new  # idempotent


def test_zema_models_never_score_cira():
    with pytest.raises(ValueError, match="may not score"):
        zb.scored_evidence("logreg", "random", 1, np.array([1.0, 0, 0]), "v1",
                           calibration_measured=False, source_dataset=SourceDataset.CIRA)
    with pytest.raises(ValueError, match="ZeMA"):
        zb.check_domain(pd.DataFrame({"outlet_pressure__mean": [1.0]}))


# --- protocol ----------------------------------------------------------------------------

def test_evaluation_refuses_to_run_when_code_changed_since_the_tag(tmp_path):
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "code.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "prereg")
    git("tag", "prereg-3b")
    token = zb.EvaluationToken.issue("prereg-3b", ["code.py"], repo=tmp_path)
    assert token.tag == "prereg-3b"
    (tmp_path / "code.py").write_text("x = 2\n")
    with pytest.raises(zb.PreregistrationError, match="code.py"):
        zb.EvaluationToken.issue("prereg-3b", ["code.py"], repo=tmp_path)
    with pytest.raises(zb.PreregistrationError, match="tag"):
        zb.EvaluationToken.issue("no-such-tag", ["code.py"], repo=tmp_path)


def test_report_opens_with_the_test_rig_note():
    from pumpcopilot import zema_report

    md = zema_report.markdown({"frozen": {"selected": {}}}, results=None)
    st = {"n": 12, "class_counts": {"0": 12, "1": 0, "2": 0}, "macro_f1": None,
          "note": "too few cycles", "recall": {"0": 1.0, "1": None, "2": None}}
    assert "too few cycles" in zema_report.stratum_cell(st)
    res = {"preregistration": {"tag": "prereg-3b-r2", "commit": "3ab44276f478"},
           "headline": {"chronological": "logreg"}, "splits": {"chronological": {
               "logreg": {"val_macro_f1": 0.707, "test": {"macro_f1": 0.476},
                          "ci95": {"macro_f1": {"lo": 0.244, "hi": 0.611}, "n_blocks": 9},
                          "stratified": {"cooler_pct": {"100": {}}}}}}}
    summary = zema_report.summary(res)
    assert "well below validation, with wide uncertainty (9 test runs)" in summary
    assert "can't separate time drift from condition shift" in summary
    assert "0.476" in summary and "0.707" in summary
    assert "0.50" in zema_report.stratum_cell({**st, "macro_f1": 0.5, "note": None})
    first = md.lstrip().splitlines()[0:4]
    assert "hydraulic test rig" in " ".join(first).lower()
    assert "centrifugal" in " ".join(first).lower()
    assert "not yet evaluated" in md.lower()
