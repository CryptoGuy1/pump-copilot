# Assumptions register

Assumptions the pipeline relies on but cannot fully verify from the data. Each entry says what
we assume, why, what breaks if we are wrong, and what would settle it. When an assumption is
confirmed or overturned, update the entry (do not delete it) and note the date and source.

Sources cited below:
- **Descriptor**: Martone et al., "Sensor-Based Monitoring Data from an Industrial System of
  Centrifugal Pumps", *Data* 2025, 10(6), 91, DOI 10.3390/data10060091.
- **Audit**: `reports/cira_audit.json`, produced by `pumpcopilot audit cira`.
- **Dataset**: Martone, A. and Zazzaro, G. (2026). Centrifugal Pump Dataset (version 2)
  [dataset]. Zenodo. DOI 10.5281/zenodo.18479728.

---

## A1: CIRA timestamps are UTC

**Assumption.** Every CIRA timestamp is UTC. Timestamps with a `Z` suffix are kept as UTC. Naive
timestamps (none in the current files) are also treated as UTC (`cira.DEFAULT_NAIVE_TZ`).

**Evidence.**
- Descriptor §3.1: "The gateway collects the sensor data and timestamps them in UTC with a
  resolution of one second."
- All 8 files write timestamps as `YYYY-MM-DDThh:mm:ssZ`.
- Descriptor Table 1 startup and shutdown times match the first and last second above 20 bar
  outlet pressure in 7 of 8 files, to the second. So Table 1 uses the same clock as the files.
  This shows the files and descriptor agree with each other; on its own it does not prove UTC.
  The eighth, B_2024-10-30's shutdown, is an error in Table 1 (A6), not a clock difference.
- Against: the Zenodo README and descriptor Table 2 give the format as `YYYY-MM-DD hh:mm:ss`
  with no timezone. The audit records this for every file as "UTC marker present; README says
  no timezone; confirm with authors".

**Impact if wrong.** If the timestamps are actually Italian local time, every instant is off by
2 h on 2024-04-10 and 2024-06-11 (CEST) and by 1 h on 2024-10-30 (CET; DST ended 2024-10-27).
Order within a day and alignment across pumps stay correct, because all files shift together.
What would break: time-of-day features, any join with external data (weather, plant logs), and
the ±3 h span check in the audit, which would have less margin.

**How to revisit.** Ask the authors whether the `Z` means UTC or whether local time was written
with a UTC marker. If it is local time, treat `Z` as a plant-time marker in
`cira._parse_timestamps`, set `DEFAULT_NAIVE_TZ = "Europe/Rome"`, and update
`test_utc_marker_is_not_relocalized` and `test_naive_timestamps_default_to_utc`.

---

## A2: `Temp.PV` and `Pres.PV` are the descriptor's `X_Temp.SV` and `X_Pres.SV`

**Assumption.** The file headers `X_Temp.PV` (motor casing temperature, °C) and `X_Pres.PV`
(outlet fluid pressure, bar) are the columns descriptor Table 2 lists as `X_Temp.SV` and
`X_Pres.SV`. We use those units, but keep them `unit_verified: false` in
`data/cira_columns.yaml`.

**Evidence.**
- Table 2 and the Zenodo README both list `.SV`; all 8 files have `.PV` instead.
- The descriptor's own statistics (Tables 4–6) use `X_Temp.PV` and `X_Pres.PV`. Their values
  match ours: for example, A_Pres.PV on 2024-04-10 has median 0.452 and max 41.978 in both.
- By elimination, each file has exactly one pressure column and one temperature column beyond
  the accelerometer ones.
- The values are plausible: about 40–43 bar while running, against a nameplate maximum pressure
  of 40 bar (descriptor Table 3). The motor casing temperature tracks the accelerometer contact
  temperatures.
- Why the units stay unverified: the sensors are WirelessHART devices (descriptor §3.1). In
  HART, PV, SV, TV and QV are separate device variables (Primary to Quaternary), and each can
  carry its own quantity and unit depending on how the device is set up. The accelerometers
  show this: `.PV` is velocity, `.SV` the acceleration peak, `.TV` the contact temperature. So a
  unit stated for a `.SV` header is not a statement about the `.PV` one.

**Impact if wrong.** Two signals would have the wrong quantity or unit: `outlet_pressure` and
`motor_casing_temperature`. Pressure matters most, because it is the clearest sign of whether a
pump is running (see `data/operating_rules.yaml`). Until this is resolved, events for both carry `UNIT_UNVERIFIED`.

**How to revisit.** Ask the authors, or get the device configuration (HART device-variable
assignment), for the temperature and pressure transmitters. If confirmed, set
`unit_verified: true` for both entries and update
`test_column_map_matches_real_headers_and_descriptor`, which currently requires them to be
unverified.

---

## A3: Zenodo version 2 is authoritative; v1's `C_2024-10-30.csv` is excluded

**Assumption.** We use Zenodo record 18479728 (version 2 of 2, latest, 9 files). Version 1
(15301820) has one extra file, `C_2024-10-30.csv`. We treat that file as out of scope and pump C
as absent on 2024-10-30 (`cira.KNOWN_ABSENT`).

**Evidence.**
- The Zenodo API lists two versions of concept record 15301819 (retrieved 2026-09-24).
  18479728 is marked latest.
- The 9 files in version 2 have the same md5 as the matching files in version 1.
- The descriptor (Table 1 footnote), the version 1 description and the version 2 README all say
  pump C was turned off on 2024-10-30.
- Against: the descriptor cites version 1, and version 1 does contain a 6,144,055-byte
  `C_2024-10-30.csv` (md5 `1ee09c859372756f6232300363d1476b`). We have not downloaded or
  inspected it. `data/manifest.yaml` records it under `not_used`.

**Impact if wrong.** If that file holds real operation data, we are missing one pump-day: pump C
on 2024-10-30, the only day on which A, B and C could all be compared. The "8 files" check and
`KNOWN_ABSENT` would then be wrong. If it holds idle-pump data (most likely, given that
"turned off" is stated three times), we lose nothing that matters for the scope.

**How to revisit.** Download the version 1 file into a scratch location, outside `data/raw/`,
and audit it. Check whether the outlet pressure ever rises to running level. Ask the authors why
version 2 removed it. `acquire` re-lists the versions on every run: if a version 3 appears,
`downloaded.is_latest` in `manifest.lock.json` turns false, and this entry should be reviewed.

---

## A4: Min and median differ from descriptor Table 6 because the descriptor uses operating windows

**Assumption.** For A_2024-10-30, our full-file minimum and median differ from descriptor Table
6 because the descriptor computed its statistics only over each pump's operating window
(Table 1), not because we parse the file differently.

**Evidence.**
- Full-file maxima match Table 6 exactly: A_Pres.PV 42.487, A_ACR_Mot.SV 24.247.
- Restricted to the Table 1 window (10:59:15–13:19:13 UTC), A_ACR_Mot.SV matches much more
  closely: minimum 0.466 (exact match), median 15.945 vs 16.061, mean 15.918 vs 16.066. A_Pres.PV
  median 39.985 vs 39.884.
- The decimal-comma parse is clearly right: running pressure reads about 40 bar, not 0.04 or
  40,000.
- Not explained by this assumption:
  - Within that window, A_Pres.PV has a minimum of 37.594, while Table 6 gives 0.670.
  - Barometer over the A and B windows combined (1020.159 / 1021.488 / 1022.514 min/median/max)
    matches Table 6 (1021.407 / 1022.119 / 1022.473) no better than the full file does.
  So the window explains most of the difference, but the exact windowing the descriptor used is
  unknown.
- Supporting, from B_2024-10-30 (see A6): Table 6's maxima for four B signals match exactly
  over our derived operating window and are exceeded over the full file. So the descriptor did
  compute its statistics over operating windows, at least for that pump-day.

**Impact if wrong.** If the difference comes from our reading of the file instead, replayed
values for A on 2024-10-30 could be wrong, and this is the file that was previously misparsed.
The window question also matters for step 2, whose running/off rules are checked against
Table 1 (the one disagreement there, B_2024-10-30, is A6).

**How to revisit.** Write a small reproduction script for Tables 4–6 across all 8 files that
tries candidate windows: the full file, Table 1 windows, and pressure-threshold running state.
Keep the one that reproduces the descriptor, and turn that into a regression test. Ask the
authors which window Tables 4–6 use.

---

## A5: Readings are sample-and-hold; a repeated value is not a new measurement

**Assumption.** Each sensor sends a new reading every few seconds to a minute. The gateway
export repeats the last reading on every 1 Hz row until the next one arrives. So a value change
is a reading event, and a run of identical values is one reading being held, not many equal
measurements. The step 2 rules work on reading events (`operating.reading_events`):
- stale limits come from the time between readings while running;
- pressure decides the state, and a motor reading can veto running (idle reading: off,
  "pressurized while stopped") only if it was taken after the current pressure state began;
  without one, the running segment carries the attribute `motor_unconfirmed`;
- spikes are scored per reading, and a spike decision waits for up to 2 further readings
  (the decision delay in `reports/cira_operating_rules.md`).

**Evidence.**
- Typical time between readings while running (reading events, all 8 files): outlet pressure
  60 s, pump accelerometer 56 s, motor accelerometer, casing and ambient 8 s. The 99th
  percentiles are 62–123 s. Values change only at these events; in between, the 1 Hz rows
  repeat them exactly (to 9 or more decimals).
- Motor readings can be held far longer. On B_2024-06-11 the motor accelerometer's first
  reading after the 07:08:08 start came at 07:30:49 and was then held 30 min. On C_2024-06-11
  there was a single motor reading (11:18:10) during a 27-minute run. Meanwhile pressure was at
  about 42 bar, so the pump was running.
- Update rates can change between days. B's motor accelerometer and casing temperature
  reported every 8 s while running on 2024-06-11 but every 60 s on 2024-10-30, so
  per-window reading counts learned on one day do not carry over to another
  (`reports/cira_scoring_eval.md`, post-hoc diagnostics).
- The acquisition chain explains it: descriptor §3.2 says Telegraf reads the gateway over Modbus
  TCP. Modbus registers keep their last value until the device updates them.
- Against: descriptor §3.1 says "All sensors perform measurements at a sampling frequency of
  1 Hz". That is true of the rows in the files, not of the readings in them.

**Impact if wrong.** If the repeats were genuine 1 Hz measurements that happened to be equal,
then:
- stale flags would mark real steady values;
- the `motor_unconfirmed` running spans (1,395 s on C_2024-06-11, 1,239 s on B_2024-06-11)
  would instead be motor-idle readings under full pressure, i.e. "pressurized while stopped";
- per-reading spike scoring would under-count samples.

**How to revisit.** Ask the authors for the WirelessHART update (burst) period of each device
and the Telegraf polling interval. If a device's update period is known, it can replace the
derived interval in the stale and decision-delay calculations. If held readings turn out to be
live measurements, switch `stale_mask` and `vibration_evidence` back to per-sample logic, and
update the tests in `tests/test_operating.py` that assert per-reading behaviour.

---

## A6: B_2024-10-30 ran until 15:10:28; Table 1's 11:05:56 shutdown is not used

**Assumption.** Pump B ran on 2024-10-30 from 08:28:33 until 15:10:28 UTC, as derived from the
data. The descriptor's Table 1 gives shutdown at 11:05:56; we treat that value as an error in
the table, not in the data.

**Evidence.**
- After 11:05:56, every B signal says running:
  - outlet pressure 39–42 bar;
  - fresh motor peak readings of 83–101 m/s², against an idle level of about 0.47;
  - pump vibration about 0.004 m/s;
  - motor casing temperature steady at about 35.5 °C.
- At 15:10:28–15:11:00 they drop together: pressure to 0.7 bar, motor peak to 0.5 m/s².
- The motor-vibration check finds 0 s of "pressurized while stopped" on this pump-day, so this
  is not a pump that stopped while the line stayed under pressure.
- The descriptor's own Table 6 agrees with the data, not with Table 1:
  - Table 6's B maxima are motor peak 227.879 and pump peak 637.108. They occur at 14:59:48
    and 13:21:02, after 11:05:56. Within the Table 1 window the motor peak never exceeds 91.6.
  - Over our window 08:28:33–15:10:27, the maxima of B_Temp.PV (40.916), B_ACR_Mot.TV (42.805),
    B_ACR_Pmp.TV (44.234) and B_Pres.PV (43.101) match Table 6 exactly. Over the full file the
    first two are higher (41.289, 43.414), because temperatures keep rising after the stop.
- In the other 7 pump-days, our derived start and stop match Table 1 within 9 s. The one
  exception is A_2024-04-10's start, 120 s late: a single 38.6 bar pressure reading at 12:28:30
  is a flicker under the rule, not a start. Motor-based stops come a few seconds before the
  pressure drop, which fits A5.
- B's signals after 11:05:56 are not a copy of A's, which was running over part of the same
  period. Between 11:06 and 13:19, 0% of rows are identical for pressure, motor peak, motor
  contact temperature and casing temperature, and B's motor peak median is 134 m/s² against
  A's 16. The temperatures correlate (0.94–0.97) only because both pumps are warming up.

**Impact if wrong.** If B really stopped at 11:05:56, about 4 h of B_2024-10-30 would be
wrongly labelled running, and Table 6 itself would be inconsistent with Table 1. Baselines for B
would include those hours.

**How to revisit.** Ask the authors to confirm B's shutdown on 2024-10-30; 15:10 is our estimate
of the intended value. If Table 1 is confirmed as printed, find out what the signals after
11:05:56 are.

---

## A7: Fixed settling times after a start: pressure 5 min, vibration 10 min, temperature 30 min

**Assumption.** After a pump starts, a signal is treated as settled a fixed time after the run
start, set by its type: pressure 5 min, vibration (velocity and acceleration peak) 10 min,
temperature (casing and contact temperatures, relative to ambient) 30 min. The 3a-3 within-run
baseline of a signal starts at the later of that time and the signal's first fresh reading
(`onset.settling_s`, `scoring.settled_at`).

These are **engineering assumptions set in advance, not tuned values**. They were fixed before
the 3a-3 re-tune and are not part of the tuning grid. They replace a first attempt that derived
per-signal onset limits from B June's own rate of change. That rule let outlet pressure's
baseline start at the run start, and it made the baseline rule depend on the tuning day.

**Evidence.**
- Order of magnitude only, from general centrifugal-pump behaviour: discharge pressure follows
  the pump curve within seconds to a minute of reaching speed; vibration settles once speed and
  flow stabilise and any start-up transients have passed; motor and bearing temperatures rise
  over tens of minutes with a large thermal time constant.
- CIRA's pressure updates about every 60 s (A5), so 5 min gives several readings after the
  start.
- The CIRA descriptor gives no settling times, and no pump or motor datasheet in the dataset
  gives thermal time constants.

**Impact if wrong.**
- Too short (most likely for temperature, which on some days is still rising after 30 min):
  the baseline captures part of the warm-up, and the later steady level is reviewed as a
  shift.
- Too long: less running time is scored, and short runs abstain because the baseline cannot
  complete before the stop.

**How to revisit.** Get the motor's thermal time constant and the pump's start-up procedure
from the operator or the dataset authors. Or estimate settling per signal from several
labelled normal starts. Not from the scored days, and not as part of 3a-3, which is the last
detector revision.

---

## A8: Replay uses declared per-day constants and stored ingest annotations

**Assumption.** A replay session (Step 4a) scores only readings observed at or before its
cursor, but two kinds of input come from the whole stored day:

1. **Day constants**, recorded on the session (`replay_sessions.day_constants`):
   - each scored signal's median reading interval over the day, which sets the 3a-3 window
     length (`window_readings` x interval);
   - the day's scored signals;
   - the stale limits from `data/operating_rules.yaml`.
2. **Ingest annotations** stored with each reading at load time: operating state, the
   `stale_suspected` and `spike_suspected` flags, and the operating-state segments.

Batch 3a-3 uses the same values, so replay reproduces batch scoring exactly. That is what the
equivalence tests check.

**Evidence.**
- The intervals are a real lookahead. Taken from the readings known when a signal's baseline
  completes, instead of from the whole day, the window length differs on the real days. For
  example, B October motor peak is 420 s instead of 360 s. On B June the pump accelerometer's
  window is 60 s instead of 360 s, because its cadence changed during the day.
- The annotations have a bounded lookahead:
  - operating state: the minimum state duration of 1.5 pressure readings and the 2-reading
    transition window;
  - spike flag: the revert within 2 readings;
  - stale flag: set on a reading once its hold passes the limit, 64–123 s by signal. Scoring
    already uses stale evidence only from reading time + limit.

**Impact if wrong.** Live, none of these would be known in advance.
- A cadence change during a run would give a different window length than replay uses.
- A reading could be counted as eligible for up to one spike-revert window or one stale limit
  before it would be excluded live.
- Replay latency and scores are therefore those of the frozen 3a-3 detector fed its batch
  inputs, not of a fully causal detector.

**How to revisit.** A causal detector would fix the window length per signal when its
baseline completes, and derive state and flags online with an explicit decision delay. That
is a detector change and belongs to future work (3a-3 is the last detector revision).
