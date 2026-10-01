# Client brief (fictional)

> **Fictional.** This brief is a simulation written for the portfolio project. The client, site
> and people are invented; no refinery supplied data, requirements or approval. It frames the
> demonstration, which runs on public data ([scope](SCOPE.md)).

**For:** the reliability manager of a fictional refinery's utilities and offsites area.

**Service:** one centrifugal pump service (which one is an open question), plus a small fleet
view of similar pumps.

## The problem
Pump condition is spread across the historian, inspection notes and work orders. A change in
vibration, temperature or pressure can mean a developing problem. It can just as well mean a
load change, a start or stop, a sensor fault or a different operating point. Operators see
trends but not the evidence behind them. Engineers find out late, and the handoff from operator
to engineer to maintenance is an informal conversation rather than a record.

## Decisions the tool supports
It supports these decisions; it makes none of them:
1. **Operator:** is this pump worth flagging for review now, and is the data behind it good enough
   to say so?
2. **Reliability engineer:** what changed, against which baseline and in which operating state,
   and what should be checked next (sensor, operating point, recent maintenance)?
3. **Engineer:** close the review as no action, keep watching, or escalate as a draft maintenance
   request, with the evidence attached.

It does not decide whether to stop, start or switch a pump. It raises no alarm and writes nothing
back to the plant.

## What success looks like
- Every suggested review shows its evidence: the signals, the window, the baseline, the
  operating state, the data quality and the model version.
- The review workload is acceptable to the engineers (the number of reviews per pump per week is
  to be agreed; see below).
- No duplicate reviews for one episode, and a full audit trail of who acknowledged, noted and
  decided what, and when.
- The assistant never gives a diagnosis or a control instruction the evidence does not support.
- An engineer who did not see the trend can follow a closed review from its record alone.

## Open questions
Recorded as open; a real client would answer them, and none is assumed here:
- Which pump service, and which failure modes matter most (seal, bearing, cavitation,
  instrumentation)?
- What review workload is acceptable, and what counts as an actionable observation?
- Which historian tags exist, at what sampling rate, and how good is their data quality?
- How is the operating state known: run status signals, a setpoint, flow?
- Which maintenance history and work-order outcomes could be linked to past episodes?
- How should a case move from operator to engineer to maintenance, and who closes it?
- What data access, network zoning, identity and cybersecurity approval would a pilot need?
- Which alarm philosophy applies, so that the advisory never competes with plant alarms?
- What would a shadow-mode pilot need to show before anyone relied on it?
