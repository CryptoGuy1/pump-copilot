# Model card: ZeMA hydraulic test rig pump-leakage classifier

> **Hydraulic test rig only.** These results come from a hydraulic test rig. They do not
> transfer to centrifugal pumps, and no ZeMA model scores CIRA data.

Built from [reports/zema_benchmark.md](../reports/zema_benchmark.md). Data:
[data card](data_card_zema.md).

## What it is
A three-class classifier of the rig's internal pump leakage state (0 none, 1 weak, 2 severe)
from per-cycle summary features. The headline model is logistic regression in every split,
chosen by validation macro-F1. Its outputs always read "hydraulic test rig pump leakage
state: k".

- **Features:** per cycle and measured channel, the mean, standard deviation, minimum, maximum,
  5/25/50/75/95th percentiles and slope, plus a spectral summary for the 100 Hz channels. The
  virtual channels CE, CP and SE are excluded.
- **Compared with:** always guessing the majority class, gradient boosting, the stable flag
  alone (a shortcut), and the four other condition labels alone (conditions only, which are not
  observable in practice and show the confounding).

## Intended use
A public benchmark showing how much a pump-leakage result on this rig depends on the split. It
appears on its own page, never mixed with the CIRA detector.

## Out-of-scope use
- Any pump other than this rig's, including the CIRA centrifugal pumps or a refinery pump.
- Maintenance decisions or alarms.
- The cooler, valve, accumulator or stability states, which are not targets.

## Evaluation
Evaluated once on the test parts after the pre-registration. Intervals are 95% block-bootstrap
intervals over leakage label runs, not over cycles.

| split (test cycles) | logreg macro-F1 (95% interval) | always guessing | recall 0 / 1 / 2 |
|---|---|---|---|
| chronological, 50-cycle gaps (391): **primary** | 0.476 (0.244–0.611) | 0.187 | 0.04 / 1.00 / 0.65 |
| grouped by leakage run (456) | 0.987 (0.614–1.000) | 0.237 | 1.00 / 1.00 / 0.97 |
| stratified random (441) | 1.000 (1.000–1.000) | 0.237 | 1.00 / 1.00 / 1.00 |

- **The headline is the gap:** the same model on the same data scores 1.000 on a random split
  and 0.476 on the chronological one. Its validation macro-F1 on the chronological split was
  0.707.
- **Calibration** on the chronological test part is very poor: Brier 0.875 and top-label ECE
  0.404. On the random split it is good (Brier 0.001, ECE 0.004).
- **Shortcut:** the stable flag alone carries part of the leakage information, so a model can
  look good by learning the rig's test schedule. The report gives the stable-flag table, its
  mutual information, and every result stratified by the other conditions.

## Pre-registration
Tag `prereg-3b-r2`, commit `3ab44276f4785e0341282bef69ca1f99a3d52d11`. It supersedes
`prereg-3b` (`eae1dbd`), whose test parts were never opened. The evaluation refuses to run if
the code or the configuration changed since the tag.

## Limitations
- **Time and condition are confounded:** the chronological test part is later in the recording
  and covers different conditions (the cooler at 100% only), so the drop may come from drift,
  condition shift or both.
- **Few runs:** the chronological test part has 9 leakage runs, hence the wide interval.
- **The random split is the naive number:** cycles of one leakage run fall in both its
  training and test parts, so its near-perfect score is not evidence of generalisation.
- **One rig:** unseen-rig generalisation is untested.
