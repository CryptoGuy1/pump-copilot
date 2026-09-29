> **Hydraulic test rig only.** These results come from a hydraulic test rig (ZeMA). They do not transfer to centrifugal pumps, and no ZeMA model scores CIRA data.

# ZeMA benchmark: hydraulic test rig pump leakage

Target: pump leakage only. Every output label reads "hydraulic test rig pump leakage state: k" (k = 0 no leakage, 1 weak, 2 severe).

**This is revision 2.** It supersedes `prereg-3b` (`eae1dbde5e5a`), whose test parts were never evaluated. Revised on review before any test evaluation: the test parts of prereg-3b were never opened, so this is a new pre-registration, not a post-hoc change.

Headline model per split: random **logreg**, grouped **logreg**, chronological **logreg**.

## Protocol (pre-registered)

1. Target: hydraulic test rig pump leakage (0, 1, 2) only.
2. Features: per-cycle mean, std, min, max, 5/25/50/75/95th percentiles and slope per measured channel, plus a spectral summary (dominant frequency, centroid, power share below 1 Hz, 1-5 Hz, above 5 Hz) for the 100 Hz channels. Virtual channels CE, CP, SE excluded.
3. Splits: (a) stratified random 60/20/20; (b) grouped by contiguous leakage run (5 stratified group folds: test, validation, 3 x train); (c) chronological 60/20/20 with a 50-cycle gap on each side of the validation part: the PRIMARY result.
4. Splits (a) and (b) are built from the leakage label sequence of all cycles, as their definitions require (stratification, run boundaries); only the assignment of cycles to parts is kept. (c) uses cycle order alone. No test label is used to fit, tune or score anything before the evaluation.
5. Models: majority class, logistic regression, gradient boosting (scikit-learn); and two baselines: the stable flag alone (shortcut) and the four other condition labels (conditions only: not observable in practice, shows the confounding).
6. Tuning: each model's settings are chosen by macro-F1 on the validation part of each split (fit on train). The final fit is on the train part only.
7. Evaluation: once, on the test parts, by `pumpcopilot zema eval --prereg prereg-3b`, which refuses to run if src/pumpcopilot or this config changed since the tag.
8. Metrics: macro-F1, per-class recall, confusion matrices; 95% block-bootstrap intervals (2000 resamples) over leakage label runs; every result stratified by the stable flag and the cooler, valve and accumulator levels; Brier score and reliability curves on the test part. ScoredEvidence is marked calibrated only where that measurement exists.
9. Strata: counts and per-class recall are always reported; macro-F1 only for strata with at least 2 classes and at least 30 cycles, otherwise 'too few cycles'.
10. The stable-flag shortcut baseline is fitted with class-balanced weighting (each class weighted by 1 / its frequency) in all three splits.
11. The stable-flag x leakage table and its mutual information over all cycles are computed by the evaluation (they include test cycles). The Step 1 audit already reported the condition x leakage crosstabs over all cycles (reports/zema_audit.json).
12. ZeMA cycles have no clock: each ScoredEvidence carries cycle_id and time_is_placeholder: true, and its time window is cycle x 60 s from 1970-01-01.
13. prereg-3b (eae1dbd) is superseded by this revision before any test evaluation; its test parts were never opened. The tag is kept.
14. Headline model per split (the best validation macro-F1 among majority, logreg, gradient_boosting; equal to 3 decimals goes to the simpler, in that order): random: logreg; grouped: logreg; chronological: logreg.

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
| chronological | shortcut_stable_flag | {'class_weight': 'balanced'} | 0.505 |
| chronological | conditions_only | {'learning_rate': 0.1, 'max_iter': 300, 'max_depth': 3} | 0.456 |
| grouped | majority | - | 0.237 |
| grouped | logreg | {'C': 0.1, 'class_weight': 'balanced'} | 0.849 |
| grouped | gradient_boosting | {'learning_rate': 0.05, 'max_iter': 100, 'max_depth': 3} | 0.841 |
| grouped | shortcut_stable_flag | {'class_weight': 'balanced'} | 0.467 |
| grouped | conditions_only | {'learning_rate': 0.05, 'max_iter': 300, 'max_depth': 3} | 0.224 |
| random | majority | - | 0.237 |
| random | logreg | {'C': 10.0, 'class_weight': 'balanced'} | 0.998 |
| random | gradient_boosting | {'learning_rate': 0.1, 'max_iter': 300, 'max_depth': None} | 0.998 |
| random | shortcut_stable_flag | {'class_weight': 'balanced'} | 0.428 |
| random | conditions_only | {'learning_rate': 0.1, 'max_iter': 100, 'max_depth': 3} | 0.420 |

## Result in brief

Primary result (chronological split, headline model logreg): test macro-F1 0.476 (95% interval 0.244–0.611), well below validation, with wide uncertainty (9 test runs). Its validation macro-F1 was 0.707. The data can't separate time drift from condition shift: the chronological test part comes later in the recording and also covers different conditions (cooler at 100% only), so the drop may come from either, or both.

## Stable flag x leakage, all cycles

| stable_flag | leakage 0 | leakage 1 | leakage 2 |
|---|---|---|---|
| 0 | 489 | 480 | 480 |
| 1 | 732 | 12 | 12 |

Mutual information 0.316 bits, 22.0% of the leakage entropy (1.438 bits), over 2205 cycles.

## Test (evaluated once, pre-registration `prereg-3b-r2` = `3ab44276f478`)

Intervals are 95% block-bootstrap percentile intervals over leakage label runs (the number of runs in each test part is given), not over cycles.

Calibration is measured on each test part: Brier score, top-label ECE and a plain grade (ECE below 0.05 good, below 0.10 fair, below 0.20 poor, else very poor).

| split | model | macro-F1 | 95% interval | recall 0 / 1 / 2 | runs | Brier | ECE | calibration |
|---|---|---|---|---|---|---|---|---|
| (c) chronological 60/20/20, 50-cycle gaps: PRIMARY | majority | 0.187 | 0.000–0.277 | 1.00 / 0.00 / 0.00 | 9 | 0.709 | 0.177 | poor |
|  | logreg | 0.476 | 0.244–0.611 | 0.04 / 1.00 / 0.65 | 9 | 0.875 | 0.404 | very poor |
|  | gradient_boosting | 0.551 | 0.238–0.636 | 1.00 / 0.97 / 0.00 | 9 | 0.617 | 0.347 | very poor |
|  | shortcut_stable_flag | 0.250 | 0.100–0.351 | 0.15 / 0.98 / 0.00 | 9 | 0.696 | 0.091 | fair |
|  | conditions_only | 0.295 | 0.094–0.460 | 0.67 / 0.41 / 0.00 | 9 | 0.837 | 0.224 | very poor |
| (b) grouped by leakage run | majority | 0.237 | 0.000–0.308 | 1.00 / 0.00 / 0.00 | 6 | 0.604 | 0.006 | good |
|  | logreg | 0.987 | 0.614–1.000 | 1.00 / 1.00 / 0.97 | 6 | 0.042 | 0.096 | fair |
|  | gradient_boosting | 0.997 | 0.657–1.000 | 1.00 / 0.99 / 1.00 | 6 | 0.005 | 0.009 | good |
|  | shortcut_stable_flag | 0.467 | 0.095–0.565 | 0.84 / 0.98 / 0.00 | 6 | 0.359 | 0.071 | fair |
|  | conditions_only | 0.224 | 0.000–0.287 | 0.84 / 0.00 / 0.00 | 6 | 0.960 | 0.479 | very poor |
| (a) stratified random: the naive number | majority | 0.237 | 0.162–0.281 | 1.00 / 0.00 / 0.00 | 37 | 0.594 | 0.001 | good |
|  | logreg | 1.000 | 1.000–1.000 | 1.00 / 1.00 / 1.00 | 37 | 0.001 | 0.004 | good |
|  | gradient_boosting | 0.993 | 0.981–1.000 | 1.00 / 0.97 / 1.00 | 37 | 0.014 | 0.007 | good |
|  | shortcut_stable_flag | 0.390 | 0.264–0.467 | 0.55 / 0.00 / 0.96 | 37 | 0.516 | 0.095 | fair |
|  | conditions_only | 0.400 | 0.299–0.477 | 0.66 / 0.28 / 0.26 | 37 | 0.504 | 0.093 | fair |

### (c) chronological 60/20/20, 50-cycle gaps: PRIMARY: confusion matrices (rows true 0/1/2, columns predicted)

- majority: [[152, 0, 0], [123, 0, 0], [116, 0, 0]]
- logreg: [[6, 146, 0], [0, 123, 0], [0, 41, 75]]
- gradient_boosting: [[152, 0, 0], [4, 119, 0], [0, 116, 0]]
- shortcut_stable_flag: [[23, 129, 0], [3, 120, 0], [2, 114, 0]]
- conditions_only: [[102, 50, 0], [73, 50, 0], [66, 50, 0]]

### (c) chronological 60/20/20, 50-cycle gaps: PRIMARY: stratified macro-F1

| model | stable_flag=0 | stable_flag=1 | cooler_pct=100 | valve_pct=73 | valve_pct=80 | valve_pct=90 | valve_pct=100 | accumulator_bar=90 | accumulator_bar=100 | accumulator_bar=115 |
|---|---|---|---|---|---|---|---|---|---|---|
| majority | 0.17 (n 363) | too few cycles (n 28) | 0.19 (n 391) | 0.18 (n 84) | 0.17 (n 90) | 0.17 (n 90) | 0.22 (n 127) | 0.20 (n 142) | 0.18 (n 133) | 0.17 (n 116) |
| logreg | 0.49 (n 363) | too few cycles (n 28) | 0.48 (n 391) | 0.46 (n 84) | 0.55 (n 90) | 0.50 (n 90) | 0.42 (n 127) | 0.52 (n 142) | 0.44 (n 133) | 0.42 (n 116) |
| gradient_boosting | 0.55 (n 363) | too few cycles (n 28) | 0.55 (n 391) | 0.57 (n 84) | 0.56 (n 90) | 0.51 (n 90) | 0.56 (n 127) | 0.56 (n 142) | 0.56 (n 133) | 0.54 (n 116) |
| shortcut_stable_flag | 0.17 (n 363) | too few cycles (n 28) | 0.25 (n 391) | 0.18 (n 84) | 0.17 (n 90) | 0.17 (n 90) | 0.32 (n 127) | 0.26 (n 142) | 0.28 (n 133) | 0.19 (n 116) |
| conditions_only | 0.28 (n 363) | too few cycles (n 28) | 0.30 (n 391) | 0.27 (n 84) | 0.26 (n 90) | 0.26 (n 90) | 0.36 (n 127) | 0.20 (n 142) | 0.28 (n 133) | 0.26 (n 116) |

### (b) grouped by leakage run: confusion matrices (rows true 0/1/2, columns predicted)

- majority: [[251, 0, 0], [82, 0, 0], [123, 0, 0]]
- logreg: [[251, 0, 0], [0, 82, 0], [0, 4, 119]]
- gradient_boosting: [[251, 0, 0], [0, 81, 1], [0, 0, 123]]
- shortcut_stable_flag: [[211, 40, 0], [2, 80, 0], [3, 120, 0]]
- conditions_only: [[211, 30, 10], [82, 0, 0], [83, 40, 0]]

### (b) grouped by leakage run: stratified macro-F1

| model | stable_flag=0 | stable_flag=1 | cooler_pct=3 | cooler_pct=20 | cooler_pct=100 | valve_pct=73 | valve_pct=80 | valve_pct=90 | valve_pct=100 | accumulator_bar=90 | accumulator_bar=100 | accumulator_bar=115 | accumulator_bar=130 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| majority | 0.10 (n 240) | 0.33 (n 216) | 0.13 (n 164) | 0.46 (n 251) | too few cycles (n 41) | 0.10 (n 60) | 0.10 (n 60) | 0.10 (n 60) | 0.30 (n 276) | too few cycles (n 241) | 0.00 (n 123) | too few cycles (n 82) | too few cycles (n 10) |
| logreg | 0.99 (n 240) | 1.00 (n 216) | 0.98 (n 164) | 1.00 (n 251) | too few cycles (n 41) | 1.00 (n 60) | 1.00 (n 60) | 0.97 (n 60) | 0.98 (n 276) | too few cycles (n 241) | 1.00 (n 123) | too few cycles (n 82) | too few cycles (n 10) |
| gradient_boosting | 1.00 (n 240) | 1.00 (n 216) | 0.99 (n 164) | 1.00 (n 251) | too few cycles (n 41) | 1.00 (n 60) | 0.99 (n 60) | 1.00 (n 60) | 1.00 (n 276) | too few cycles (n 241) | 0.99 (n 123) | too few cycles (n 82) | too few cycles (n 10) |
| shortcut_stable_flag | 0.17 (n 240) | 0.33 (n 216) | 0.15 (n 164) | 0.99 (n 251) | too few cycles (n 41) | 0.17 (n 60) | 0.17 (n 60) | 0.17 (n 60) | 0.48 (n 276) | too few cycles (n 241) | 0.40 (n 123) | too few cycles (n 82) | too few cycles (n 10) |
| conditions_only | 0.00 (n 240) | 0.33 (n 216) | 0.00 (n 164) | 0.46 (n 251) | too few cycles (n 41) | 0.00 (n 60) | 0.00 (n 60) | 0.00 (n 60) | 0.29 (n 276) | too few cycles (n 241) | 0.00 (n 123) | too few cycles (n 82) | too few cycles (n 10) |

### (a) stratified random: the naive number: confusion matrices (rows true 0/1/2, columns predicted)

- majority: [[244, 0, 0], [99, 0, 0], [98, 0, 0]]
- logreg: [[244, 0, 0], [0, 99, 0], [0, 0, 98]]
- gradient_boosting: [[244, 0, 0], [3, 96, 0], [0, 0, 98]]
- shortcut_stable_flag: [[134, 0, 110], [0, 0, 99], [4, 0, 94]]
- conditions_only: [[160, 45, 39], [24, 28, 47], [28, 45, 25]]

### (a) stratified random: the naive number: stratified macro-F1

| model | stable_flag=0 | stable_flag=1 | cooler_pct=3 | cooler_pct=20 | cooler_pct=100 | valve_pct=73 | valve_pct=80 | valve_pct=90 | valve_pct=100 | accumulator_bar=90 | accumulator_bar=100 | accumulator_bar=115 | accumulator_bar=130 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| majority | 0.18 (n 303) | 0.49 (n 138) | 0.23 (n 127) | 0.23 (n 138) | 0.25 (n 176) | 0.18 (n 66) | 0.18 (n 75) | 0.17 (n 68) | 0.28 (n 232) | 0.28 (n 172) | 0.18 (n 85) | 0.20 (n 79) | 0.23 (n 105) |
| logreg | 1.00 (n 303) | 1.00 (n 138) | 1.00 (n 127) | 1.00 (n 138) | 1.00 (n 176) | 1.00 (n 66) | 1.00 (n 75) | 1.00 (n 68) | 1.00 (n 232) | 1.00 (n 172) | 1.00 (n 85) | 1.00 (n 79) | 1.00 (n 105) |
| gradient_boosting | 0.99 (n 303) | 1.00 (n 138) | 1.00 (n 127) | 1.00 (n 138) | 0.98 (n 176) | 1.00 (n 66) | 1.00 (n 75) | 0.96 (n 68) | 1.00 (n 232) | 1.00 (n 172) | 1.00 (n 85) | 0.99 (n 79) | 0.98 (n 105) |
| shortcut_stable_flag | 0.16 (n 303) | 0.49 (n 138) | 0.40 (n 127) | 0.38 (n 138) | 0.39 (n 176) | 0.15 (n 66) | 0.15 (n 75) | 0.15 (n 68) | 0.47 (n 232) | 0.43 (n 172) | 0.26 (n 85) | 0.25 (n 79) | 0.40 (n 105) |
| conditions_only | 0.26 (n 303) | 0.49 (n 138) | 0.38 (n 127) | 0.41 (n 138) | 0.37 (n 176) | 0.29 (n 66) | 0.21 (n 75) | 0.18 (n 68) | 0.43 (n 232) | 0.47 (n 172) | 0.24 (n 85) | 0.32 (n 79) | 0.40 (n 105) |

