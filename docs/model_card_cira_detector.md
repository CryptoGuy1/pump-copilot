# Model card: CIRA review detector (revision 3a-3)

Built from [reports/cira_scoring_eval.md](../reports/cira_scoring_eval.md) (the 3a-3 part,
with the earlier revisions for comparison) and [reports/replay_4a.md](../reports/replay_4a.md).
Data: [data card](data_card_cira.md).

## What it is
An unsupervised, per-signal deviation detector for real centrifugal-pump telemetry. For each
signal it forms a band from a baseline period at the start of each running period. It then
scores later 10-minute windows (one every minute) against that band. Three consecutive windows
outside the band suggest a review; review episodes less than 15 minutes apart become one case.
There is no learned model and no probability: scores are deviations in band units, and
calibration is not applicable.

- **Baseline:** per signal and run, from the later of its settling time and its first fresh
  reading, for 30 minutes (at least 15 minutes, 15 readings and 5 windows). The band is the
  centre ± max(k × MAD, 0.1% of the centre), with k = 8.
- **Settling times**, fixed in advance as engineering assumptions: pressure 5 min, vibration
  10 min, temperature 30 min after a start
  ([A7](ASSUMPTIONS.md#a7-fixed-settling-times-after-a-start-pressure-5-min-vibration-10-min-temperature-30-min)).
- **Inputs:** fresh readings in running periods only. Readings flagged stale or as spikes, and
  those in start or stop transitions, are excluded. Temperatures are taken relative to ambient.
- **Outputs:** one scored record per signal and window, in one of four states: normal, review
  suggested, insufficient evidence or data unavailable. Abstaining always carries a reason.

## Intended use
To point a reviewer at periods worth a look on the CIRA pumps, with the evidence behind each
suggestion, in a read-only replay. A case means "look here".

## Out-of-scope use
- Fault diagnosis, fault confirmation, or any claim of detection performance on real faults:
  CIRA has no fault labels.
- Alarms, interlocks or control actions of any kind.
- Other pumps, plants or sensors without new validation; ZeMA data (a model never scores
  another dataset).
- Time-to-failure or remaining useful life.

## Training and tuning data
Pump B on 11 June 2024 only. The tuning code loads no other pump-day. Settings were chosen on
June by the least running time in a case among settings that detected at least 80% of 6-sigma
step injections. The chosen setting detected 80% of 10 such injections, and 75% of June's
running time was in a case.

## Evaluation
October 2024 (B and A), scored once after the pre-registration commit. CIRA has no fault
labels, so review episodes on real data are unlabelled, neither confirmed nor refuted.

| pump | running h | unlabelled review episodes | cases | running time in a case |
|---|---|---|---|---|
| B | 6.665 | 36 | 1 | 90.5% |
| A | 2.298 | 6 | 2 | 55.9% |

Synthetic injections on B October (in memory, labelled SYNTHETIC; 15 per fault type and size).
These measure the pipeline and persistence rule, not real fault detection:

| fault | small | medium | large |
|---|---|---|---|
| step (1, 3, 6 sigma) | 13% | 33% | 47% |
| ramp (1, 3, 6 sigma) | 13% | 33% | 47% |
| drift (1, 3, 6 sigma) | 20% | 27% | 53% |
| stuck (5, 20, 60 min) | 100% | 100% | 100% |
| dropout (5, 20, 60 min) | 100% | 100% | 100% |

Replay reproduces batch scoring: the stored rows equal batch 3a-3 for both October days
([replay report](../reports/replay_4a.md)).

## Pre-registration
Commit `385263d68785643388397817690a9e12643ce39c` (tag `prereg-3a3`). The configuration, rules,
column map, code and migrations were checked unchanged since that commit before October was
scored. A first tuning attempt was discarded before October was scored, and the report records
it.

## Limitations
- **Not blind:** earlier steps showed summary statistics of the October files, so the analyst
  was not blind to October before tuning (the tuning procedure itself never reads October).
- **Noisy on real data:** pump B spends 90.5% of its October running time in one review case.
  Outlet pressure has the most review windows (336 of 358): its band from the run's start is
  narrow (42.649–43.215 bar), and only 2% of its clean October windows were normal.
- **Weak on slow and small faults:** step, ramp and drift detection rates are 13–53%; outlet
  pressure detected none of them. Stuck and dropout are always detected, the stuck faults by the
  stale flag.
- **One fit day:** tuning used one pump-day; pump A's settings were never tuned on pump A.
- **Earlier revisions:** 3a fitted a band on another day and abstained on pump A; 3a-2 was
  revised after seeing the 3a results (post hoc). Both are kept and reported, not hidden.
