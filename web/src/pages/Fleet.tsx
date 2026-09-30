import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { call, client, type Schema } from "../api/client";
import { Loading, Provenance, StateBadge, Synthetic, fmtTime } from "../components/common";
import { SignalKey, SignalName, Sparkline } from "../components/Sparkline";
import { STATES, scoreSeries, signalsToDraw } from "../fleet";

type Pump = Schema<"Fleet">["pumps"][number];
type FleetSession = Schema<"FleetSession">;
type Scores = Schema<"Scores">;

const scoresQuery = (asset: string, day: string, session: number) => ({
  queryKey: ["scores", asset, day, session],
  queryFn: () => call(client.GET("/api/assets/{asset_id}/days/{source_day}/scores", {
    params: { path: { asset_id: asset, source_day: day }, query: { session_id: session } },
  })),
});

export function Fleet() {
  const q = useQuery({ queryKey: ["fleet"],
                       queryFn: () => call(client.GET("/api/fleet")) });
  const pumps = q.data?.pumps ?? [];
  // the session behind each pump's state (its latest real one), and its latest synthetic
  // session: both as the API reports them, states computed on the server
  const realOf = (p: Pump) => p.sessions.find((x) => x.session.session_id === p.state_session_id);
  const synOf = (p: Pump) => p.sessions.find((x) => x.session.synthetic);
  const scoresFor = (p: Pump, x?: FleetSession) => ({
    ...scoresQuery(p.asset_id, x?.session.source_day ?? "", x?.session.session_id ?? -1),
    enabled: !!x,
  });
  const pumpScores = useQueries({ queries: pumps.map((p) => scoresFor(p, realOf(p))) });
  const synScores = useQueries({ queries: pumps.map((p) => scoresFor(p, synOf(p))) });
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
        </div>
        <StateLegend />
      </header>
      <Loading q={q}>
        <div className="tiles">
          <Tile label="pumps" value={pumps.length} tone="a" />
          <Tile label="pumps with review suggested" value={review} tone="b" />
          <Tile label="open cases, real" value={openReal} tone="c" />
          <Tile label="open cases, synthetic" value={openSyn} tone="d" synthetic />
        </div>
        <div className="cards">
          {pumps.map((p, i) => {
            const syn = synOf(p);
            return [
              <PumpCard key={p.asset_id} pump={p} session={realOf(p)}
                        scores={pumpScores[i]?.data} />,
              // each pump's synthetic session right after it, so the two can be compared
              ...(syn ? [<SyntheticCard key={`syn-${syn.session.session_id}`} pump={p}
                                        fs={syn} scores={synScores[i]?.data} />] : []),
            ];
          })}
        </div>
      </Loading>
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

function SignalStates({ states }: { states: Record<string, string> }) {
  const flagged = Object.entries(states).filter(([, s]) => s !== "normal")
    .sort(([a], [b]) => a.localeCompare(b));
  const normal = Object.values(states).filter((s) => s === "normal").length;
  return (
    <ul className="signal-states">
      {flagged.map(([sig, st]) => (
        <li key={sig}><StateBadge state={st} /><SignalName name={sig} /></li>
      ))}
      <li className="muted">{normal} of {Object.keys(states).length} signals normal</li>
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
  return (
    <article className="card" data-testid={`pump-${p.asset_id}`}>
      <div className="card-accent" aria-hidden="true" />
      <header className="card-head">
        <div>
          <h2 className="mono">{p.asset_id}</h2>
          <p className="muted"><AsOf state={p.state} /></p>
        </div>
        <StateBadge state={p.state.state} size="lg" />
      </header>
      <Spark scores={scores} states={p.state.signals} what={p.asset_id}
             none={s ? undefined : "no real replay session: no scores to draw"} />
      <SignalStates states={p.state.signals} />
      <dl className="facts">
        <div><dt>open cases</dt>
          <dd><Link to={`/cases?asset_id=${p.asset_id}`}>
            <span className="num">{fs?.open_cases ?? 0}</span> in this session</Link>
            <span className="muted"> · all sessions <span className="num">
              {p.open_cases.real}</span> real, <span className="num">
              {p.open_cases.synthetic}</span> synthetic</span></dd></div>
        <div><dt>latest replay</dt>
          <dd>{s ? <>#{s.session_id} {s.source_day} · {s.status} at {s.speed}x · cursor{" "}
            <span className="num">{fmtTime(s.cursor_at)}</span> <Synthetic show={s.synthetic} />
          </> : "none"}</dd></div>
        <div><dt>data quality</dt>
          <dd>{p.data_quality.status} ({p.data_quality.source_day}) · stale{" "}
            <span className="num">{p.data_quality.flag_counts.stale_suspected}</span> · spike{" "}
            <span className="num">{p.data_quality.flag_counts.spike_suspected}</span> · gaps{" "}
            <span className="num">{p.data_quality.gaps ?? "-"}</span></dd></div>
        <div><dt>days</dt>
          <dd className="days">{p.days.map((d) => (
            <Link key={d} to={`/assets/${p.asset_id}/${d}`} className="chip">{d}</Link>))}</dd>
        </div>
      </dl>
      <footer className="provenance-strip">
        <Synthetic show={p.synthetic} />
        <Provenance model_version={p.model_version} assumptions={p.assumptions} />
      </footer>
    </article>
  );
}

/** A real synthetic replay session (an injected fault on stored data), shown so that the
 * SYNTHETIC treatment is judged on real data. */
function SyntheticCard({ pump: p, fs, scores }:
                       { pump: Pump; fs: FleetSession; scores?: Scores }) {
  const s = fs.session;
  const states = fs.state.signals;
  return (
    <article className="card card-synthetic" data-testid={`synthetic-session-${s.session_id}`}>
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
      <Spark scores={scores} states={states} what={`${p.asset_id} (synthetic)`} />
      <SignalStates states={states} />
      <dl className="facts">
        <div><dt>open cases</dt>
          <dd><Link to={`/cases?session_id=${s.session_id}`}><span className="num">
            {fs.open_cases}</span> in this session</Link></dd></div>
        <div><dt>replay</dt>
          <dd>{s.status} at {s.speed}x · cursor <span className="num">
            {fmtTime(s.cursor_at)}</span></dd></div>
      </dl>
      <footer className="provenance-strip">
        <Synthetic show={fs.synthetic} />
        <Provenance model_version={fs.model_version} assumptions={fs.assumptions} />
      </footer>
    </article>
  );
}
