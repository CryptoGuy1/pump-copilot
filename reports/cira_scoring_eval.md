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
