# Data card: CIRA Centrifugal Pump Dataset

Built from `data/manifest.yaml`, `data/cira_columns.yaml`,
[reports/cira_audit.json](../reports/cira_audit.json) (the file audit) and the
[assumptions register](ASSUMPTIONS.md). Model: [model card](model_card_cira_detector.md).

## Source
- **Dataset:** Martone, A. and Zazzaro, G. (2026). Centrifugal Pump Dataset (version 2)
  [dataset]. Zenodo. https://doi.org/10.5281/zenodo.18479728
- **Data descriptor:** Martone, A., D'Ambrosio, A., Ferrucci, M., Cembalo, A., Romano, G. and
  Zazzaro, G. (2025). Sensor-Based Monitoring Data from an Industrial System of Centrifugal
  Pumps. Data 10(6), 91. https://doi.org/10.3390/data10060091
- **Version used:** record 18479728, version 2 of 2
  ([A3](ASSUMPTIONS.md#a3-zenodo-version-2-is-authoritative-v1s-c_2024-10-30csv-is-excluded)).

## Licence
CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), from the Zenodo metadata; the data
descriptor states the same. The raw files are downloaded by `pumpcopilot acquire` and never
committed or redistributed ([NOTICE](../NOTICE)).

## Contents
Real telemetry from three centrifugal pumps (A, B, C) on three separate days: 10 April,
11 June and 30 October 2024. That makes eight CSV files; pump C has no 30 October file
because it was off that day.

| file | rows | span (UTC) | cadence |
|---|---|---|---|
| A, B, C on 2024-04-10 | 4,980 each | 12:00–14:00 | 5 s until 12:46:15, then 1 s |
| A on 2024-06-11 | 21,429 | 10:00–16:00 | 1 s |
| B on 2024-06-11 | 35,829 | 06:00–16:00 | 1 s |
| C on 2024-06-11 | 21,429 | 10:00–16:00 | 1 s |
| A on 2024-10-30 | 21,600 | 10:30–16:30 | 1 s |
| B on 2024-10-30 | 29,700 | 08:15–16:30 | 1 s |

That is 144,927 data rows in all. Every file has a timestamp and 10 measurements. Each pump has
two accelerometers, motor and pump, giving vibration velocity (m/s), acceleration peak (m/s²)
and contact temperature (°C). There are also motor casing temperature (°C), outlet pressure
(bar), and the site's barometric pressure and ambient temperature. The audit found no duplicate
or unparseable timestamps. There is one 172 s gap across all pumps on 11 June after 10:11:10.

The data covers normal operation: there are no fault labels, no maintenance outcomes, no
suction pressure and no flow.

## Known issues
Each is handled by an assumption in the register (the data-quality page lists them too):

- [A1](ASSUMPTIONS.md#a1-cira-timestamps-are-utc): the documentation gives no time zone; the
  timestamps are taken as UTC.
- [A2](ASSUMPTIONS.md#a2-temppv-and-prespv-are-the-descriptors-x_tempsv-and-x_pressv): the
  pressure and temperature headers are `.PV` where the descriptor says `.SV`, so their units
  stay unverified.
- [A3](ASSUMPTIONS.md#a3-zenodo-version-2-is-authoritative-v1s-c_2024-10-30csv-is-excluded):
  the two Zenodo versions differ by one file (pump C, 30 October), which is not used.
- [A4](ASSUMPTIONS.md#a4-min-and-median-differ-from-descriptor-table-6-because-the-descriptor-uses-operating-windows):
  summary statistics differ from the descriptor's Table 6, which uses operating windows.
- [A5](ASSUMPTIONS.md#a5-readings-are-sample-and-hold-a-repeated-value-is-not-a-new-measurement):
  readings are sample-and-hold, so a repeated value is not a new measurement.
- [A6](ASSUMPTIONS.md#a6-b_2024-10-30-ran-until-151028-table-1s-110556-shutdown-is-not-used):
  pump B's 30 October stop time in Table 1 does not match the data.
- [A7](ASSUMPTIONS.md#a7-fixed-settling-times-after-a-start-pressure-5-min-vibration-10-min-temperature-30-min):
  signals take time to settle after a start; fixed settling times are assumed.
- [A8](ASSUMPTIONS.md#a8-replay-uses-declared-per-day-constants-and-stored-ingest-annotations):
  replay uses declared per-day constants and stored ingest annotations.

## What we derived
Nothing in the raw files is changed. Derived, and stored separately:
- **Operating state** per pump and day (running, off, unknown) from outlet pressure, with motor
  vibration able only to veto ([operating rules](../reports/cira_operating_rules.md)).
- **Quality flags** on each reading (stale or spike suspected, duplicate timestamp, gap before,
  placeholder suspected, unit unverified), kept as flags and never used to change a value.
- **Telemetry and readings tables** in TimescaleDB: 1,449,270 telemetry rows (the 144,927 rows ×
  10 measurements) and 63,238 readings ([replay report](../reports/replay_4a.md)).
- **Scores and cases** from the detector, and **synthetic fault injections**, applied to
  in-memory copies only and labelled SYNTHETIC ([CIRA report](../reports/cira_scoring_eval.md)).
- Temperatures are also scored relative to the ambient temperature.
