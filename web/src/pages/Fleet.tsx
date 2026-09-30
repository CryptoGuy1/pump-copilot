import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { call, client, type Schema } from "../api/client";
import { fmtTime } from "../components/common";
import { SignalKey, SignalName, Sparkline } from "../components/Sparkline";
import { Card, EmptyState, ErrorPanel, ProvenanceStrip, STATES, Skeleton, StateBadge, Synthetic,
         ValueReadout } from "../components/ui";
import { scoreSeries, signalsToDraw } from "../fleet";
import { useArrivals } from "../hooks/useArrivals";
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
  enabled: !!x,
});
const byNumber = (a: string, b: string) => Number(a.slice(1)) - Number(b.slice(1));
const uniq = (xs: string[]) => [...new Set(xs)];

export function Fleet() {
  const q = useQuery({ queryKey: ["fleet"], queryFn: () => call(client.GET("/api/fleet")) });
  const pumps = q.data?.pumps ?? [];
  // the session behind each pump's own state (its latest real one) and its latest synthetic
  // session: both as the API reports them, states computed on the server
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
    synthetic: shown.some((x) => x.synthetic) ? "mixed" : false,
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
      {q.isLoading && <div className="cards">{[0, 1, 2].map((i) =>
        <Skeleton key={i} card lines={4} label="loading pumps" />)}</div>}
      {q.error != null && <ErrorPanel title="Could not load the fleet" error={q.error}
        action={<button type="button" className="btn-sm" onClick={() => q.refetch()}>
          Try again</button>} />}
      {q.data && <>
        <div className="tiles">
          <Tile label="pumps" value={pumps.length} tone="a" />
          <Tile label="pumps with review suggested" value={review} tone="b" />
          <Tile label="open cases, real" value={openReal} tone="c" />
          <Tile label="open cases, synthetic" value={openSyn} tone="d" synthetic />
        </div>
        {pumps.length === 0 ? <EmptyState title="No pumps yet">Load the CIRA data
          (pumpcopilot db load cira) to see the fleet.</EmptyState> :
        <div className="fleet-body">
          <div className="fleet-main">
            <section aria-labelledby="real-fleet-title">
              <div className="section-head panel">
                <h2 id="real-fleet-title" className="section-title">Pumps</h2>
                <p className="muted section-lede">Real data: each pump as of its latest real
                  replay.</p>
              </div>
              <div className="cards" data-testid="real-fleet">
                {pumps.map((p, i) => <PumpCard key={p.asset_id} pump={p} session={realOf(p)}
                                               scores={pumpScores[i]?.data} />)}
              </div>
            </section>
            {synthetic.length > 0 && <section aria-labelledby="synthetic-title"
                                              data-testid="synthetic-scenarios">
              <div className="section-head panel section-head-synthetic">
                <h2 id="synthetic-title" className="section-title">Synthetic scenarios</h2>
                <p className="muted section-lede">Replays of stored days with an injected fault, to
                  test the detector: they are not real events.</p>
              </div>
              <div className="cards">
                {synthetic.map(({ p, fs }, i) => <SyntheticCard key={fs.session.session_id}
                  pump={p} fs={fs} scores={synScores[i]?.data} />)}
              </div>
            </section>}
          </div>
          <LiveCases />
        </div>}
      </>}
    </section>
  );
}

function StateLegend() {
  return (
    <div className="legend panel-inset" role="group" aria-label="How states are shown">
      <p className="eyebrow">How states are shown</p>
      <ul>
        {STATES.map((s) => <li key={s}><StateBadge state={s} /></li>)}
        <li><Synthetic show /></li>
      </ul>
    </div>
  );
}

function Tile({ label, value, tone, synthetic }:
              { label: string; value: number; tone: string; synthetic?: boolean }) {
  return (
    <div className={`tile tile-${tone}`}>
      <span className="tile-value num">{value}</span>
      <span className="tile-label">{label}</span>
      {synthetic && value > 0 && <Synthetic show />}
    </div>
  );
}

/** Open cases, newest first; a case that opens while the page is shown slides in. */
function LiveCases() {
  const query = { status: "open" as const, limit: 6 };
  const q = useQuery({ queryKey: ["cases", query], queryFn: () =>
    call(client.GET("/api/cases", { params: { query } })) });
  const arrived = useArrivals(q.data?.cases.map((c) => c.case_id));
  return (
    <aside className="live-cases panel" aria-labelledby="live-cases-title">
      <h2 id="live-cases-title">Open cases</h2>
      <p className="muted">Newest first, each as of its replay's cursor; new ones appear
        live.</p>
      {q.isLoading && <Skeleton lines={4} label="loading cases" />}
      {q.error != null && <ErrorPanel title="Could not load cases" error={q.error} />}
      {q.data && (q.data.cases.length === 0
        ? <EmptyState title="No open cases">A case opens when a replay reaches evidence worth
            review.</EmptyState>
        : <ul className="case-list" data-testid="live-cases">
            {q.data.cases.map((c) => (
              <li key={c.case_id} className={`case-item${arrived.has(c.case_id) ? " enter" : ""}`}
                  data-testid={`live-case-${c.case_id}`}>
                <div className="row">
                  <Link to={`/cases/${c.case_id}`}>Case #{c.case_id}</Link>
                  <Synthetic show={c.synthetic} />
                </div>
                <div className="row muted">
                  <span className="mono">{c.asset_id}</span>
                  <span>{c.source_day} · evidence {fmtTime(c.evidence_start)}–
                    {fmtTime(c.evidence_end)}</span>
                </div>
                <div className="row muted">{(c.signals ?? []).map((s) =>
                  <SignalName key={s} name={s} />)}</div>
              </li>))}
          </ul>)}
      <Link to="/cases?status=open">All open cases</Link>
    </aside>
  );
}

function SignalStates({ states }: { states: Record<string, string> }) {
  const flagged = Object.entries(states).filter(([, s]) => s !== "normal")
    .sort(([a], [b]) => a.localeCompare(b));
  const normal = Object.values(states).filter((s) => s === "normal").length;
  return (
    <ul className="signal-states">
      {flagged.map(([sig, st]) => (
        <li key={sig}><StateBadge state={st} /><SignalName name={sig} /></li>
      ))}
      <li className="muted">{Object.keys(states).length
        ? `${normal} of ${Object.keys(states).length} signals normal`
        : "no signal states yet at this cursor"}</li>
    </ul>
  );
}

function Spark({ scores, states, what, none }:
               { scores?: Scores; states: Record<string, string>; what: string;
                 none?: string }) {
  const draw = signalsToDraw(states);
  if (none) return <div className="spark-wrap"><p className="spark-empty">{none}</p></div>;
  if (!scores) return <div className="spark-wrap"><p className="spark-empty spark-loading">
    loading…</p></div>;
  if (!draw.length) return <div className="spark-wrap"><p className="spark-empty">no scores
    at this cursor yet</p></div>;
  return (
    <div className="spark-wrap">
      <Sparkline series={scoreSeries(scores.scores, draw)}
                 label={`${what}: deviation score over time for ${draw.join(", ")}`} />
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

function PumpCard({ pump: p, session: fs, scores }:
                  { pump: Pump; session?: FleetSession; scores?: Scores }) {
  const s = fs?.session;
  const dq = p.data_quality;
  return (
    <Card testId={`pump-${p.asset_id}`} title={<span className="mono">{p.asset_id}</span>}
          sub={<AsOf state={p.state} />} aside={<StateBadge state={p.state.state} size="lg" />}
          foot={<ProvenanceStrip p={{ synthetic: false, model_version: p.model_version,
                                      assumptions: p.assumptions }} />}>
      <Spark scores={scores} states={p.state.signals} what={p.asset_id}
             none={s ? undefined : "no real replay session: no scores to draw"} />
      <SignalStates states={p.state.signals} />
      <div className="readouts">
        <ValueReadout label="open, this replay" value={fs?.open_cases ?? 0} />
        <ValueReadout label="open, all real" value={p.open_cases.real} />
        <ValueReadout label="stale" value={dq.flag_counts.stale_suspected} size="sm" />
        <ValueReadout label="spikes" value={dq.flag_counts.spike_suspected} size="sm" />
        <ValueReadout label="gaps" value={dq.gaps ?? "-"} size="sm" />
      </div>
      <dl className="facts">
        <div><dt>replay</dt>
          <dd>{s ? <>#{s.session_id} {s.source_day} · {s.status} at {s.speed}x ·{" "}
            <Link to={`/cases?session_id=${s.session_id}`}>its cases</Link></> : "none"}</dd></div>
        <div><dt>data quality</dt><dd>{dq.status} ({dq.source_day})</dd></div>
        <div><dt>days</dt>
          <dd className="days">{p.days.map((d) => (
            <Link key={d} to={`/assets/${p.asset_id}/${d}`} className="chip">{d}</Link>))}</dd>
        </div>
      </dl>
    </Card>
  );
}

/** A real synthetic replay session (an injected fault on stored data). */
function SyntheticCard({ pump: p, fs, scores }:
                       { pump: Pump; fs: FleetSession; scores?: Scores }) {
  const s = fs.session;
  return (
    <Card className="card-synthetic" testId={`synthetic-session-${s.session_id}`}
          accent={false}
          foot={<ProvenanceStrip p={{ synthetic: fs.synthetic, model_version: fs.model_version,
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
          <p className="muted">replay #{s.session_id} {s.source_day} · <AsOf state={fs.state} />
          </p>
        </div>
        <StateBadge state={fs.state.state} size="lg" />
      </header>
      <Spark scores={scores} states={fs.state.signals} what={`${p.asset_id} (synthetic)`} />
      <SignalStates states={fs.state.signals} />
      <div className="readouts">
        <ValueReadout label="open, this replay" value={fs.open_cases} />
        <ValueReadout label="replay" value={s.status} size="sm" />
      </div>
      <p><Link to={`/cases?session_id=${s.session_id}`}>This replay's cases</Link></p>
    </Card>
  );
}
