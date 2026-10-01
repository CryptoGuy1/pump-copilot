# Project scope (summary)

A concise summary of the original project scope. The original scope document is not in this
repository; this summary is reconstructed from what the repository records of it (the README,
[ADR-0001](adr/0001-scope-adjustments.md), the guardrails in `src/pumpcopilot/schema.py`, the
manifest and the step history). Where ADR-0001 changed how the scope is delivered, the
adjustment is noted; the claim boundaries are unchanged.

## Goal
A read-only decision-support prototype for a refinery-style pump service, built as a portfolio
demonstration on public data: replay real pump telemetry, score it against a baseline, open
review cases with their evidence, help an engineer review a case with a checked assistant, and
evaluate every part honestly, including the results that did not go well.

## Claim boundaries
- **Read-only.** No control actions and no plant alarms; there is no alarm state in the data
  contract, and escalation is an export.
- **Public data only.** No refinery or plant data.
- **One model, one dataset.** A model fit on one dataset never scores another; ZeMA results do
  not transfer to the CIRA pumps.
- **The bench label stays.** ZeMA outputs are "hydraulic test rig pump leakage state" only.
- **No fault claims on CIRA.** CIRA has no fault labels, so a case means "look here", and a
  review on real data is unlabelled, not false. Synthetic fault injections are labelled
  SYNTHETIC and measure the pipeline, not real fault detection.
- **Observations, not diagnoses.** Evidence states what was measured. Abstaining needs a reason,
  and suggesting a review needs evidence.
- **Calibration is measured, not claimed.**

## Datasets
- **CIRA Centrifugal Pump Dataset** (Zenodo record 18479728, version 2, CC BY 4.0): the primary
  source, real centrifugal-pump telemetry under normal operation, replayed per pump-day.
- **ZeMA condition monitoring of hydraulic systems** (UCI 447, CC BY 4.0): a separate benchmark
  page for the test rig's pump leakage state.
- **4TU / Tata Steel centrifugal pump faults**: a candidate labelled benchmark for a later
  phase, not in scope yet (ADR-0001 §5).

Raw data is downloaded from the original sources and never committed or redistributed
([NOTICE](../NOTICE)).

## Phases
- **Week 1: data contract, acquisition and audit**, with a stop rule: acquisition stops if a
  source's version, licence or hashes do not match `data/manifest.yaml`.
- **Operating state and loading:** CIRA running state and data-quality flags, loaded into
  TimescaleDB.
- **Scoring and evaluation:** a CIRA baseline and detector, pre-registered and evaluated once;
  the ZeMA benchmark, pre-registered, with the random-against-chronological split gap as the
  headline (ADR-0001 §3).
- **Replay and API:** a replay worker with persisted scores and cases, and a read-only API with a
  live stream.
- **Frontend:** the fleet, asset-day, case, replay, evaluation and data-quality views.
- **Assistant:** a copilot whose every answer is checked before it is shown, evaluated on
  questions written blind.
- **Public readiness:** licences, credits and a review of what the repository makes public.

The budget is about 80 hours in total, which is why ADR-0001 chose a modular monolith over
separate services.
