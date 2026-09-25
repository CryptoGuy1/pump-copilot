# CIRA baseline scoring: protocol and evaluation

Every result below is labelled **REAL** (measured CIRA data, scored as recorded) or **SYNTHETIC** (faults injected into in-memory copies of real readings, provenance `synthetic_injection`, never written to the database). CIRA has no fault labels (the descriptor describes normal operation), so a review on REAL data can be neither confirmed nor refuted: we count them as unlabelled review episodes.

## Protocol

1. **Tuning data: B_2024-06-11 only.** Running time is split in time: the first 60% fits the baseline, the rest validates (2.393 running h). The tuning code loads no other pump-day (enforced by `test_tuning_only_ever_loads_b_june`).
2. **Grid:** k [3.0, 4.0, 5.0, 6.0, 8.0], consecutive_windows [2, 3, 5], window_s [300, 600, 1200], step_s [60]; 3 synthetic fault starts per fault type and size on the June validation part.
3. **Objective:** fewest unlabelled review episodes per running hour on the June validation part, then highest mean synthetic detection rate, then shortest median delay, then larger k and N.
4. **Freeze** the selected settings in `data/scoring_config.yaml`.
5. **Fit on B June, score B October once** (REAL unlabelled review episodes per running hour).
6. **Same for A** if its June fit passes the minimum-fit rule, otherwise record the abstention.
7. **Synthetic injections on B October** with the frozen settings (SYNTHETIC).

**Disclosure.** Earlier steps (audit, operating-state report, Table 1 checks) showed summary statistics of the October files, so the analyst was not blind to October before tuning. The tuning *procedure* never reads October data. The minimum fit duration (2 h) was chosen with pump A's June running time already known; see the exploratory A run below.

## Frozen configuration

```yaml
features: {window_s: 300, step_s: 60, min_fresh_readings: 3, min_fresh_fraction: 0.5}
baseline: {k: 6.0, floor_fraction: 0.001, min_fit_running_s: 7200, min_fit_readings: 100}
review: {consecutive_windows: 2}
normalization: {ambient_signal: ambient_temperature, barometric_signal: ambient_pressure, temperature_unit: degC}
injection:
  signals: [outlet_pressure, pump_vibration_velocity, motor_casing_temperature]
  offset_sizes_sigma: [1.0, 3.0, 6.0]
  durations_s: [300, 1200, 3600]
  ramp_s: 600
  drift_s: 7200
  offset_horizon_s: 3600
tuning: {split_fraction: 0.6}
```

Selected on June validation: window 300 s, step 60 s, k = 6.0, N = 2: 0 unlabelled review episodes (0.0 per running hour), mean synthetic detection rate 78%, median delay 3.5 min over 135 injections.

### Tuning table (June validation, best 12 of 45)

| window s | k | N | unlabelled reviews | per running h | mean detection | median delay min |
|---|---|---|---|---|---|---|
| 300 | 6.0 | 2 | 0 | 0.0 | 78% | 3.5 |
| 300 | 6.0 | 3 | 0 | 0.0 | 76% | 3.4 |
| 300 | 6.0 | 5 | 0 | 0.0 | 74% | 3.4 |
| 300 | 8.0 | 2 | 0 | 0.0 | 70% | 3.2 |
| 300 | 8.0 | 3 | 0 | 0.0 | 70% | 3.2 |
| 300 | 8.0 | 5 | 0 | 0.0 | 67% | 3.2 |
| 600 | 8.0 | 2 | 0 | 0.0 | 66% | 6.2 |
| 600 | 8.0 | 3 | 0 | 0.0 | 66% | 7.0 |
| 600 | 8.0 | 5 | 0 | 0.0 | 66% | 9.0 |
| 1200 | 8.0 | 2 | 0 | 0.0 | 61% | 11.2 |
| 1200 | 8.0 | 3 | 0 | 0.0 | 61% | 11.4 |
| 1200 | 8.0 | 5 | 0 | 0.0 | 61% | 11.4 |

## REAL: pump B (fit 2024-06-11, score 2024-10-30)

- Model `cira-band-cee7013ac407` version `840cea2414a1dab9` (hashes of the config and of the fit readings).
- Fit day running time: 5.957 h (minimum 2 h).
- Normalisation: temperatures (motor_accelerometer_contact_temperature, motor_casing_temperature, pump_accelerometer_contact_temperature) normalised as value minus ambient_temperature (latest ambient reading at or before each reading, same day only); barometric signal ambient_pressure found (range 3.0 mbar this day), not used: pressures are scored as measured.

Baseline bands (fit on June running windows):

| signal | center | band low | band high | MAD of window medians | fit readings | min fresh per window |
|---|---|---|---|---|---|---|
| motor_acceleration_peak | 29.726 | 20.499 | 38.953 m/s^2 | 1.54 | 2097 | 19 |
| motor_accelerometer_contact_temperature_rel_ambient | 12.663 | 3.1207 | 22.206 K | 1.59 | 1800 | 19 |
| motor_casing_temperature_rel_ambient | 10.612 | 5.3754 | 15.849 K | 0.873 | 2392 | 19 |
| motor_vibration_velocity | 0.0011026 | 0.0009867 | 0.0012184 m/s | 1.93e-05 | 2128 | 19 |
| outlet_pressure | 42.734 | 38.337 | 47.131 bar | 0.733 | 349 | 3 |
| pump_acceleration_peak | 27.192 | 18.962 | 35.422 m/s^2 | 1.37 | 275 | 3 |
| pump_accelerometer_contact_temperature_rel_ambient | 16.417 | 11.245 | 21.589 K | 0.862 | 267 | 3 |
| pump_vibration_velocity | 0.0031919 | 0.0028855 | 0.0034984 m/s | 5.11e-05 | 280 | 3 |

**October, scored once:** 6.665 running h, 3160 signal-windows (3160 ScoredEvidence records). **11 unlabelled review episodes = 1.6505 per running hour.**

| signal | normal | review_suggested | insufficient_evidence | data_unavailable |
|---|---|---|---|---|
| motor_acceleration_peak | 0 | 0 | 395 | 0 |
| motor_accelerometer_contact_temperature_rel_ambient | 0 | 0 | 395 | 0 |
| motor_casing_temperature_rel_ambient | 0 | 0 | 395 | 0 |
| motor_vibration_velocity | 0 | 0 | 395 | 0 |
| outlet_pressure | 383 | 9 | 3 | 0 |
| pump_acceleration_peak | 310 | 72 | 13 | 0 |
| pump_accelerometer_contact_temperature_rel_ambient | 359 | 22 | 14 | 0 |
| pump_vibration_velocity | 3 | 381 | 11 | 0 |

**Diagnostics (post-hoc, computed after the one October scoring; not used for tuning):** typical time between readings and median level of the eligible readings, June fit day vs October.

| signal | June interval s | October interval s | June median | October median | October median vs June band |
|---|---|---|---|---|---|
| motor_acceleration_peak | 8 | 60 | 29.24 | 126.4 | outside |
| motor_accelerometer_contact_temperature_rel_ambient | 8 | 60 | 12.64 | 15.73 | inside |
| motor_casing_temperature_rel_ambient | 8 | 61 | 10.1 | 13.54 | inside |
| motor_vibration_velocity | 8 | 60 | 0.001106 | 0.0015 | outside |
| outlet_pressure | 59 | 61 | 42.75 | 40.38 | inside |
| pump_acceleration_peak | 60 | 61 | 27.27 | 20.43 | inside |
| pump_accelerometer_contact_temperature_rel_ambient | 60 | 61 | 16.39 | 17.78 | inside |
| pump_vibration_velocity | 60 | 61 | 0.003199 | 0.00393 | outside |

| review episode: signal | start | end | windows | max band-relative score |
|---|---|---|---|---|
| outlet_pressure | 09:38:00 | 09:45:00 | 3 | 1.25 |
| outlet_pressure | 13:18:00 | 13:28:00 | 6 | 2.8 |
| pump_acceleration_peak | 13:18:00 | 13:26:00 | 4 | 50.9 |
| pump_acceleration_peak | 13:28:00 | 13:33:00 | 1 | 1.06 |
| pump_acceleration_peak | 13:31:00 | 13:36:00 | 1 | 1.03 |
| pump_acceleration_peak | 13:38:00 | 13:43:00 | 1 | 1.06 |
| pump_acceleration_peak | 13:46:00 | 14:11:00 | 21 | 1.15 |
| pump_acceleration_peak | 14:16:00 | 15:04:00 | 44 | 1.19 |
| pump_accelerometer_contact_temperature_rel_ambient | 08:43:00 | 09:09:00 | 22 | 1.49 |
| pump_vibration_velocity | 08:43:00 | 11:47:00 | 180 | 3.51 |
| pump_vibration_velocity | 11:45:00 | 15:10:00 | 201 | 6.74 |

## REAL: pump A (fit 2024-06-11, score 2024-10-30)

- Model `cira-band-cee7013ac407` version `0750a643004ceed4` (hashes of the config and of the fit readings).
- Fit day running time: 1.316 h (minimum 2 h).
- Normalisation: temperatures (motor_accelerometer_contact_temperature, motor_casing_temperature, pump_accelerometer_contact_temperature) normalised as value minus ambient_temperature (latest ambient reading at or before each reading, same day only); barometric signal ambient_pressure found (range 2.4 mbar this day), not used: pressures are scored as measured.
- **Abstained:** baseline abstained: fit day 2024-06-11 has 1.32 h running, minimum is 2.00 h. Every window for this pump is `insufficient_evidence`; October was not scored.

### EXPLORATORY (REAL data, outside the protocol): pump A with a 1 h minimum fit

The protocol's 2 h minimum fit was chosen with pump A's June running time (1.316 h) already known, so the abstention above was foreseeable when the rule was set. This run lowers the minimum only to show what the model would do. It is not a protocol result and was not used for tuning.

- Model `cira-band-1370c3d16196` version `58bab02256846895` (hashes of the config and of the fit readings).
- Fit day running time: 1.316 h (minimum 1 h).
- Normalisation: temperatures (motor_accelerometer_contact_temperature, motor_casing_temperature, pump_accelerometer_contact_temperature) normalised as value minus ambient_temperature (latest ambient reading at or before each reading, same day only); barometric signal ambient_pressure found (range 2.4 mbar this day), not used: pressures are scored as measured.
- Signals without a baseline: motor_casing_temperature_rel_ambient (65 fit readings < 100 required); outlet_pressure (71 fit readings < 100 required)

Baseline bands (fit on June running windows):

| signal | center | band low | band high | MAD of window medians | fit readings | min fresh per window |
|---|---|---|---|---|---|---|
| motor_acceleration_peak | 13.85 | 12.686 | 15.014 m/s^2 | 0.194 | 390 | 19 |
| motor_accelerometer_contact_temperature_rel_ambient | 12.116 | -2.756 | 26.988 K | 2.48 | 359 | 19 |
| motor_vibration_velocity | 0.0027895 | 0.0026607 | 0.0029183 m/s | 2.15e-05 | 394 | 19 |
| pump_acceleration_peak | 35.051 | 30.314 | 39.788 m/s^2 | 0.789 | 393 | 19 |
| pump_accelerometer_contact_temperature_rel_ambient | 12.355 | 5.4811 | 19.229 K | 1.15 | 333 | 19 |
| pump_vibration_velocity | 0.0038253 | 0.0037342 | 0.0039165 m/s | 1.52e-05 | 393 | 19 |

**October, scored once:** 2.298 running h, 1064 signal-windows (1064 ScoredEvidence records). **0 unlabelled review episodes = 0.0 per running hour.**

| signal | normal | review_suggested | insufficient_evidence | data_unavailable |
|---|---|---|---|---|
| motor_acceleration_peak | 0 | 0 | 133 | 0 |
| motor_accelerometer_contact_temperature_rel_ambient | 0 | 0 | 133 | 0 |
| motor_casing_temperature_rel_ambient | 0 | 0 | 133 | 0 |
| motor_vibration_velocity | 0 | 0 | 133 | 0 |
| outlet_pressure | 0 | 0 | 133 | 0 |
| pump_acceleration_peak | 0 | 0 | 133 | 0 |
| pump_accelerometer_contact_temperature_rel_ambient | 0 | 0 | 133 | 0 |
| pump_vibration_velocity | 0 | 0 | 133 | 0 |

**Diagnostics (post-hoc, computed after the one October scoring; not used for tuning):** typical time between readings and median level of the eligible readings, June fit day vs October.

| signal | June interval s | October interval s | June median | October median | October median vs June band |
|---|---|---|---|---|---|
| motor_acceleration_peak | 8 | 60 | 13.81 | 16.26 | outside |
| motor_accelerometer_contact_temperature_rel_ambient | 8 | 60 | 12.28 | 17.73 | inside |
| motor_casing_temperature_rel_ambient | 60 | 61 | 11.07 | 15.86 | - |
| motor_vibration_velocity | 8 | 60 | 0.00279 | 0.003013 | outside |
| outlet_pressure | 60 | 59 | 41.53 | 39.94 | - |
| pump_acceleration_peak | 8 | 61 | 34.86 | 33.52 | inside |
| pump_accelerometer_contact_temperature_rel_ambient | 8 | 61 | 12.31 | 14.05 | inside |
| pump_vibration_velocity | 8 | 61 | 0.003826 | 0.003713 | outside |

## SYNTHETIC: fault injection on B October

225 injections into in-memory copies of B_2024-10-30 readings (provenance `synthetic_injection`), 5 start times per fault and size, each scored against the June baseline and compared with the clean October scoring. Signals: outlet_pressure, pump_vibration_velocity, motor_casing_temperature. Offsets are multiples of the signal's robust sigma on the fit day (1.4826 x MAD of June running readings).

Faults: **step** (offset from t0), **ramp** (linear to full size over 10 min), **drift** (linear over 2 h), **stuck** (no new readings for the duration; the last value is held), **dropout** (readings and samples removed for the duration).

Detected means: step/ramp/drift -> `review_suggested` for that signal; stuck -> `insufficient_evidence` (too few fresh readings); dropout -> `data_unavailable` or `insufficient_evidence`; in each case in a window that was not already in that state in the clean scoring, and decided within the fault's horizon. Delay is from fault start to the end of the deciding window.

| fault | size | injections | detected | rate | median delay min |
|---|---|---|---|---|---|
| step | 1 sigma | 15 | 1 | 7% | 22.5 |
| step | 3 sigma | 15 | 1 | 7% | 22.5 |
| step | 6 sigma | 15 | 6 | 40% | 3.5 |
| ramp | 1 sigma | 15 | 1 | 7% | 22.5 |
| ramp | 3 sigma | 15 | 1 | 7% | 22.5 |
| ramp | 6 sigma | 15 | 6 | 40% | 12.5 |
| drift | 1 sigma | 15 | 2 | 13% | 79.5 |
| drift | 3 sigma | 15 | 2 | 13% | 79.5 |
| drift | 6 sigma | 15 | 7 | 47% | 103.5 |
| stuck | 5 min | 15 | 10 | 67% | 2.5 |
| stuck | 20 min | 15 | 10 | 67% | 3.0 |
| stuck | 60 min | 15 | 10 | 67% | 3.0 |
| dropout | 5 min | 15 | 10 | 67% | 2.5 |
| dropout | 20 min | 15 | 10 | 67% | 3.0 |
| dropout | 60 min | 15 | 10 | 67% | 3.0 |

By signal (rate / median delay min). A fault can only be detected where the clean scoring was not already in the target state, so the share of clean October windows normal for each signal bounds what this table can show:

| fault | size | outlet_pressure | pump_vibration_velocity | motor_casing_temperature |
|---|---|---|---|---|
| *clean October windows normal* | | *97%* | *1%* | *0%* |
| step | 1 sigma | 0% / - | 20% / 22.5 | 0% / - |
| step | 3 sigma | 0% / - | 20% / 22.5 | 0% / - |
| step | 6 sigma | 100% / 3.5 | 20% / 22.5 | 0% / - |
| ramp | 1 sigma | 0% / - | 20% / 22.5 | 0% / - |
| ramp | 3 sigma | 0% / - | 20% / 22.5 | 0% / - |
| ramp | 6 sigma | 100% / 12.5 | 20% / 22.5 | 0% / - |
| drift | 1 sigma | 0% / - | 40% / 79.5 | 0% / - |
| drift | 3 sigma | 0% / - | 40% / 79.5 | 0% / - |
| drift | 6 sigma | 100% / 103.5 | 40% / 79.5 | 0% / - |
| stuck | 5 min | 100% / 2.5 | 100% / 3.4 | 0% / - |
| stuck | 20 min | 100% / 3.0 | 100% / 3.0 | 0% / - |
| stuck | 60 min | 100% / 3.0 | 100% / 3.9 | 0% / - |
| dropout | 5 min | 100% / 2.5 | 100% / 3.4 | 0% / - |
| dropout | 20 min | 100% / 3.0 | 100% / 3.0 | 0% / - |
| dropout | 60 min | 100% / 3.0 | 100% / 3.9 | 0% / - |

**Limits of the synthetic track.** It measures this pipeline and its persistence rule, not real fault detection. Operating state, stale and spike flags come from the real recording and are not recomputed after injection, so an injected pressure fault never changes the running/off state, and an injected step is never itself flagged as a spike.

# 3a-2: post-hoc revision after 3a results

**Everything in this part is a post-hoc revision after 3a results.** The changes were designed after the 3a October results were seen, so October is not an unseen test set for them. Tuning still used B June only and October was scored once per mode, but these numbers are weaker evidence than a blind result.

## What changed (post-hoc revision after 3a results)

- **Per-signal windows.** A window spans `window_readings` typical reading intervals of that signal on that day (rounded up to whole minutes), so it can hold enough fresh readings whatever the sensor's update rate. The fresh-reading minimum is a fixed statistical 5 readings, no longer derived from June's update rate (which excluded all October motor signals in 3a).
- **Stuck sensors go through the stale flag.** A window overlapping a reading held past its stale limit (from `data/operating_rules.yaml`) is `insufficient_evidence` with reason `stale_suspected`. Stuck injections recompute that flag as the pipeline would, and only a stale decision counts as detecting them.
- **Within-run mode.** Each running segment is scored against a band fit on its own first `baseline_s` seconds after the transition window, with a configurable minimum. It needs no other day, so it also scores pump A.

## Frozen configurations (post-hoc revision after 3a results)

Both tuned on B_2024-06-11 only and frozen under `revision_3a2` in `data/scoring_config.yaml`.

Across-day: fewest unlabelled review episodes per running hour on the June validation part, then highest mean synthetic detection rate, then shortest median delay, then larger k and N. Grid k [3.0, 4.0, 5.0, 6.0, 8.0], consecutive_windows [2, 3, 5], window_readings [6, 10, 20], step_s [60].

```yaml
features: {window_s: 600, step_s: 60, min_fresh_readings: 5, min_fresh_fraction: 0.5, window_readings: 6,
  window_floor_s: 60, stale_via_flag: true}
baseline: {k: 6.0, floor_fraction: 0.001, min_fit_running_s: 7200, min_fit_readings: 100}
review: {consecutive_windows: 2}
```

Within-run (within-run: each run's first baseline_s seconds after the transition window are its baseline; the rest of the same run is scored): fewest unlabelled review episodes per scored running hour on June, then highest mean synthetic detection rate, then shortest median delay, then larger k, N and baseline_s. Grid k [3.0, 4.0, 5.0, 6.0, 8.0], consecutive_windows [2, 3, 5], window_readings [6, 10, 20], step_s [60], baseline_s [1200, 1800, 3600].

```yaml
features: {window_s: 600, step_s: 60, min_fresh_readings: 5, min_fresh_fraction: 0.5, window_readings: 10,
  window_floor_s: 60, stale_via_flag: true}
baseline: {k: 3.0, floor_fraction: 0.001, min_fit_running_s: 7200, min_fit_readings: 100}
review: {consecutive_windows: 5}
within_run: {baseline_s: 3600, min_baseline_s: 900, min_baseline_readings: 15, min_baseline_windows: 5}
```

## REAL: October scored once per mode (post-hoc revision after 3a results)

Unlabelled review episodes / per running hour (CIRA has no fault labels). Within-run hours exclude each run's baseline period. Window shares are normal / review / insufficient / unavailable over all signal-windows.

| mode | pump | running h scored | unlabelled reviews / per h | window shares |
|---|---|---|---|---|
| 3a across-day (committed) | B | 6.67 | 11 / 1.6505 | 33% / 15% / 51% / 0% |
| 3a across-day (committed) | A | - | abstained | - |
| 3a-2 across-day | B | 6.67 | 43 / 6.4519 | 49% / 35% / 15% / 0% |
| 3a-2 across-day | A | - | abstained | - |
| 3a-2 across-day, EXPLORATORY 1 h minimum | A | 2.3 | 8 / 3.4816 | 60% / 10% / 30% / 0% |
| 3a-2 within-run | B | 5.67 | 32 / 5.649 | 32% / 49% / 19% / 0% |
| 3a-2 within-run | A | 1.3 | 14 / 10.7877 | 49% / 40% / 12% / 0% |

### 3a-2 across-day, B: states per signal (post-hoc revision after 3a results)

| signal | normal | review_suggested | insufficient_evidence | data_unavailable |
|---|---|---|---|---|
| motor_acceleration_peak | 64 | 229 | 101 | 0 |
| motor_accelerometer_contact_temperature_rel_ambient | 266 | 6 | 122 | 0 |
| motor_casing_temperature_rel_ambient | 262 | 99 | 32 | 0 |
| motor_vibration_velocity | 26 | 228 | 140 | 0 |
| outlet_pressure | 365 | 14 | 14 | 0 |
| pump_acceleration_peak | 229 | 129 | 35 | 0 |
| pump_accelerometer_contact_temperature_rel_ambient | 343 | 23 | 27 | 0 |
| pump_vibration_velocity | 1 | 380 | 12 | 0 |

| review episode: signal | start | end | windows | max score |
|---|---|---|---|---|
| motor_acceleration_peak | 09:58:00 | 10:14:00 | 11 | 3.25 |
| motor_acceleration_peak | 10:16:00 | 10:39:00 | 18 | 4.01 |
| motor_acceleration_peak | 10:41:00 | 11:00:00 | 14 | 4.84 |
| motor_acceleration_peak | 11:08:00 | 12:22:00 | 69 | 10.5 |
| motor_acceleration_peak | 12:24:00 | 13:07:00 | 38 | 17.1 |
| motor_acceleration_peak | 13:09:00 | 13:26:00 | 12 | 17.9 |
| motor_acceleration_peak | 13:35:00 | 13:48:00 | 8 | 16.3 |
| motor_acceleration_peak | 13:50:00 | 14:16:00 | 21 | 16.4 |
| motor_acceleration_peak | 14:20:00 | 14:30:00 | 5 | 16.1 |
| motor_acceleration_peak | 14:32:00 | 15:10:00 | 33 | 16.8 |
| motor_accelerometer_contact_temperature_rel_ambient | 08:44:00 | 08:55:00 | 6 | 1.08 |
| motor_casing_temperature_rel_ambient | 08:32:00 | 09:10:00 | 32 | 1.81 |
| motor_casing_temperature_rel_ambient | 13:27:00 | 13:52:00 | 19 | 1.29 |
| motor_casing_temperature_rel_ambient | 13:54:00 | 14:48:00 | 48 | 1.17 |
| motor_vibration_velocity | 09:05:00 | 09:15:00 | 5 | 1.94 |
| motor_vibration_velocity | 09:20:00 | 09:56:00 | 31 | 2.66 |
| motor_vibration_velocity | 10:16:00 | 10:39:00 | 18 | 2.03 |
| motor_vibration_velocity | 10:41:00 | 11:00:00 | 14 | 2.73 |
| motor_vibration_velocity | 11:08:00 | 12:22:00 | 69 | 2.75 |
| motor_vibration_velocity | 12:24:00 | 12:49:00 | 20 | 2.21 |
| motor_vibration_velocity | 12:52:00 | 13:07:00 | 10 | 1.65 |
| motor_vibration_velocity | 13:14:00 | 13:26:00 | 7 | 1.94 |
| motor_vibration_velocity | 13:35:00 | 13:48:00 | 8 | 2.29 |
| motor_vibration_velocity | 13:56:00 | 14:16:00 | 15 | 1.9 |
| motor_vibration_velocity | 14:20:00 | 14:30:00 | 5 | 1.93 |
| motor_vibration_velocity | 14:32:00 | 14:58:00 | 21 | 2.36 |
| motor_vibration_velocity | 15:00:00 | 15:10:00 | 5 | 1.92 |
| outlet_pressure | 09:37:00 | 09:44:00 | 1 | 1.4 |
| outlet_pressure | 09:40:00 | 09:47:00 | 1 | 1.19 |
| outlet_pressure | 10:42:00 | 10:49:00 | 1 | 1 |
| outlet_pressure | 12:43:00 | 12:50:00 | 1 | 1.04 |
| outlet_pressure | 13:16:00 | 13:32:00 | 10 | 2.57 |
| pump_acceleration_peak | 09:59:00 | 10:07:00 | 2 | 1.01 |
| pump_acceleration_peak | 11:46:00 | 11:57:00 | 5 | 1.14 |
| pump_acceleration_peak | 12:20:00 | 12:32:00 | 6 | 1.13 |
| pump_acceleration_peak | 12:32:00 | 12:40:00 | 2 | 1.07 |
| pump_acceleration_peak | 12:50:00 | 13:13:00 | 17 | 1.13 |
| pump_acceleration_peak | 13:13:00 | 13:21:00 | 2 | 1.01 |
| pump_acceleration_peak | 13:17:00 | 13:25:00 | 2 | 10.9 |
| pump_acceleration_peak | 13:23:00 | 13:58:00 | 29 | 1.28 |
| pump_acceleration_peak | 14:00:00 | 15:10:00 | 64 | 1.41 |
| pump_accelerometer_contact_temperature_rel_ambient | 08:44:00 | 09:13:00 | 23 | 1.48 |
| pump_vibration_velocity | 08:44:00 | 15:10:00 | 380 | 6.05 |

### 3a-2 within-run, B: states per signal (post-hoc revision after 3a results)

| signal | normal | review_suggested | insufficient_evidence | data_unavailable |
|---|---|---|---|---|
| motor_acceleration_peak | 54 | 161 | 115 | 0 |
| motor_accelerometer_contact_temperature_rel_ambient | 45 | 137 | 148 | 0 |
| motor_casing_temperature_rel_ambient | 18 | 282 | 29 | 0 |
| motor_vibration_velocity | 176 | 0 | 154 | 0 |
| outlet_pressure | 23 | 295 | 11 | 0 |
| pump_acceleration_peak | 192 | 103 | 34 | 0 |
| pump_accelerometer_contact_temperature_rel_ambient | 12 | 295 | 22 | 0 |
| pump_vibration_velocity | 318 | 11 | 0 | 0 |

| review episode: signal | start | end | windows | max score |
|---|---|---|---|---|
| motor_acceleration_peak | 10:01:00 | 10:14:00 | 4 | 3.37 |
| motor_acceleration_peak | 10:19:00 | 10:39:00 | 11 | 4.21 |
| motor_acceleration_peak | 10:44:00 | 11:00:00 | 7 | 4.71 |
| motor_acceleration_peak | 11:11:00 | 12:22:00 | 62 | 11.4 |
| motor_acceleration_peak | 12:27:00 | 13:07:00 | 31 | 18.5 |
| motor_acceleration_peak | 13:12:00 | 13:26:00 | 5 | 19.2 |
| motor_acceleration_peak | 13:38:00 | 13:48:00 | 1 | 17.7 |
| motor_acceleration_peak | 13:53:00 | 14:16:00 | 14 | 17 |
| motor_acceleration_peak | 14:35:00 | 15:10:00 | 26 | 18.2 |
| motor_accelerometer_contact_temperature_rel_ambient | 09:40:00 | 09:56:00 | 7 | 1.16 |
| motor_accelerometer_contact_temperature_rel_ambient | 10:01:00 | 10:29:00 | 19 | 1.7 |
| motor_accelerometer_contact_temperature_rel_ambient | 10:44:00 | 11:00:00 | 7 | 1.9 |
| motor_accelerometer_contact_temperature_rel_ambient | 11:11:00 | 11:48:00 | 28 | 2.29 |
| motor_accelerometer_contact_temperature_rel_ambient | 11:59:00 | 12:15:00 | 7 | 2.48 |
| motor_accelerometer_contact_temperature_rel_ambient | 12:27:00 | 12:40:00 | 4 | 2.6 |
| motor_accelerometer_contact_temperature_rel_ambient | 12:46:00 | 12:59:00 | 4 | 2.71 |
| motor_accelerometer_contact_temperature_rel_ambient | 13:08:00 | 14:12:00 | 55 | 3.13 |
| motor_accelerometer_contact_temperature_rel_ambient | 14:30:00 | 14:45:00 | 6 | 2.96 |
| motor_casing_temperature_rel_ambient | 09:41:00 | 10:36:00 | 45 | 1.57 |
| motor_casing_temperature_rel_ambient | 10:48:00 | 13:52:00 | 174 | 3.02 |
| motor_casing_temperature_rel_ambient | 13:57:00 | 15:10:00 | 63 | 2.88 |
| outlet_pressure | 10:00:00 | 11:05:00 | 55 | 2.55 |
| outlet_pressure | 11:00:00 | 15:10:00 | 240 | 4.28 |
| pump_acceleration_peak | 12:21:00 | 12:38:00 | 7 | 1.29 |
| pump_acceleration_peak | 12:50:00 | 13:17:00 | 17 | 1.46 |
| pump_acceleration_peak | 13:26:00 | 13:58:00 | 22 | 1.88 |
| pump_acceleration_peak | 14:03:00 | 15:10:00 | 57 | 2.45 |
| pump_accelerometer_contact_temperature_rel_ambient | 09:35:00 | 09:52:00 | 7 | 1.18 |
| pump_accelerometer_contact_temperature_rel_ambient | 09:57:00 | 14:46:00 | 279 | 2.83 |
| pump_accelerometer_contact_temperature_rel_ambient | 14:51:00 | 15:10:00 | 9 | 2.5 |
| pump_vibration_velocity | 11:32:00 | 11:50:00 | 8 | 1.76 |
| pump_vibration_velocity | 12:18:00 | 12:31:00 | 3 | 1.6 |

### 3a-2 within-run, A: states per signal (post-hoc revision after 3a results)

| signal | normal | review_suggested | insufficient_evidence | data_unavailable |
|---|---|---|---|---|
| motor_acceleration_peak | 5 | 63 | 0 | 0 |
| motor_accelerometer_contact_temperature_rel_ambient | 24 | 32 | 12 | 0 |
| motor_casing_temperature_rel_ambient | 13 | 23 | 31 | 0 |
| motor_vibration_velocity | 16 | 42 | 10 | 0 |
| outlet_pressure | 62 | 6 | 0 | 0 |
| pump_acceleration_peak | 59 | 8 | 0 | 0 |
| pump_accelerometer_contact_temperature_rel_ambient | 33 | 23 | 11 | 0 |
| pump_vibration_velocity | 50 | 17 | 0 | 0 |

| review episode: signal | start | end | windows | max score |
|---|---|---|---|---|
| motor_acceleration_peak | 12:07:00 | 13:19:00 | 63 | 4.32 |
| motor_accelerometer_contact_temperature_rel_ambient | 12:38:00 | 13:19:00 | 32 | 1.32 |
| motor_casing_temperature_rel_ambient | 12:31:00 | 12:50:00 | 9 | 1.17 |
| motor_casing_temperature_rel_ambient | 12:55:00 | 13:19:00 | 14 | 1.36 |
| motor_vibration_velocity | 12:06:00 | 12:26:00 | 11 | 1.93 |
| motor_vibration_velocity | 12:25:00 | 12:52:00 | 18 | 4.52 |
| motor_vibration_velocity | 12:57:00 | 13:19:00 | 13 | 4.69 |
| outlet_pressure | 12:23:00 | 12:38:00 | 6 | 1.69 |
| pump_acceleration_peak | 12:06:00 | 12:17:00 | 1 | 1.52 |
| pump_acceleration_peak | 12:56:00 | 13:13:00 | 7 | 1.17 |
| pump_accelerometer_contact_temperature_rel_ambient | 12:46:00 | 13:19:00 | 23 | 1.34 |
| pump_vibration_velocity | 12:06:00 | 12:24:00 | 8 | 1.8 |
| pump_vibration_velocity | 12:34:00 | 12:46:00 | 2 | 1.45 |
| pump_vibration_velocity | 13:02:00 | 13:19:00 | 7 | 1.75 |

## SYNTHETIC: B October injections, both modes (post-hoc revision after 3a results)

Detection rate / median delay in minutes, all injected signals together. Same fault definitions as 3a, except that stuck must now be decided by the stale flag.

| fault | size | across-day (3a-2) | within-run (3a-2) | 3a (committed) |
|---|---|---|---|---|
| step | 1 sigma | 13% / 27.0 | 0% / - | 7% / 22.5 |
| step | 3 sigma | 27% / 4.5 | 33% / 12.5 | 7% / 22.5 |
| step | 6 sigma | 60% / 4.5 | 40% / 10.0 | 40% / 3.5 |
| ramp | 1 sigma | 13% / 30.5 | 0% / - | 7% / 22.5 |
| ramp | 3 sigma | 27% / 17.5 | 33% / 17.5 | 7% / 22.5 |
| ramp | 6 sigma | 60% / 11.4 | 40% / 13.4 | 40% / 12.5 |
| drift | 1 sigma | 20% / 55.5 | 0% / - | 13% / 79.5 |
| drift | 3 sigma | 33% / 54.5 | 33% / 91.5 | 13% / 79.5 |
| drift | 6 sigma | 67% / 71.9 | 33% / 50.5 | 47% / 103.5 |
| stuck | 5 min | 100% / 1.0 | 100% / 1.0 | 67% / 2.5 |
| stuck | 20 min | 100% / 1.4 | 100% / 0.5 | 67% / 3.0 |
| stuck | 60 min | 100% / 0.5 | 100% / 0.5 | 67% / 3.0 |
| dropout | 5 min | 93% / 3.0 | 0% / - | 67% / 2.5 |
| dropout | 20 min | 100% / 2.5 | 100% / 6.5 | 67% / 3.0 |
| dropout | 60 min | 100% / 2.5 | 100% / 6.5 | 67% / 3.0 |

By signal, across-day | within-run (rate / median delay min); the first row is the share of clean October windows that were normal, which bounds what can be detected:

| fault | size | outlet_pressure across | outlet_pressure within | pump_vibration_velocity across | pump_vibration_velocity within | motor_casing_temperature across | motor_casing_temperature within |
|---|---|---|---|---|---|---|---|
| *clean October windows normal* | | *93%* | *7%* | *0%* | *97%* | *67%* | *5%* |
| step | 1 sigma | 0% / - | 0% / - | 0% / - | 0% / - | 40% / 27.0 | 0% / - |
| step | 3 sigma | 0% / - | 0% / - | 0% / - | 100% / 12.5 | 80% / 4.5 | 0% / - |
| step | 6 sigma | 100% / 4.5 | 20% / 49.4 | 0% / - | 100% / 9.5 | 80% / 4.5 | 0% / - |
| ramp | 1 sigma | 0% / - | 0% / - | 0% / - | 0% / - | 40% / 30.5 | 0% / - |
| ramp | 3 sigma | 0% / - | 0% / - | 0% / - | 100% / 17.5 | 80% / 17.5 | 0% / - |
| ramp | 6 sigma | 100% / 11.5 | 20% / 49.4 | 0% / - | 100% / 13.4 | 80% / 8.0 | 0% / - |
| drift | 1 sigma | 0% / - | 0% / - | 0% / - | 0% / - | 60% / 55.5 | 0% / - |
| drift | 3 sigma | 0% / - | 0% / - | 0% / - | 100% / 91.5 | 100% / 54.5 | 0% / - |
| drift | 6 sigma | 100% / 97.5 | 0% / - | 0% / - | 100% / 50.5 | 100% / 34.5 | 0% / - |
| stuck | 5 min | 100% / 1.0 | 100% / 1.0 | 100% / 1.9 | 100% / 2.0 | 100% / 1.0 | 100% / 1.0 |
| stuck | 20 min | 100% / 0.5 | 100% / 0.5 | 100% / 1.5 | 100% / 1.5 | 100% / 0.5 | 100% / 0.5 |
| stuck | 60 min | 100% / 0.5 | 100% / 0.5 | 100% / 1.5 | 100% / 1.5 | 100% / 0.5 | 100% / 0.5 |
| dropout | 5 min | 100% / 3.0 | 0% / - | 100% / 3.9 | 0% / - | 80% / 3.0 | 0% / - |
| dropout | 20 min | 100% / 2.5 | 100% / 6.5 | 100% / 3.5 | 100% / 7.4 | 100% / 2.5 | 100% / 6.5 |
| dropout | 60 min | 100% / 2.5 | 100% / 6.5 | 100% / 3.5 | 100% / 6.5 | 100% / 2.5 | 100% / 6.5 |

**Stuck injections re-run (post-hoc revision after 3a results):** across_day: 45 of 45 detected, 45 of them decided by the stale flag; within_run: 45 of 45 detected, 45 of them decided by the stale flag.

