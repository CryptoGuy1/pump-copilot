# pump-copilot

[![CI](https://github.com/CryptoGuy1/pump-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/CryptoGuy1/pump-copilot/actions/workflows/ci.yml)

**[Live demo: a static snapshot](https://cryptoguy1.github.io/pump-copilot/)** (read-only, nothing
is live; [how it is built](#static-snapshot-github-pages)).

A read-only decision-support prototype for pump condition review, built on public data. It
replays real centrifugal-pump telemetry, scores it against each pump's own baseline, opens review
cases with their evidence, and lets an engineer ask a checked assistant about a case. It is
deliberately **not** a control system, an alarm system or a fault diagnosis: there are no control
actions and no alarm states, escalation is an export, and a case means "look here", never
"fault". The scope and its claim boundaries are in [docs/SCOPE.md](docs/SCOPE.md).

## Screenshots

| | |
|---|---|
| ![Fleet overview: three pumps with their states, open cases and a synthetic scenario shown apart](docs/images/fleet.png) | ![Asset day: scored signals against their baseline bands, with review windows and the operating state](docs/images/asset-day.png) |
| **Fleet.** Each pump's state as of its latest real replay; synthetic scenarios are kept apart. | **Asset day.** Every signal against its baseline band, with the review windows and the operating state. |
| ![Case detail: the evidence timeline, band charts, and a checked assistant answer with evidence references](docs/images/case-assistant.png) | ![Evaluation: macro-F1 by split with intervals, the confusion matrix and the stable-flag shortcut](docs/images/evaluation.png) |
| **Case.** The evidence timeline, and a recorded assistant answer that passed the checker, citing evidence IDs. | **Evaluation.** Every number comes from the stored results; each chapter says how to read it. |

Screenshots of the static snapshot ([reports/design/snapshot/](reports/design/snapshot/), made by
[web/snapshot-e2e/](web/snapshot-e2e/)).

## Key results

<!-- key results: written by `pumpcopilot readme` from the stored results; do not edit by hand -->
- **ZeMA hydraulic test rig.** Same model, same data: 1.000 on a random split, 0.476 on a chronological one (95% interval 0.244–0.611; always guessing scores 0.187). Test rig only: it says nothing about the CIRA pumps. [Evaluation](https://cryptoguy1.github.io/pump-copilot/evaluation) · [report](reports/zema_benchmark.md)
- **CIRA detector.** On real October data the pre-registered 3a-3 detector keeps 90.5% of pump B's and 55.9% of pump A's running time inside a review case, so a case means "look here", not "fault"; on synthetic injections, dropout and stuck faults are always detected, but a 6-sigma step only in 7 of 15. [Evaluation](https://cryptoguy1.github.io/pump-copilot/evaluation) · [report](reports/cira_scoring_eval.md)
- **Assistant.** On questions written blind before each run, the checked model's answer was served for 8 of 20 (checker revision 1) and 13 of 20 (checker revision 2); the others fell back to the evidence summary. On 50 adversarial prompts with the final checker, 47/50 final answers met their registered expectations (50/50 under the corrected expectations). **0 control instructions served** in the 94 answers the model served. [Evaluation](https://cryptoguy1.github.io/pump-copilot/evaluation) · [report](reports/assistant_eval.md)
<!-- /key results -->

Detail and limitations: model cards for the [CIRA detector](docs/model_card_cira_detector.md)
and [ZeMA](docs/model_card_zema.md), and data cards for [CIRA](docs/data_card_cira.md) and
[ZeMA](docs/data_card_zema.md).

## How it works

```mermaid
flowchart LR
  cira[CIRA pump telemetry<br/>Zenodo, CC BY 4.0] --> ingest[Ingest and audit<br/>operating state, quality flags]
  ingest --> db[(TimescaleDB<br/>readings, scores, cases)]
  db <--> worker[Replay worker<br/>detector 3a-3, cases as of a cursor]
  db --> api[API, read-only role<br/>FastAPI, live stream]
  zema[ZeMA test rig<br/>UCI, CC BY 4.0] --> bench[Benchmark, offline<br/>pre-registered] --> results[Stored results<br/>reports/]
  results --> api
  api --> web[Web app]
  api --> assistant[Assistant<br/>Claude, checked]
```

1. **Acquire** ([acquire.py](src/pumpcopilot/acquire.py)): downloads the datasets named in
   [data/manifest.yaml](data/manifest.yaml) and checks them against the sources' checksums.
2. **Audit** ([cira.py](src/pumpcopilot/cira.py), [zema.py](src/pumpcopilot/zema.py)): cadence,
   gaps, duplicates, placeholders and label structure, before anything is modelled.
3. **Operating state** ([operating.py](src/pumpcopilot/operating.py)): running, off or unknown by
   stated rules, plus stale and spike flags that never change a value.
4. **Scoring** ([scoring.py](src/pumpcopilot/scoring.py)): per-signal robust bands from each run's
   steady start; a review needs consecutive windows outside the band, and abstaining needs a
   reason.
5. **Replay** ([replay.py](src/pumpcopilot/replay.py)): a worker replays a stored day at 1×, 10×
   or 60× and writes scores as the cursor passes them, identical to batch scoring.
6. **Cases** ([cases.py](src/pumpcopilot/cases.py)): review episodes become cases in an
   append-only log (acknowledge, note, disposition, close, export).
7. **API** ([api.py](src/pumpcopilot/api.py)): a read-only database role, a response model for
   every endpoint ([api/openapi.json](api/openapi.json)), and a live stream that carries signals
   only.
8. **Frontend** ([web/](web/)): React and TypeScript on a client generated from the API contract;
   three themes, checked with axe.
9. **Assistant** ([assistant.py](src/pumpcopilot/assistant.py)): answers from the case's evidence
   only; an answer is shown only if it passes the [checker](docs/ASSISTANT_CHECKER.md), otherwise
   the evidence summary is shown.

## How it was evaluated

- **Pre-registered, evaluated once.** The CIRA detector's final revision is tagged `prereg-3a3`
  and the ZeMA benchmark `prereg-3b-r2` (it supersedes `prereg-3b`, whose test parts were never
  opened). The evaluation code refuses to run if the code or configuration changed since the tag.
- **Held out and blind.** ZeMA's test parts were scored once. The assistant's
  [holdout](data/assistant_benign_holdout.yaml) and
  [second holdout](data/assistant_benign_holdout2.yaml) questions were written and committed
  before each run, and an [adversarial set](data/assistant_adversarial.yaml) tests what it must
  refuse.
- **What did not work is kept.** The post-hoc detector revision and the results that missed their
  targets are reported, not replaced.
- **Assumptions are written down.** Each assumption about the data, with its evidence and its
  impact if wrong, is in the [assumptions register](docs/ASSUMPTIONS.md).
- **Targets against results.** [docs/SCOPE.md](docs/SCOPE.md#targets-versus-results) compares each
  original target with the measured result: met, not met or not measured.

## Run it yourself

Needs Python 3.11+, Docker, and Node 20 for the web app.

**Set up and test**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d db         # TimescaleDB; password from POSTGRES_PASSWORD (default pump_dev_only)
pytest                          # the database tests make their own test databases
```

**Data and database**
```bash
pumpcopilot acquire             # downloads CIRA (Zenodo) and ZeMA (UCI) into data/raw/, never committed
pumpcopilot audit cira          # -> reports/cira_audit.json
pumpcopilot db migrate          # applies migrations/NNNN_*.sql in order
pumpcopilot db load cira        # idempotent: a second run inserts nothing
```

**Run the app**
```bash
pumpcopilot replay create cira-pump-B 2024-10-30 --speed 60 [--scenario B_stuck_pressure]
make dev                        # database, worker, API and web UI at http://127.0.0.1:5173
cp .env.example .env            # optional: ANTHROPIC_API_KEY for the assistant (gitignored);
                                # without a key the assistant shows the evidence summary
```

**Rebuild the reports from the stored results** (nothing is re-evaluated)
```bash
pumpcopilot score report        # reports/cira_scoring_eval.md
pumpcopilot assistant report    # reports/assistant_eval.md
pumpcopilot readme --check      # the key results above match the stored results
pumpcopilot api --export-openapi && make gen-api   # the API contract and the typed web client
```

<details><summary>All commands</summary>

```bash
pumpcopilot audit zema          # -> reports/zema_audit.json
pumpcopilot rules cira          # -> data/operating_rules.yaml, reports/cira_operating_rules.md
pumpcopilot state cira          # -> reports/cira_state.json (state time, changes, stale/spike)
pumpcopilot db perf             # -> reports/db_perf.json
pumpcopilot db storage          # bytes per hypertable, compression state
pumpcopilot db reset --yes-i-mean-it  # drop, recreate and migrate; refuses non-local URLs
pumpcopilot worker --until-idle # claims sessions (FOR UPDATE SKIP LOCKED), scores with 3a-3
pumpcopilot replay pause|resume|rewind|latency|verify <id>
pumpcopilot case list|show|ack|note|dispose|close|export [<id>] [--by NAME]
pumpcopilot api [--port 8000]   # HTTP API and live stream on 127.0.0.1 only; docs at /docs
make test-web                   # Vitest and the TypeScript check
make e2e                        # Playwright end to end, on its own database (pumpcopilot_e2e)
```

The pre-registered evaluations were run once, at their tags. They refuse to run if the code or
configuration has changed since the tag (as it has), and running them again would re-score the
held-out data:
```bash
pumpcopilot zema features && pumpcopilot zema eval --prereg prereg-3b-r2
pumpcopilot score eval-3a3 --prereg prereg-3a3
```
</details>

The database password comes from `POSTGRES_PASSWORD` (export it in the shell, so the database,
the worker and the API all see it; it applies when the database volume is first created), or
`DATABASE_URL` replaces the whole connection string. Without either, the local development
default `pump_dev_only` is used.

## Static snapshot (GitHub Pages)

The live demo is the app's static build (`cd web && npm run build:snapshot`). It reads recorded
API responses from [web/public/snapshot/](web/public/snapshot/) instead of the API: nothing in it
is live, no stream is opened, and every action is disabled. To rebuild it:
```bash
./scripts/snapshot_source.sh   # reset the local pumpcopilot_snapshot database, load, replay 3 sessions
export DATABASE_URL=postgresql://pump:pump_dev_only@localhost:5432/pumpcopilot_snapshot
pumpcopilot snapshot record    # once: 6 real model requests, hard cap 6 (reports/snapshot_answers.json)
pumpcopilot snapshot export    # the files and web/public/snapshot/manifest.json
```
[.github/workflows/pages.yml](.github/workflows/pages.yml) builds and deploys it on every push to
master (or by hand); it needs no secrets.

## Repository layout

```
src/pumpcopilot/   the pipeline, API, assistant and report generators (one package)
web/               the React app, its tests, and the static snapshot build
migrations/        numbered SQL for TimescaleDB
data/              manifest, column map, frozen configurations, question sets (raw data: local only)
reports/           every stored result and report, and the design checks (reports/design/)
docs/              scope, assumptions register, ADRs, model and data cards, client brief
tests/             pytest, including the database tests
```

## Licence and data credits

The code is licensed under the [MIT License](LICENSE). It does not cover the datasets.

This project uses two public datasets, both under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Full citations are in
[NOTICE](NOTICE) and [data/manifest.yaml](data/manifest.yaml).
- **CIRA Centrifugal Pump Dataset**: Martone and Zazzaro, Zenodo,
  [doi:10.5281/zenodo.18479728](https://doi.org/10.5281/zenodo.18479728). The data descriptor is
  Martone et al. (2025), Data 10(6), 91.
- **ZeMA condition monitoring of hydraulic systems**: Helwig, Pignanelli and Schütze, UCI Machine
  Learning Repository, [doi:10.24432/C5CW21](https://doi.org/10.24432/C5CW21).

The raw data is not redistributed. It is not in this repository, and `pumpcopilot acquire`
downloads it from the original sources. The raw data is unchanged; the features, scores, reports
and figures here are derived from it.

## Built with AI assistance

Built by Benjamin Nweke with Claude as a coding assistant. The project owner set the scope, made
every design and evaluation decision, reviewed each result, and confirmed the judgement calls
recorded in the repository.

## Author

**Benjamin Nweke**
