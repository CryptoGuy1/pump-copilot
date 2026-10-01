# Data card: ZeMA condition monitoring of hydraulic systems

> **Hydraulic test rig only.** This is a laboratory test rig, not a centrifugal pump; nothing
> derived from it scores CIRA data.

Built from `data/manifest.yaml`, the channel list in `src/pumpcopilot/zema.py` and
[reports/zema_benchmark.md](../reports/zema_benchmark.md). Model: [model card](model_card_zema.md).

## Source
Helwig, N., Pignanelli, E. and Schütze, A. (2015). Condition monitoring of hydraulic systems
[dataset]. UCI Machine Learning Repository (dataset 447). https://doi.org/10.24432/C5CW21

## Licence
CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). The raw files are downloaded by
`pumpcopilot acquire` and never committed or redistributed ([NOTICE](../NOTICE)).

## Contents
2,205 load cycles of 60 seconds from one hydraulic test rig, one row per cycle in each
channel file:

| channels | rate | unit |
|---|---|---|
| PS1–PS6 pressure | 100 Hz | bar |
| EPS1 motor power | 100 Hz | W |
| FS1–FS2 volume flow | 10 Hz | l/min |
| TS1–TS4 temperature | 1 Hz | °C |
| VS1 vibration | 1 Hz | mm/s |
| CE, CP, SE (virtual channels, computed rather than measured) | 1 Hz | %, kW, % |

The profile file labels every cycle with five conditions: cooler, valve, internal pump leakage,
accumulator and a stable flag. Cycles have no clock time.

Leakage, the only target here: 1,221 cycles with none, 492 weak and 492 severe, in 37
contiguous runs. The class counts come from the report's stable-flag table over all cycles.

## Known issues
- **Confounding:** the conditions are superimposed, so the leakage labels come with the cooler,
  valve and accumulator states of the test campaign. The report stratifies every result by them.
- **The stable flag is a shortcut:** 756 cycles are unstable, almost all with no leakage (732 of
  them). On its own, the flag predicts part of the leakage state through the test schedule.
- **Staged in blocks:** leakage changes in contiguous runs, so neighbouring cycles are not
  independent. A random split mixes them, which is why the chronological split is the primary
  result.
- **Time and condition are entangled:** the chronological test part, at the end of the
  recording, has the cooler at 100% only.

## What we derived
Nothing in the raw files is changed. Derived:
- **Per-cycle features** for each measured channel: summary statistics and slope, plus a spectral
  summary for the 100 Hz channels. They are cached locally and never committed. The virtual
  channels are excluded.
- **Splits:** stratified random, grouped by leakage run, and chronological with 50-cycle gaps.
  Each is defined by the pre-registration and reported in
  [the benchmark](../reports/zema_benchmark.md).
- **Scores** labelled "hydraulic test rig pump leakage state", with cycle ids and placeholder
  times.
