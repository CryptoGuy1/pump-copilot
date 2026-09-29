> **Hydraulic test rig only.** These results come from a hydraulic test rig (ZeMA). They do not transfer to centrifugal pumps, and no ZeMA model scores CIRA data.

# ZeMA benchmark: hydraulic test rig pump leakage

Target: pump leakage only. Every output label reads "hydraulic test rig pump leakage state: k" (k = 0 no leakage, 1 weak, 2 severe).

## Protocol (pre-registered)

1. Target: hydraulic test rig pump leakage (0, 1, 2) only.
2. Features: per-cycle mean, std, min, max, 5/25/50/75/95th percentiles and slope per measured channel, plus a spectral summary (dominant frequency, centroid, power share below 1 Hz, 1-5 Hz, above 5 Hz) for the 100 Hz channels. Virtual channels CE, CP, SE excluded.
3. Splits: (a) stratified random 60/20/20; (b) grouped by contiguous leakage run (5 stratified group folds: test, validation, 3 x train); (c) chronological 60/20/20 with a 50-cycle gap on each side of the validation part: the PRIMARY result.
4. Splits (a) and (b) are built from the leakage label sequence of all cycles, as their definitions require (stratification, run boundaries); only the assignment of cycles to parts is kept. (c) uses cycle order alone. No test label is used to fit, tune or score anything before the evaluation.
5. Models: majority class, logistic regression, gradient boosting (scikit-learn); and two baselines: the stable flag alone (shortcut) and the four other condition labels (conditions only: not observable in practice, shows the confounding).
6. Tuning: each model's settings are chosen by macro-F1 on the validation part of each split (fit on train). The final fit is on the train part only.
7. Evaluation: once, on the test parts, by `pumpcopilot zema eval --prereg prereg-3b`, which refuses to run if src/pumpcopilot or this config changed since the tag.
8. Metrics: macro-F1, per-class recall, confusion matrices; 95% block-bootstrap intervals (2000 resamples) over leakage label runs; every result stratified by the stable flag and the cooler, valve and accumulator levels; Brier score and reliability curves on the test part. ScoredEvidence is marked calibrated only where that measurement exists.

## Splits

| split | train | val | test | definition |
|---|---|---|---|---|
| random | 1323 | 441 | 441 | stratified random 60/20/20, seed 0 |
| grouped | 1293 | 456 | 456 | 5 stratified group folds over 37 leakage runs, seed 0 |
| chronological | 1323 | 391 | 391 | cycle order 60/20/20, 50-cycle gap on each side of validation |

## Validation (tuning), frozen

| split | model | selected settings | validation macro-F1 |
|---|---|---|---|
| chronological | majority | - | 0.266 |
| chronological | logreg | {'C': 10.0, 'class_weight': None} | 0.707 |
| chronological | gradient_boosting | {'learning_rate': 0.05, 'max_iter': 100, 'max_depth': 3} | 0.658 |
| chronological | shortcut_stable_flag | - | 0.505 |
| chronological | conditions_only | {'learning_rate': 0.1, 'max_iter': 300, 'max_depth': 3} | 0.456 |
| grouped | majority | - | 0.237 |
| grouped | logreg | {'C': 0.1, 'class_weight': 'balanced'} | 0.849 |
| grouped | gradient_boosting | {'learning_rate': 0.05, 'max_iter': 100, 'max_depth': 3} | 0.841 |
| grouped | shortcut_stable_flag | - | 0.237 |
| grouped | conditions_only | {'learning_rate': 0.05, 'max_iter': 300, 'max_depth': 3} | 0.224 |
| random | majority | - | 0.237 |
| random | logreg | {'C': 10.0, 'class_weight': 'balanced'} | 0.998 |
| random | gradient_boosting | {'learning_rate': 0.1, 'max_iter': 300, 'max_depth': None} | 0.998 |
| random | shortcut_stable_flag | - | 0.237 |
| random | conditions_only | {'learning_rate': 0.1, 'max_iter': 100, 'max_depth': 3} | 0.420 |

## Test

**Not yet evaluated.** The test parts are read once, by `pumpcopilot zema eval --prereg prereg-3b`, after the pre-registration commit.

