import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError, call, client, type Schema } from "../api/client";
import { fmtTime } from "../components/common";
import { SignalKey, Sparkline } from "../components/Sparkline";
import { Button, Card, EmptyState, ErrorPanel, ProvenanceLine, STATES, SignalName, Skeleton,
         StateBadge, StateIcon, StateMark, Synthetic, Table, ValueReadout } from "../components/ui";
import { scoreSeries, signalsToDraw } from "../fleet";
import { useArrivals } from "../hooks/useArrivals";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { usePageProvenance } from "../pageProvenance";

type Pump = Schema<"Fleet">["pumps"][number];
type FleetSession = Schema<"FleetSession">;
type Scores = Schema<"Scores">;

const scoresQuery = (asset: string, x?: FleetSession) => ({
  queryKey: ["scores", asset, x?.session.source_day ?? "", x?.session.session_id ?? -1],
  queryFn: () => call(client.GET("/api/assets/{asset_id}/days/{source_day}/scores", {
    params: { path: { asset_id: asset, source_day: x!.session.source_day },
              query: { session_id: x!.session.session_id } },
  })),
  enabled: !!x && Object.keys(x.state.signals).length > 0,
});
const byNumber = (a: string, b: string) => Number(a.slice(1)) - Number(b.slice(1));
const uniq = (xs: string[]) => [...new Set(xs)];

export function Fleet() {
  const q = useQuery({ queryKey: ["fleet"], queryFn: () => call(client.GET("/api/fleet")) });
  const pumps = q.data?.pumps ?? [];
  // the session behind each pump's own state: its latest real one (computed on the server)
  const realOf = (p: Pump) => p.sessions.find((x) => x.session.session_id === p.state_session_id);
  const pumpScores = useQueries({ queries: pumps.map((p) => scoresQuery(p.asset_id, realOf(p))) });
  // every synthetic session, newest first, in its own section below the real fleet
  const synthetic = pumps.flatMap((p) => p.sessions.filter((x) => x.session.synthetic)
    .map((fs) => ({ p, fs }))).sort((a, b) => b.fs.session.session_id - a.fs.session.session_id);
  const synScores = useQueries({ queries: synthetic.map(({ p, fs }) =>
    scoresQuery(p.asset_id, fs)) });
  const shown = [...pumps.map(realOf).filter(Boolean) as FleetSession[],
                 ...synthetic.map((x) => x.fs)];
  usePageProvenance(q.data && {
    synthetic: synthetic.length ? "mixed" : false, scenarios: synthetic.length,
    model_version: uniq(shown.flatMap((x) => x.model_version)).sort(),
    assumptions: uniq([...pumps.flatMap((p) => p.assumptions),
                       ...shown.flatMap((x) => x.assumptions)]).sort(byNumber) });
  const openReal = pumps.reduce((n, p) => n + p.open_cases.real, 0);
  const openSyn = pumps.reduce((n, p) => n + p.open_cases.synthetic, 0);
  const review = pumps.filter((p) => p.state.state === "review_suggested").length;

  return (
    <section className="fleet">
      <header className="masthead panel">
        <div className="masthead-accent" aria-hidden="true" />
        <div>
          <p className="eyebrow">CIRA centrifugal pumps · public data · read-only</p>
          <h1>Fleet overview</h1>
          <p className="lede">Each pump's state comes from its latest real replay. Synthetic
            scenarios are shown apart, below, and never stand for a pump.</p>
        </div>
        <StateLegend />
      </header>
      {q.isLoading && <div className="cards cards-3">{[0, 1, 2].map((i) =>
        <Skeleton key={i} card lines={4} label="loading pumps" />)}</div>}
      {q.error != null && <ErrorPanel title="Could not load the fleet" error={q.error}
        action={<Button size="sm" onClick={() => q.refetch()}>Try again</Button>} />}
      {q.data && <>
        <div className="tiles">
          <Tile label="Pumps" value={pumps.length} />
          <Tile label="Pumps with review suggested" value={review} kind="review" />
          <Tile label="Open cases, real" value={openReal} />
          <Tile label="Open cases, synthetic" value={openSyn} kind="synthetic" />
        </div>
        {pumps.length === 0 ? <EmptyState title="No pumps yet">Load the CIRA data
          (pumpcopilot db load cira) to see the fleet.</EmptyState> :
        <div className="fleet-body">
          <section aria-labelledby="real-fleet-title">
            <div className="section-head">
              <h2 id="real-fleet-title" className="section-title">Pumps</h2>
              <p className="muted section-lede">Real data: each pump as of its latest real
                replay.</p>
            </div>
            <div className="cards cards-3" data-testid="real-fleet">
              {pumps.map((p, i) => <PumpCard key={p.asset_id} pump={p} session={realOf(p)}
                                             scores={pumpScores[i]?.data} />)}
            </div>
          </section>
          <OpenCases />
          {synthetic.length > 0 && <section aria-labelledby="synthetic-title"
                                            data-testid="synthetic-scenarios">
            <div className="section-head section-head-synthetic">
              <h2 id="synthetic-title" className="section-title">Synthetic scenarios</h2>
              <p className="muted section-lede">Replays of stored days with an injected fault, to
                test the detector: they are not real events.</p>
            </div>
            <div className="cards cards-3">
              {synthetic.map(({ p, fs }, i) => <SyntheticCard key={fs.session.session_id}
                pump={p} fs={fs} scores={synScores[i]?.data} />)}
            </div>
          </section>}
        </div>}
      </>}
    </section>
  );
}

/** The states and SYNTHETIC, explained; a "What do these mean?" toggle on small screens. */
function StateLegend() {
  const wide = useMediaQuery("(min-width: 721px)");
  const list = (
    <ul>
      {STATES.map((s) => <li key={s}><StateBadge state={s} /></li>)}
      <li><Synthetic show /></li>
    </ul>);
  if (!wide) return (
    <details className="legend legend-toggle panel-inset" data-testid="state-legend">
      <summary>What do these mean?</summary>{list}</details>);
  return (
    <div className="legend panel-inset" role="group" aria-label="How states are shown"
         data-testid="state-legend">
      <p className="eyebrow">How states are shown</p>{list}
    </div>
  );
}

function Tile({ label, value, kind }:
              { label: string; value: number; kind?: "review" | "synthetic" }) {
  return (
    <div className={`tile${kind ? ` tile-${kind}` : ""}`} data-testid={`tile-${kind ?? "plain"}`}>
      {kind === "review" && <StateIcon state="review_suggested" />}
      {kind === "synthetic" && <span className="tile-stripe" aria-hidden="true" />}
      <span className="tile-value num">{value}</span>
      <span className="tile-label">{label}</span>
    </div>
  );
}

/** Open cases, newest first, as a compact list; a case that opens while the page is shown
 * slides in. */
function OpenCases() {
  const query = { status: "open" as const, limit: 8 };
  const q = useQuery({ queryKey: ["cases", query], queryFn: () =>
    call(client.GET("/api/cases", { params: { query } })) });
  const arrived = useArrivals(q.data?.cases.map((c) => c.case_id));
  return (
    <section aria-labelledby="open-cases-title">
      <div className="section-head">
        <h2 id="open-cases-title" className="section-title">Open cases</h2>
        <p className="muted section-lede">Newest first, each as of its replay's cursor; new
          ones appear live.</p>
        <Link to="/cases?status=open" className="section-link">All open cases</Link>
      </div>
      {q.isLoading && <Skeleton lines={4} label="loading cases" />}
      {q.error != null && <ErrorPanel title="Could not load cases" error={q.error} />}
      {q.data && (q.data.cases.length === 0
        ? <EmptyState title="No open cases">A case opens when a replay reaches evidence worth
            review.</EmptyState>
        : <Table label="Open cases">
            <thead><tr><th>Case</th><th>Data</th><th>Signals</th><th>Asset</th>
              <th>Evidence (UTC)</th><th>Status</th></tr></thead>
            <tbody data-testid="open-cases">
              {q.data.cases.map((c) => {
                const [main, ...more] = c.signals ?? [];
                return (
                  <tr key={c.case_id} data-testid={`live-case-${c.case_id}`}
                      className={arrived.has(c.case_id) ? "enter" : undefined}>
                    <td><Link to={`/cases/${c.case_id}`}>#{c.case_id}</Link></td>
                    <td>{c.synthetic ? <Synthetic show /> : "Real"}</td>
                    <td>{main ? <SignalName id={main} /> : "-"}
                      {more.length > 0 && <span className="muted"> +{more.length}</span>}</td>
                    <td className="mono">{c.asset_id}</td>
                    <td className="num">{c.source_day} {fmtTime(c.evidence_start)}–
                      {fmtTime(c.evidence_end)}</td>
                    <td><span className="status-pill">{c.status.charAt(0).toUpperCase()
                      + c.status.slice(1)}</span></td>
                  </tr>);
              })}
            </tbody>
          </Table>)}
    </section>
  );
}

/** Per-signal states as compact rows (icon and label); normal ones are counted. */
function SignalStates({ states }: { states: Record<string, string> }) {
  const flagged = Object.entries(states).filter(([, s]) => s !== "normal")
    .sort(([a], [b]) => a.localeCompare(b));
  const normal = Object.values(states).filter((s) => s === "normal").length;
  return (
    <ul className="signal-states">
      {flagged.map(([sig, st]) => (
        <li key={sig}><StateMark state={st} /><SignalName id={sig} /></li>
      ))}
      <li className="muted">{normal} of {Object.keys(states).length} signals normal</li>
    </ul>
  );
}

function Spark({ scores, states, what }:
               { scores?: Scores; states: Record<string, string>; what: string }) {
  const draw = signalsToDraw(states);
  if (!scores) return <div className="spark-wrap"><p className="spark-empty spark-loading">
    loading…</p></div>;
  return (
    <div className="spark-wrap">
      <Sparkline series={scoreSeries(scores.scores, draw)}
                 label={`${what}: deviation score over time`} />
      <SignalKey signals={draw} />
    </div>
  );
}

/** "as of" the session cursor, or why there is no state yet. */
function AsOf({ state }: { state: Pump["state"] }) {
  return state.as_of
    ? <>as of <span className="num">{fmtTime(state.as_of)}</span> UTC (replay cursor)</>
    : <>{state.reason}</>;
}

/** "ok · 145 stale readings, 0 spikes, 0 gaps" for the day the card is about. */
function DataQuality({ dq }: { dq: Pump["data_quality"] }) {
  const n = (k: number | null | undefined, w: string) =>
    `${k ?? "-"} ${w}${k === 1 ? "" : "s"}`;
  return <>{dq.status} · <span className="num">{n(dq.flag_counts.stale_suspected,
    "stale reading")}, {n(dq.flag_counts.spike_suspected, "spike")}, {n(dq.gaps, "gap")}
    </span> <span className="muted">({dq.source_day})</span></>;
}

/** One message when a card has nothing to show yet: the reason and what would change it. */
function NoScores({ session: fs }: { session?: FleetSession }) {
  const qc = useQueryClient();
  const resume = useMutation({
    mutationFn: (id: number) => call(client.POST("/api/replay/sessions/{session_id}/start",
                                                 { params: { path: { session_id: id } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fleet"] });
                       qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
  const err = resume.error as ApiError | null;
  if (!fs) return (
    <EmptyState title="No real replay yet"
                action={<Link to="/replay">Start a replay</Link>}>
      This pump has stored days but no replay session, so there are no scores.
    </EmptyState>);
  const s = fs.session;
  const why = s.status === "paused" ? `Replay #${s.session_id} is paused`
    : s.status === "pending" ? `Replay #${s.session_id} is waiting for a worker`
    : `Replay #${s.session_id} is ${s.status}`;
  return (
    <EmptyState title={s.cursor_at ? `No scores yet at ${fmtTime(s.cursor_at)} UTC`
                                   : "No scores yet"}
                action={s.status === "paused" ? <Button size="sm" variant="primary"
                  disabled={resume.isPending} onClick={() => resume.mutate(s.session_id)}>
                  Resume replay #{s.session_id}</Button> : undefined}>
      {why}; the first windows are scored once each signal has settled after the run start
      (A7).{err && <> Could not resume: {err.message}</>}
    </EmptyState>);
}

function PumpCard({ pump: p, session: fs, scores }:
                  { pump: Pump; session?: FleetSession; scores?: Scores }) {
  const s = fs?.session;
  const dq = p.data_quality;
  const has = Object.keys(p.state.signals).length > 0;
  return (
    <Card testId={`pump-${p.asset_id}`} title={<span className="mono">{p.asset_id}</span>}
          sub={<AsOf state={p.state} />} aside={<StateBadge state={p.state.state} size="lg" />}
          foot={<ProvenanceLine p={{ synthetic: false, model_version: p.model_version,
                                     assumptions: p.assumptions }} />}>
      {has ? <>
        <Spark scores={scores} states={p.state.signals} what={p.asset_id} />
        <SignalStates states={p.state.signals} />
        <div className="readouts">
          <ValueReadout label="Open cases, this replay" value={fs?.open_cases ?? 0} />
          <ValueReadout label="Open cases, all real replays" value={p.open_cases.real} />
        </div>
      </> : <NoScores session={fs} />}
      <dl className="facts">
        <div><dt>Replay</dt>
          <dd>{s ? <>#{s.session_id} {s.source_day} · {s.status} at {s.speed}x ·{" "}
            <Link to={`/cases?session_id=${s.session_id}`}>its cases</Link></> : "none"}</dd></div>
        <div><dt>Data quality</dt><dd><DataQuality dq={dq} /></dd></div>
        <div><dt>Open a day</dt>
          <dd className="days">{p.days.map((d) => (
            <Link key={d} to={`/assets/${p.asset_id}/${d}`} className="day-link"
                  aria-label={`Open ${p.asset_id} on ${d}`}>{d}</Link>))}</dd>
        </div>
      </dl>
    </Card>
  );
}

/** A real synthetic replay session (an injected fault on stored data). */
function SyntheticCard({ pump: p, fs, scores }:
                       { pump: Pump; fs: FleetSession; scores?: Scores }) {
  const s = fs.session;
  const has = Object.keys(fs.state.signals).length > 0;
  return (
    <Card className="card-synthetic" testId={`synthetic-session-${s.session_id}`}
          accent={false}
          foot={<ProvenanceLine p={{ synthetic: fs.synthetic, model_version: fs.model_version,
                                     assumptions: fs.assumptions }} />}>
      <div className="synthetic-frame" aria-hidden="true" />
      <div className="synthetic-banner">
        <Synthetic show />
        <span>Injected fault <span className="mono">{s.scenario}</span> on stored data: not a
          real event</span>
      </div>
      <header className="card-head">
        <div>
          <h2 className="mono">{p.asset_id}</h2>
          <p className="muted">Replay #{s.session_id} {s.source_day} · <AsOf state={fs.state} />
          </p>
        </div>
        <StateBadge state={fs.state.state} size="lg" />
      </header>
      {has ? <>
        <Spark scores={scores} states={fs.state.signals} what={`${p.asset_id} (synthetic)`} />
        <SignalStates states={fs.state.signals} />
        <div className="readouts">
          <ValueReadout label="Open cases, this replay" value={fs.open_cases} />
        </div>
      </> : <NoScores session={fs} />}
      <p><Link to={`/cases?session_id=${s.session_id}`}>This replay's cases</Link></p>
    </Card>
  );
}
