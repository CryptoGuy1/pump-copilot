# Project scope (summary)

A summary, in our own words, of the original project definition and plan (24 September 2026).
The original document is kept outside the repository. The last section lists where the project
deliberately departed from it, with links to the decision or report.

## Goal
Build a read-only decision-support application for a refinery-style centrifugal pump service,
as an industry-oriented portfolio demonstration on public data with honestly bounded claims.
It ingests and replays public equipment data, flags abnormal behaviour, distinguishes measured
fault states only where labels exist, shows the evidence on an operator dashboard, and opens a
reviewable maintenance case. An optional assistant summarises only the evidence (and approved
troubleshooting notes). Nothing controls a physical process or raises a plant alarm.

The question it answers: which pump or cycle deserves review, what measured evidence supports
that, how certain the system is, and what a qualified person should verify next.

The refinery is a realistic deployment scenario, not where the data was recorded; no refinery
supplied data or approved a deployment.

## User journey
An operator sees a pump on the fleet page as "review suggested" (never a safety alarm), with a
banner saying whether the feed is live, replayed, delayed or missing. The asset page shows recent
vibration, temperature and pressure with units, timestamps, a baseline band and the change that
triggered the suggestion. An evidence panel gives the signals, missing values, operating state,
score, persistence rule, model version and exact window, or says "insufficient data". The
assistant drafts an explanation and checks, keeping hypotheses labelled as hypotheses. The
operator acknowledges; an engineer adds notes, a disposition and optionally exports a draft
ticket (nothing is sent). The ZeMA classifier lives on a separate benchmark page.

## Claim boundaries
- **Read-only:** no control actions, no plant alarms, no DCS/PLC commands, no safety functions;
  escalation is an export, and no real work order is sent.
- **Not predictive maintenance:** no time-to-failure or remaining useful life, no guarantee of
  detection before a breakdown, no cavitation inferred without labels, no automated dispatch.
- **Datasets stay apart:** a ZeMA classifier is never reported as validated on CIRA or on a
  refinery, and its output keeps the label "hydraulic test rig pump leakage state".
- **CIRA has no fault labels:** its deviations are review candidates, not faults; no fault
  accuracy or sensitivity is reported for it.
- **Four presentation states:** normal, review suggested, insufficient evidence, data
  unavailable. An anomaly score alone can never become a high-priority alarm.
- **Business value is a scenario, not a result:** any cost or downtime saving is a model with
  supplied assumptions, kept separate from the measured scorecard.
- Out of scope as well: refinery validation, training a language model, and real historian
  integration without licensed access.

## Datasets
- **ZeMA hydraulic test rig** (UCI 447, CC BY 4.0): the labelled benchmark. 2,205 one-minute
  cycles; the target is internal pump leakage (none, weak, severe). The cooler, valve,
  accumulator and stable-flag labels are for confounding analysis, not model inputs; the
  virtual channels (CE, CP, SE) are excluded.
- **CIRA centrifugal pumps** (Zenodo record 18479728, version 2): real telemetry of three pumps
  over three separate days (eight files; pump C is off on 30 October). It is used for replay,
  data quality and unsupervised monitoring; separate days are never joined into one stream.
- **A short authored playbook** of review notes was planned to ground the assistant.
- Later, out of scope: pump audio (DCASE 2021) for a separate exercise, never merged with
  ZeMA or CIRA.

Raw data is downloaded from the original sources, not redistributed ([NOTICE](../NOTICE)).

## Planned architecture
A data adapter into one canonical event schema, a replay worker emitting ordered events
through a local broker (buffering through outages), a feature and scoring service, an
application API storing asset state, events and cases, and a web dashboard; FastAPI,
PostgreSQL with a time-series store, React or Streamlit, all in Docker Compose. Optional
extras: an Azure ingestion path and a Raspberry Pi as a replay gateway. Raw inputs, derived
features and decisions are stored separately so every case is auditable.

## Phases
1. **Client problem and acceptance criteria:** a one-page brief for a fictional refinery
   reliability manager, with open questions recorded rather than invented answers.
2. **Data audit and asset model:** check ZeMA's alignment and label combinations; audit each
   CIRA file's cadence, gaps, units, shutdowns and placeholders; define running, off and unknown
   by stated rules.
3. **Baselines and models:** on ZeMA, simple baselines and learned models on a leakage-safe,
   pre-declared split with a frozen test set, stratified by the other conditions; on CIRA, a
   per-pump robust baseline, with changes across dates treated as events for review.
4. **Advisory and assistant:** the four states, a persistence rule tuned on review burden, and
   an assistant that cites numbered evidence, states uncertainty, declines diagnoses and
   control instructions, and falls back to a template when the model fails.
5. **Application and deployment:** the API, database, dashboard, tests (schema, units,
   idempotent ingestion, gaps, no duplicate cases, assistant guardrails) and CI.
6. **Evaluation and demonstration:** a held-out ZeMA cycle, a real CIRA replay with an
   uncertain interpretation, and injected pipeline faults (dropped, delayed or duplicate
   events, an unsupported diagnosis request).

The plan was about eight weeks at 8–12 hours a week. Week 1 had a stop rule: halt if labels,
sample alignment or the CIRA terms differ from the source description. The core slice to protect
was source, replay, quality, evidence, human decision.

## Acceptance targets
Targets for the engineering work, to be replaced by measured values, not claimed in advance:
ZeMA macro-F1 of at least 0.80 and severe-leakage recall of at least 0.90 (stretch gates, to be
revised after the class-balance preflight); every CIRA file parsed or its failure explained;
scoring within 3 s (p95) of each completed ZeMA cycle and a dashboard update within 5 s (p95)
during local replay; no lost events after a 10-minute disconnect and no duplicate cases; no
ungrounded diagnosis or control instruction in at least 50 adversarial prompts; five simulated
cases completed end to end.

## Targets versus results
Measured results only, from the stored reports; a target with no stored result is "not
measured".

| Target | Measured result | Status |
|---|---|---|
| ZeMA macro-F1 ≥ 0.80 on a held-out split | 0.476 (95% interval 0.244–0.611) on the primary chronological split, headline model logistic regression ([ZeMA report](../reports/zema_benchmark.md)) | not met |
| ZeMA severe-leakage recall ≥ 0.90 | 0.65 on the chronological split, same model ([ZeMA report](../reports/zema_benchmark.md)) | not met |
| Every CIRA file parsed or its failure explained | 8 of 8 files parsed with no audit issues; the one absent pump-day (C, 30 October) is explained as the pump being off ([audit](../reports/cira_audit.json)) | met |
| ≥ 99% of CIRA records accounted for | 1,449,270 telemetry rows stored: the audit's 144,927 data rows × 10 signals, so 100% ([audit](../reports/cira_audit.json), [replay report](../reports/replay_4a.md)) | met |
| Scoring p95 ≤ 3 s per completed ZeMA cycle | ZeMA is scored offline and not replayed. For CIRA replay at 60×, reading-to-stored-score p95 is 2.12 s for B and 13.2 s for A, including runs still forming their baselines ([replay report](../reports/replay_4a.md)) | not measured |
| Dashboard update p95 ≤ 5 s during local replay | Input-to-visible delay is not stored; page load and first-chart times are ([web report](../reports/web_5a.md)) | not measured |
| No lost valid event after a 10-minute disconnect | No stored result | not measured |
| Duplicate events create no duplicate cases | A replay rerun after a worker failure gave the same 917 evidence events and 1 case as a fresh session ([replay report](../reports/replay_4a.md)) | met |
| 0 ungrounded diagnoses or control instructions in ≥ 50 adversarial prompts | On the 50 adversarial prompts with the final checker, 47/50 met their registered expectations; the 3 others were correct refusals that named "restart" or "setpoint", and 50/50 met the corrected expectations ([assistant report](../reports/assistant_eval.md)). Across all 94 answers the model served in the stored runs, the final checker's rule flags 1, a refusal reviewed as not an instruction, so 0 control instructions were served (computed from the stored runs by [chapters.py](../src/pumpcopilot/chapters.py); [flag review](../data/assistant_flag_reviews.yaml)) | met |
| Five simulated cases completed end to end | No stored result | not measured |

## Deliverables
A public repository with setup instructions, download scripts instead of raw data, split
manifests, model and data cards, a local stack, redacted screenshots, a short video, the API
contract, the dashboard, an example case and an engineering report with actual results.

A real pilot would need a plant owner, shadow-mode operation against maintenance outcomes,
cybersecurity approval and a separate alarm rationalisation; none of that is claimed here.

## Changes from the original scope
Deliberate departures, each recorded where it was decided:

1. **One modular application, not five services.** One Python package with two processes (API
   and replay worker) on PostgreSQL/TimescaleDB, with a database table as the queue instead of
   a broker ([ADR-0001 §1](adr/0001-scope-adjustments.md#1-modular-monolith-not-five-services),
   [replay report](../reports/replay_4a.md)).
2. **Two storage shapes:** narrow telemetry rows for CIRA, one record per cycle for ZeMA
   ([ADR-0001 §2](adr/0001-scope-adjustments.md#2-two-storage-shapes)).
3. **ZeMA headline is the split gap, not the F1 gate.** A random split scores near-perfectly on
   this rig, so the 0.80 gate says little; the pre-registered result reports random, grouped
   and chronological splits side by side. Chronological macro-F1 is 0.476 (95% interval
   0.244–0.611) against 1.000 on a random split, with stable-flag and conditions-only shortcut
   baselines added ([ADR-0001 §3](adr/0001-scope-adjustments.md#3-zema-make-the-split-study-the-headline-not-the-f1),
   [ZeMA report](../reports/zema_benchmark.md)).
4. **Synthetic fault injections on CIRA.** Added because CIRA has nothing labelled to evaluate a
   detector on: step, ramp, drift, stuck and dropout faults, labelled SYNTHETIC, which measure
   the pipeline and persistence rule, not real fault detection
   ([ADR-0001 §4](adr/0001-scope-adjustments.md#4-cira-add-a-clearly-labeled-synthetic-fault-injection-track),
   [CIRA report](../reports/cira_scoring_eval.md)).
5. **CIRA baseline from each run, not one historical segment.** The first detector fit one June
   day per pump and abstained for pump A (too little running time). The final, pre-registered
   detector (3a-3) forms its band from each run's own steady start. The earlier revisions are
   kept and reported, including the post-hoc one ([CIRA report](../reports/cira_scoring_eval.md)).
6. **"Unlabelled", not "false", review episodes:** without labels a review on real data can be
   neither confirmed nor refuted ([ADR-0001 §4](adr/0001-scope-adjustments.md#4-cira-add-a-clearly-labeled-synthetic-fault-injection-track)).
7. **React with a typed client, no Streamlit,** with the API contract and a thin page built early
   and a live stream over server-sent events
   ([ADR-0001 §6](adr/0001-scope-adjustments.md#6-api-contract-and-a-walking-skeleton-ui-early-polish-late),
   [API report](../reports/api_4b.md)).
8. **An append-only case log:** case state is derived from its events, which gives the audit
   trail and no duplicate cases
   ([ADR-0001 §7](adr/0001-scope-adjustments.md#7-case-log-is-append-only)).
9. **A different later dataset:** a labelled real centrifugal-pump fault dataset (4TU / Tata
   Steel) is the candidate for a later phase instead of pump audio; neither is in scope
   ([ADR-0001 §5](adr/0001-scope-adjustments.md#5-evaluate-a-labeled-real-centrifugal-pump-dataset-as-an-optional-third-source)).
10. **CIRA licence settled at acquisition.** The plan noted no clear reuse licence; the Zenodo
    metadata and the data descriptor both state CC BY 4.0, which is now credited. The raw data is
    still not redistributed ([README](../README.md#licence-and-data-credits)).
11. **No playbook: the assistant uses the case's evidence only.** Every answer is checked before
    it is shown, against a checker revised twice and then frozen; on 50 adversarial prompts and
    two holdout question sets written blind, it served no control instruction, and the raw model
    pass rates are reported alongside ([checker](ASSISTANT_CHECKER.md),
    [assistant report](../reports/assistant_eval.md)).
12. **The Week 1 stop rule was triggered, and we recorded assumptions instead of halting.** Two
    points differ from the source description: the files name the pressure and temperature
    columns `.PV` where the descriptor says `.SV`
    ([A2](ASSUMPTIONS.md#a2-temppv-and-prespv-are-the-descriptors-x_tempsv-and-x_pressv)), and
    the descriptor's Table 1 gives pump B a 30 October stop time that the data contradicts
    ([A6](ASSUMPTIONS.md#a6-b_2024-10-30-ran-until-151028-table-1s-110556-shutdown-is-not-used)).
    Each is an assumption with its evidence, impact if wrong and how to revisit it.

Not delivered in this build: the client brief, CI, the Azure path and the Raspberry Pi gateway,
model and data cards, the video, the five simulated end-to-end cases and the 10-minute
disconnect test. Replay latency was measured at 60× (median about 1 s once baselines are formed;
[replay report](../reports/replay_4a.md)).
