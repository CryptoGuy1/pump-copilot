# CIRA operating rules: derivation

Generated 2026-09-24 by `pumpcopilot rules cira` from 8 files. Rule text: header of `data/operating_rules.yaml`. Assumptions A1-A6 in `docs/ASSUMPTIONS.md`.

## Thresholds per pump (outlet pressure, bar)

| pump | idle median | idle ceiling | lower | upper | running floor | running median | Otsu split | running share | update s | min state s | transition s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 0.557 | 2.123 | **13.947** | **25.771** | 37.594 | 40.476 | 2.26 | 27.6% | 59.0 | 88.5 | 118.0 |
| B | 0.516 | 5.267 | **13.118** | **20.969** | 28.82 | 41.296 | 5.338 | 64.8% | 61.0 | 91.5 | 122.0 |
| C | 0.545 | 0.908 | **14.06** | **27.211** | 40.363 | 41.87 | 0.945 | 9.0% | 60.0 | 90.0 | 120.0 |

## Motor vibration confirmation (ACR_Mot.SV, m/s^2)

Same derivation as pressure, on log10 of the motor accelerometer peak value, with class quantiles [0.99, 0.01] (the peak value is heavy-tailed). Pressure decides the state; a motor reading can only veto running (idle reading: off, pressurized while stopped), and only if it was taken after the current pressure state began: the sensor is sample-and-hold, and a reading held from before a start says nothing about the start. Running without such a reading stays running, with the segment attribute motor_unconfirmed.

| pump | idle median | idle ceiling | lower | upper | running floor | running median | update s (running) | min state s |
|---|---|---|---|---|---|---|---|---|
| A | 0.4656 | 0.5821 | **1.574** | **4.256** | 11.5345 | 14.9968 | 8.0 | 12.0 |
| B | 0.4677 | 0.5834 | **1.9099** | **6.2373** | 20.4174 | 35.156 | 8.0 | 12.0 |
| C | 0.4613 | 0.5 | **1.4689** | **4.3251** | 12.735 | 19.4089 | 8.0 | 12.0 |

## Pressure histograms (2.5 bar bins, all days pooled)

| bin (bar) | A | B | C |
|---|---|---|---|
| 0-2.5 | 34508 | 24635 | 23615 |
| 2.5-5 | 0 | 0 | 0 |
| 5-7.5 | 0 | 61 | 0 |
| 7.5-10 | 0 | 0 | 0 |
| 10-12.5 | 0 | 0 | 0 |
| 12.5-15 | 0 | 0 | 0 |
| 15-17.5 | 0 | 0 | 0 |
| 17.5-20 | 0 | 0 | 0 |
| 20-22.5 | 0 | 0 | 0 |
| 22.5-25 | 0 | 0 | 0 |
| 25-27.5 | 0 | 0 | 0 |
| 27.5-30 | 0 | 122 | 0 |
| 30-32.5 | 0 | 115 | 0 |
| 32.5-35 | 0 | 244 | 0 |
| 35-37.5 | 12 | 1267 | 0 |
| 37.5-40 | 4334 | 7736 | 0 |
| 40-42.5 | 8355 | 21614 | 1436 |
| 42.5-45 | 476 | 13519 | 903 |
| 45-47.5 | 0 | 905 | 0 |

## Sample-and-hold readings

A reading event is a value change; the gateway repeats the last reading at 1 Hz in between. Intervals are seconds between reading events, pooled over pumps and days.

| signal | readings | while running | median interval | median interval running | p99 interval running |
|---|---|---|---|---|---|
| ACR_Mot.PV | 6700 | 3264 | 8 | 8 | 65.4 |
| ACR_Mot.SV | 5461 | 3247 | 8 | 8 | 66 |
| ACR_Mot.TV | 5920 | 2876 | 8 | 8 | 69 |
| ACR_Pmp.PV | 4343 | 1419 | 9 | 56 | 67 |
| ACR_Pmp.SV | 3259 | 1413 | 14 | 56 | 86.1 |
| ACR_Pmp.TV | 3843 | 1315 | 10 | 57 | 123 |
| Barometer | 12630 | 3839 | 8 | 8 | 62 |
| Pres.PV | 2517 | 1014 | 60 | 60 | 64 |
| Temp.PV | 6341 | 3239 | 9 | 8 | 62 |
| Temperature | 12154 | 3811 | 8 | 8 | 62 |

## Stale before and after

Before: step 2a rule, held at least max(60 s, 3 x median interval). After: held longer than the p99 interval while running.

| signal | limit before s | limit after s | stale % all: before | after | stale % running: before | after |
|---|---|---|---|---|---|---|
| ACR_Mot.PV | 60 | 65.4 | 54.88 | 37.76 | 41.94 | 15.26 |
| ACR_Mot.SV | 60 | 66 | 57.89 | 43.52 | 41.94 | 14.38 |
| ACR_Mot.TV | 60 | 69 | 55.43 | 38.06 | 42.99 | 16.12 |
| ACR_Pmp.PV | 60 | 67 | 64.83 | 37.13 | 60.86 | 14.34 |
| ACR_Pmp.SV | 60 | 86.1 | 69.01 | 46.33 | 61.35 | 15.22 |
| ACR_Pmp.TV | 60 | 123 | 65.39 | 36.39 | 61.79 | 12.94 |
| Barometer | 60 | 62 | 13.24 | 1.16 | 17.37 | 1.33 |
| Pres.PV | 180 | 64 | 0.11 | 0.87 | 0.13 | 0.46 |
| Temp.PV | 60 | 62 | 45.39 | 2.1 | 41.99 | 1.7 |
| Temperature | 60 | 62 | 13.17 | 0.86 | 17.38 | 0.93 |

## Spike decision delay

A reading with robust z above 8 is only flagged once a later reading returns to within z 3.5 of the baseline, within 2 readings. So a spike decision waits for up to 2 further readings: the delay below is that many reading intervals while running. Replay and live scoring must hold the flag back that long.

| signal | typical delay s (2 x median interval) | slow delay s (2 x p99) |
|---|---|---|
| ACR_Mot.PV | 16 | 130.8 |
| ACR_Mot.SV | 16 | 132 |
| ACR_Mot.TV | 16 | 138 |
| ACR_Pmp.PV | 112 | 134 |
| ACR_Pmp.SV | 112 | 172.2 |
| ACR_Pmp.TV | 114 | 246 |
| Barometer | 16 | 124 |
| Pres.PV | 120 | 128 |
| Temp.PV | 16 | 124 |
| Temperature | 16 | 124 |

## Check against descriptor Table 1

Start and stop are changes of the final state (pressure, with the motor veto). Our stop is the first sample below the threshold, so +1 s is an exact match.

| pump-day | Table 1 startup | ours | diff s | first confirmed running | Table 1 shutdown | ours | diff s | pressurized while stopped s | running s: motor confirmed | motor unconfirmed |
|---|---|---|---|---|---|---|---|---|---|---|
| A_2024-04-10 | 12:28:30 | 12:30:30 | 120 | 12:32:30 | 12:49:28 | 12:49:29 | 1 | 0 | 1019 | 0 |
| A_2024-06-11 | 10:00:25 | 10:00:25 | 0 | 10:04:06 | 11:21:24 | 11:21:20 | -4 | 5 | 4166 | 103 |
| A_2024-10-30 | 10:59:15 | 10:59:15 | 0 | 11:05:06 | 13:19:13 | 13:19:05 | -8 | 9 | 8039 | 233 |
| B_2024-04-10 | 12:51:42 | 12:51:42 | 0 | 12:53:44 | 12:56:41 | 12:56:42 | 1 | 0 | 178 | 0 |
| B_2024-06-11 | 07:08:08 | 07:08:08 | 0 | 07:30:49 | 13:07:33 | 13:07:34 | 1 | 0 | 19755 | 1239 |
| B_2024-10-30 | 08:28:33 | 08:28:33 | 0 | 08:45:04 | 11:05:56 | 15:10:28 | 14672 | 0 | 23124 | 869 |
| C_2024-04-10 | 12:58:16 | 12:58:16 | 0 | 13:00:16 | 13:10:14 | 13:10:05 | -9 | 10 | 589 | 0 |
| C_2024-06-11 | 10:52:55 | 10:52:55 | 0 | 11:18:10 | 11:19:54 | 11:19:55 | 1 | 0 | 105 | 1395 |
