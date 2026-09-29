"""Step 3b: the ZeMA pump-leakage benchmark.

The ZeMA data come from a hydraulic test rig. Results here say nothing about centrifugal pumps
(CIRA), and a ZeMA model may never score CIRA data (schema.ScoredEvidence's domain guard).
The only target is pump leakage; every output label reads
"hydraulic test rig pump leakage state: k".

Protocol (pre-registered, tag prereg-3b):
1. Features: per-cycle statistics per measured channel, plus a spectral summary for the
   100 Hz channels. The virtual channels CE, CP and SE are excluded unless switched on. The
   features are cached as Parquet, keyed by the raw file hashes.
2. Splits: (a) stratified random, (b) grouped by contiguous leakage run, (c) chronological
   60/20/20 with a 50-cycle gap on each side of the validation part (primary).
3. Tuning uses train and validation labels only. A LabelVault refuses any test label, per
   split, until it is unlocked with an EvaluationToken. Only the evaluation command issues
   one, after checking that code and config are unchanged since the tag.
4. Evaluation reads the test labels once: macro-F1, per-class recall and confusion matrices,
   with 95% block-bootstrap intervals over leakage label runs. Every result is stratified by
   the stable flag and by the cooler, valve and accumulator levels. Two shortcut baselines are
   included, and calibration is measured on the test part.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import zema
from .provenance import file_digest
from .schema import (
    CalibrationStatus,
    EvidenceItem,
    FeatureWindow,
    PresentationState,
    ScoredEvidence,
    SourceDataset,
)

FEATURE_VERSION = 1
STATS = ("mean", "std", "min", "max", "p5", "p25", "p50", "p75", "p95", "slope")
SPECTRAL = ("dominant_hz", "centroid_hz", "power_lt1", "power_1_5", "power_gt5")
SPECTRAL_HZ = 100.0  # the spectral summary is for the 100 Hz channels only
VIRTUAL = ("CE", "CP", "SE")
CLASSES = (0, 1, 2)
OUTPUT_LABEL = "hydraulic test rig pump leakage state: {k}"
STRATA = ("stable_flag", "cooler_pct", "valve_pct", "accumulator_bar")
STRATUM_MIN_CYCLES = 30  # macro-F1 only for strata this large ...
STRATUM_MIN_CLASSES = 2  # ... with at least this many classes; otherwise "too few cycles"
# the headline per split: the best validation macro-F1 among these, equal to 3 decimals going
# to the simpler (earlier) one
HEADLINE_CANDIDATES = ("majority", "logreg", "gradient_boosting")
CONDITIONS = ["cooler_pct", "valve_pct", "accumulator_bar", "stable_flag"]
MODEL_NAMES = ("majority", "logreg", "gradient_boosting", "shortcut_stable_flag",
               "conditions_only")
MODEL_INPUTS = {"majority": "features", "logreg": "features", "gradient_boosting": "features",
                "shortcut_stable_flag": ["stable_flag"], "conditions_only": CONDITIONS}
SCOPE_NOTE = ("These results come from a hydraulic test rig (ZeMA). They do not transfer to "
              "centrifugal pumps, and no ZeMA model scores CIRA data.")
T0 = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)  # ZeMA cycles have no clock: cycle x 60 s


# --- features ----------------------------------------------------------------------------

def channel_set(include_virtual: bool = False) -> list[str]:
    return list(zema.MEASURED) + (list(VIRTUAL) if include_virtual else [])


def _stats(a: np.ndarray, rate: float) -> dict[str, np.ndarray]:
    t = np.arange(a.shape[1]) / rate
    tc = t - t.mean()
    pct = np.percentile(a, [5, 25, 50, 75, 95], axis=1)
    return {"mean": a.mean(1), "std": a.std(1), "min": a.min(1), "max": a.max(1),
            "p5": pct[0], "p25": pct[1], "p50": pct[2], "p75": pct[3], "p95": pct[4],
            "slope": (a - a.mean(1, keepdims=True)) @ tc / (tc @ tc)}


def _spectral(a: np.ndarray, rate: float) -> dict[str, np.ndarray]:
    t = np.arange(a.shape[1]) / rate
    tc = t - t.mean()
    slope = (a - a.mean(1, keepdims=True)) @ tc / (tc @ tc)
    detrended = a - a.mean(1, keepdims=True) - slope[:, None] * tc
    power = np.abs(np.fft.rfft(detrended, axis=1)) ** 2
    freqs = np.fft.rfftfreq(a.shape[1], d=1 / rate)
    power, freqs = power[:, 1:], freqs[1:]  # no DC
    total = power.sum(1)
    total = np.where(total > 0, total, 1.0)
    band = lambda lo, hi: power[:, (freqs >= lo) & (freqs < hi)].sum(1) / total  # noqa: E731
    return {"dominant_hz": freqs[power.argmax(1)],
            "centroid_hz": power @ freqs / total,
            "power_lt1": band(0, 1), "power_1_5": band(1, 5), "power_gt5": band(5, np.inf)}


def cycle_features(arrays: dict[str, np.ndarray], rates: dict[str, float],
                   channels: list[str] | None = None) -> pd.DataFrame:
    """One row per cycle: STATS for every channel, SPECTRAL for the 100 Hz channels."""
    cols: dict[str, np.ndarray] = {}
    for ch in channels or [c for c in channel_set() if c in arrays]:
        a = np.asarray(arrays[ch], dtype=float)
        for k, v in _stats(a, rates[ch]).items():
            cols[f"{ch}__{k}"] = v
        if rates[ch] >= SPECTRAL_HZ:
            for k, v in _spectral(a, rates[ch]).items():
                cols[f"{ch}__{k}"] = v
    out = pd.DataFrame(cols)
    out.index.name = "cycle_id"
    return out


def feature_cache_key(root: Path, channels: list[str]) -> str:
    spec = {"version": FEATURE_VERSION, "stats": STATS, "spectral": SPECTRAL,
            "files": {c: file_digest(root / f"{c}.txt") for c in channels}}
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def load_features(raw_dir: Path, cache_dir: Path, include_virtual: bool = False
                  ) -> tuple[pd.DataFrame, str]:
    """Features for every cycle, from the Parquet cache when the raw files are unchanged."""
    root = zema.find_root(raw_dir)
    channels = channel_set(include_virtual)
    key = feature_cache_key(root, channels)
    path = Path(cache_dir) / f"zema_features_{key}.parquet"
    if path.exists():
        return pd.read_parquet(path), key
    arrays = {c: zema.load_channel(root, c) for c in channels}
    feats = cycle_features(arrays, {c: zema.CHANNELS[c][0] for c in channels}, channels)
    path.parent.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(path)
    return feats, key


def check_domain(features: pd.DataFrame) -> None:
    """Only ZeMA channel features may reach a ZeMA model."""
    bad = [c for c in features.columns if c.split("__")[0] not in zema.CHANNELS]
    if bad:
        raise ValueError(f"not ZeMA features: {bad[:5]}; ZeMA models score ZeMA cycles only")


# --- splits ------------------------------------------------------------------------------

def leakage_runs(y: np.ndarray) -> np.ndarray:
    """Run id per cycle: contiguous cycles with the same leakage label share one."""
    y = np.asarray(y)
    return np.concatenate([[0], np.cumsum(y[1:] != y[:-1])]).astype(int)


def split_random(y: np.ndarray, seed: int = 0, fractions=(0.6, 0.2, 0.2)) -> dict:
    from sklearn.model_selection import train_test_split

    idx = np.arange(len(y))
    train, rest = train_test_split(idx, train_size=fractions[0], stratify=y,
                                   random_state=seed)
    val, test = train_test_split(rest, train_size=fractions[1] / (1 - fractions[0]),
                                 stratify=y[rest], random_state=seed)
    return {"train": np.sort(train), "val": np.sort(val), "test": np.sort(test)}


def split_grouped(y: np.ndarray, runs: np.ndarray, seed: int = 0, tries: int = 100,
                  return_seed: bool = False):
    """Whole leakage runs per part (5 stratified group folds: test, val, 3 x train). The first
    seed from `seed` on whose parts all contain every class is used."""
    from sklearn.model_selection import StratifiedGroupKFold

    for s in range(seed, seed + tries):
        folds = list(StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=s)
                     .split(np.zeros(len(y)), y, runs))
        test, val = folds[0][1], folds[1][1]
        train = np.setdiff1d(np.arange(len(y)), np.concatenate([test, val]))
        parts = {"train": train, "val": np.sort(val), "test": np.sort(test)}
        if all(set(y[p]) == set(CLASSES) for p in parts.values()):
            return (parts, s) if return_seed else parts
    raise ValueError("no grouped split with every class in every part")


def split_chronological(n: int, fractions=(0.6, 0.2, 0.2), gap: int = 50) -> dict:
    a, b = int(n * fractions[0]), int(n * (fractions[0] + fractions[1]))
    return {"train": np.arange(0, a), "val": np.arange(a + gap, b),
            "test": np.arange(b + gap, n)}


def make_splits(y: np.ndarray, runs: np.ndarray, seed: int = 0, gap: int = 50) -> dict:
    return {"random": split_random(y, seed), "grouped": split_grouped(y, runs, seed),
            "chronological": split_chronological(len(y), gap=gap)}


def split_fingerprint(parts: dict) -> dict:
    return {k: {"n": int(len(v)), "sha256": hashlib.sha256(
        np.asarray(v, dtype=np.int64).tobytes()).hexdigest()[:16]} for k, v in parts.items()}


# --- the label vault ---------------------------------------------------------------------

class TestLabelAccess(PermissionError):
    __test__ = False


class PreregistrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvaluationToken:
    """Proof that the pre-registration check passed; only EvaluationToken.issue makes one."""
    tag: str
    commit: str
    _key: object = field(repr=False, compare=False, default=None)

    @classmethod
    def issue(cls, tag: str, paths: list[str], repo: Path | None = None) -> EvaluationToken:
        from .scoring import verify_preregistration

        repo = repo or Path(__file__).resolve().parents[2]
        got = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                              f"{tag}^{{commit}}"], capture_output=True, text=True)
        if got.returncode != 0:
            raise PreregistrationError(f"no pre-registration tag {tag!r} in {repo}")
        check = verify_preregistration(tag, paths, repo=repo)
        if not check["unchanged"]:
            raise PreregistrationError(f"changed since {tag}: {', '.join(check['changed'])}; "
                                       "the test parts can only be evaluated with the "
                                       "pre-registered code and config")
        return cls(tag, got.stdout.strip(), _TOKEN_KEY)

    @classmethod
    def _issue_for_tests(cls) -> EvaluationToken:
        return cls("test", "test", _TOKEN_KEY)


_TOKEN_KEY = object()


class LabelVault:
    """Labels by cycle, refusing (per split) any cycle of that split's test part until it is
    unlocked by an EvaluationToken. Records which cycles each split read."""

    def __init__(self, labels: pd.DataFrame, splits: dict):
        self._labels = labels
        self._test = {k: set(np.asarray(v["test"]).tolist()) for k, v in splits.items()}
        self._unlocked = False
        self.accessed: dict[str, set[int]] = {k: set() for k in splits}

    def unlock(self, token) -> None:
        if not isinstance(token, EvaluationToken) or token._key is not _TOKEN_KEY:
            raise TestLabelAccess("test labels need an EvaluationToken from the evaluation")
        self._unlocked = True

    def get(self, idx, split: str, column: str = "pump_leakage") -> np.ndarray:
        idx = np.asarray(idx)
        if not self._unlocked and self._test[split] & set(idx.tolist()):
            raise TestLabelAccess(f"{split}: test labels are read only by the evaluation")
        self.accessed[split] |= set(idx.tolist())
        return self._labels[column].to_numpy()[idx]


# --- models ------------------------------------------------------------------------------

class StableFlagLookup:
    """Shortcut baseline: leakage from the stable flag alone, learned on the train part.
    class_weight="balanced" weights each class by 1 / its frequency, so a flag value predicts
    the class it is most typical of, P(flag | class), not the class that is most common."""

    def __init__(self, class_weight: str | None = None):
        self.class_weight = class_weight

    def fit(self, x, y):
        values = np.unique(x[:, 0])
        per_class = np.bincount(y, minlength=3).astype(float)
        self.table_ = {}
        for v in values:
            counts = np.bincount(y[x[:, 0] == v], minlength=3) + 1.0  # smoothed
            if self.class_weight == "balanced":
                counts = counts / (per_class + len(values))
            self.table_[v] = counts / counts.sum()
        prior = np.ones(3) if self.class_weight == "balanced" else per_class + 1.0
        self.prior_ = prior / prior.sum()
        return self

    def predict_proba(self, x):
        return np.array([self.table_.get(v, self.prior_) for v in x[:, 0]])

    def predict(self, x):
        return self.predict_proba(x).argmax(1)


def build_model(name: str, params: dict):
    from sklearn.dummy import DummyClassifier
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if name == "majority":
        return DummyClassifier(strategy="prior")
    if name == "logreg":
        return make_pipeline(StandardScaler(), LogisticRegression(
            C=params["C"], class_weight=params["class_weight"], max_iter=5000))
    if name in ("gradient_boosting", "conditions_only"):
        return HistGradientBoostingClassifier(
            learning_rate=params["learning_rate"], max_iter=params["max_iter"],
            max_depth=params["max_depth"], random_state=0)
    if name == "shortcut_stable_flag":
        return StableFlagLookup(params.get("class_weight"))
    raise ValueError(f"unknown model {name}")


def small_grid() -> dict[str, list[dict]]:
    """Kept small; each list runs from the simplest setting, which wins ties."""
    gb = [{"learning_rate": lr, "max_iter": it, "max_depth": d}
          for d in (3, None) for it in (100, 300) for lr in (0.05, 0.1)]
    return {"majority": [{}], "shortcut_stable_flag": [{"class_weight": "balanced"}],
            "logreg": [{"C": c, "class_weight": w} for c in (0.01, 0.1, 1.0, 10.0)
                       for w in (None, "balanced")],
            "gradient_boosting": gb, "conditions_only": gb}


def _x(model: str, features: pd.DataFrame, vault: LabelVault, idx, split: str) -> np.ndarray:
    inputs = MODEL_INPUTS[model]
    if inputs == "features":
        return features.to_numpy()[idx]
    return np.column_stack([vault.get(idx, split, c) for c in inputs])


def _proba(m, x) -> np.ndarray:
    p = m.predict_proba(x)
    classes = list(getattr(m, "classes_", CLASSES))
    out = np.zeros((len(x), 3))
    for j, c in enumerate(classes):
        out[:, int(c)] = p[:, j]
    return out


# --- metrics -----------------------------------------------------------------------------

def macro_f1(y, pred, labels=CLASSES) -> float:
    from sklearn.metrics import f1_score

    return float(f1_score(y, pred, labels=list(labels), average="macro", zero_division=0))


def metrics(y, pred) -> dict:
    from sklearn.metrics import confusion_matrix

    y, pred = np.asarray(y), np.asarray(pred)
    recall = {str(c): float((pred[y == c] == c).mean()) if (y == c).any() else None
              for c in CLASSES}
    return {"n": int(len(y)), "macro_f1": macro_f1(y, pred),
            "accuracy": float((y == pred).mean()), "recall": recall,
            "confusion": confusion_matrix(y, pred, labels=list(CLASSES)).tolist(),
            "confusion_labels": list(CLASSES)}


def block_bootstrap(y, pred, blocks, n_boot: int = 2000, seed: int = 0) -> dict:
    """95% percentile intervals, resampling whole blocks (leakage label runs) with
    replacement, as many as the part has."""
    y, pred, blocks = np.asarray(y), np.asarray(pred), np.asarray(blocks)
    ids = np.unique(blocks)
    members = [np.flatnonzero(blocks == b) for b in ids]
    rng = np.random.default_rng(seed)
    f1s, rec = [], {str(c): [] for c in CLASSES}
    for _ in range(n_boot):
        take = np.concatenate([members[i] for i in rng.integers(0, len(ids), len(ids))])
        yt, pt = y[take], pred[take]
        f1s.append(macro_f1(yt, pt))
        for c in CLASSES:
            rec[str(c)].append((pt[yt == c] == c).mean() if (yt == c).any() else np.nan)

    def ci(v):
        v = np.asarray(v, dtype=float)
        if np.isnan(v).all():
            return {"lo": None, "hi": None}
        lo, hi = np.nanpercentile(v, [2.5, 97.5])
        return {"lo": round(float(lo), 6), "hi": round(float(hi), 6)}

    return {"macro_f1": ci(f1s), "recall": {c: ci(v) for c, v in rec.items()},
            "n_blocks": int(len(ids)), "unit": "leakage label run", "n_boot": n_boot,
            "seed": seed}


def stratified(y, pred, strata: pd.DataFrame) -> dict:
    """Within each value of the stable flag and each condition level: counts and per-class
    recall always; macro-F1 (over the classes present) only for strata with at least
    STRATUM_MIN_CYCLES cycles and STRATUM_MIN_CLASSES classes, otherwise "too few cycles"."""
    y, pred = np.asarray(y), np.asarray(pred)
    out = {}
    for col in STRATA:
        vals = strata[col].to_numpy()
        out[col] = {}
        for v in np.unique(vals):
            m = vals == v
            ys, ps = y[m], pred[m]
            present = sorted(set(ys.tolist()))
            enough = m.sum() >= STRATUM_MIN_CYCLES and len(present) >= STRATUM_MIN_CLASSES
            out[col][str(int(v))] = {
                "n": int(m.sum()), "classes_present": present,
                "class_counts": {str(c): int((ys == c).sum()) for c in CLASSES},
                "recall": {str(c): float((ps[ys == c] == c).mean()) if (ys == c).any()
                           else None for c in CLASSES},
                "accuracy": float((ys == ps).mean()),
                "macro_f1": macro_f1(ys, ps, labels=present) if enough else None,
                "note": None if enough else "too few cycles"}
    return out


def shortcut_analysis(labels: pd.DataFrame) -> dict:
    """The stable flag x leakage table and their mutual information, over all cycles."""
    from sklearn.metrics import mutual_info_score

    flag, leak = labels["stable_flag"].to_numpy(), labels["pump_leakage"].to_numpy()
    table = {str(int(v)): {str(c): int(((flag == v) & (leak == c)).sum()) for c in CLASSES}
             for v in np.unique(flag)}
    mi = float(mutual_info_score(flag, leak))  # nats
    p = np.bincount(leak, minlength=3) / len(leak)
    h = float(-(p[p > 0] * np.log(p[p > 0])).sum())
    return {"over": "all cycles", "n_cycles": int(len(leak)), "table": table,
            "table_axes": "stable_flag -> pump_leakage -> cycles",
            "mutual_information_nats": mi, "mutual_information_bits": mi / np.log(2),
            "leakage_entropy_bits": h / np.log(2),
            "normalized_by_leakage_entropy": mi / h if h else None}


def headline(selected_split: dict) -> str:
    """The best validation macro-F1 among HEADLINE_CANDIDATES; equal to 3 decimals goes to
    the simpler (earlier) candidate."""
    best = None
    for name in HEADLINE_CANDIDATES:
        f1 = round(selected_split[name]["val_macro_f1"], 3)
        if best is None or f1 > best[1]:
            best = (name, f1)
    return best[0]


def calibration(y, proba, bins: int = 10) -> dict:
    """Multi-class Brier score and reliability curves (top label, and one-vs-rest per
    class)."""
    y, proba = np.asarray(y), np.asarray(proba, dtype=float)
    onehot = np.eye(3)[y]
    brier = float(((proba - onehot) ** 2).sum(1).mean())
    edges = np.linspace(0, 1, bins + 1)

    def curve(conf, hit):
        which = np.clip(np.digitize(conf, edges[1:-1], right=True), 0, bins - 1)
        return [{"bin": [float(edges[b]), float(edges[b + 1])],
                 "mean_predicted": float(conf[which == b].mean()),
                 "observed": float(hit[which == b].mean()), "n": int((which == b).sum())}
                for b in range(bins) if (which == b).any()]

    top = proba.max(1)
    correct = (proba.argmax(1) == y).astype(float)
    top_curve = curve(top, correct)
    ece = sum(abs(b["mean_predicted"] - b["observed"]) * b["n"] for b in top_curve) / len(y)
    return {"brier": brier, "ece_top_label": float(ece), "bins": bins,
            "reliability": {"top_label": top_curve, **{
                str(c): curve(proba[:, c], (y == c).astype(float)) for c in CLASSES}}}


# --- tuning and evaluation ---------------------------------------------------------------

def tune(features: pd.DataFrame, vault: LabelVault, splits: dict, grid: dict) -> dict:
    """Fit on each split's train part and choose each model's settings by macro-F1 on its
    validation part. No test label is read (the vault refuses them)."""
    check_domain(features)
    table, selected = [], {}
    for split, parts in splits.items():
        y_tr = vault.get(parts["train"], split)
        y_va = vault.get(parts["val"], split)
        selected[split] = {}
        for model in MODEL_NAMES:
            x_tr = _x(model, features, vault, parts["train"], split)
            x_va = _x(model, features, vault, parts["val"], split)
            best = None
            for params in grid[model]:
                m = build_model(model, params).fit(x_tr, y_tr)
                f1 = macro_f1(y_va, m.predict(x_va))
                table.append({"split": split, "model": model, "params": params,
                              "val_macro_f1": round(f1, 6)})
                if best is None or f1 > best["val_macro_f1"]:
                    best = {"params": params, "val_macro_f1": round(f1, 6)}
            selected[split][model] = best
    return {"tuned_on": "validation parts only", "final_fit": "train part only",
            "selected": selected, "table": table}


def model_version(split: str, model: str, params: dict) -> str:
    return hashlib.sha256(json.dumps([FEATURE_VERSION, split, model, params], sort_keys=True,
                                     default=str).encode()).hexdigest()[:16]


def evaluate(features: pd.DataFrame, vault: LabelVault, labels_strata_split, splits: dict,
             frozen: dict, n_boot: int = 2000, seed: int = 0, bins: int = 10,
             headlines: dict | None = None, n_scores: int = 10) -> dict:
    """Once, after unlocking: fit each frozen setting on train, score the test part. The
    first n_scores test cycles of each split's headline model are kept as ScoredEvidence
    (calibrated: calibration is measured on this test part)."""
    check_domain(features)
    results: dict = {"splits": {}, "scores": []}
    for split, parts in splits.items():
        test = parts["test"]
        y = vault.get(test, split)
        strata = pd.DataFrame({c: vault.get(test, split, c) for c in STRATA})
        blocks = labels_strata_split(split, test)
        results["splits"][split] = {}
        for model in MODEL_NAMES:
            sel = frozen["selected"][split][model]
            m = build_model(model, sel["params"]).fit(
                _x(model, features, vault, parts["train"], split),
                vault.get(parts["train"], split))
            x_te = _x(model, features, vault, test, split)
            proba = _proba(m, x_te)
            pred = proba.argmax(1)
            results["splits"][split][model] = {
                "params": sel["params"], "val_macro_f1": sel["val_macro_f1"],
                "test": metrics(y, pred), "ci95": block_bootstrap(y, pred, blocks, n_boot, seed),
                "stratified": stratified(y, pred, strata),
                "calibration": calibration(y, proba, bins)}
            if headlines and headlines.get(split) == model:
                ver = model_version(split, model, sel["params"])
                for cid, p in list(zip(test, proba, strict=True))[:n_scores]:
                    results["scores"].append({"split": split, "model": model, "evidence":
                        scored_evidence(model, split, int(cid), p, ver,
                                        calibration_measured=True).model_dump(mode="json")})
    return results


def scored_evidence(model: str, split: str, cycle_id: int, proba: np.ndarray,
                    model_version: str, calibration_measured: bool,
                    source_dataset: SourceDataset = SourceDataset.ZEMA) -> ScoredEvidence:
    """One cycle's prediction. Calibrated only where calibration was measured on held-out
    data for this model and split; the model's domain is ZeMA whatever the caller says."""
    k = int(np.argmax(proba))
    label = OUTPUT_LABEL.format(k=k)
    review = k != 0
    items = (EvidenceItem(ref="E1", signal_name="pump_leakage_state", value=float(proba[k]),
                          statement=f"{model} ({split} split) gives {label} with probability "
                                    f"{proba[k]:.2f}"),) if review else ()
    start = T0 + dt.timedelta(seconds=60 * int(cycle_id))
    return ScoredEvidence(
        asset_id="zema-test-rig", source_dataset=source_dataset,
        model_id=f"zema-{model}-{split}", model_version=model_version,
        model_domain=SourceDataset.ZEMA,
        feature_window=FeatureWindow(start=start, end=start + dt.timedelta(seconds=60)),
        evidence=items, score=float(proba[k]),
        confidence_calibration_status=(CalibrationStatus.CALIBRATED if calibration_measured
                                       else CalibrationStatus.UNCALIBRATED),
        presentation_state=(PresentationState.REVIEW_SUGGESTED if review
                            else PresentationState.NORMAL),
        output_label=label, cycle_id=int(cycle_id), time_is_placeholder=True)
