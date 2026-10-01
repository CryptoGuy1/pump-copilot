import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { call, client } from "../api/client";
import { Card, Loading, useAbout } from "../components/ui";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { usePageTitle } from "../hooks/usePageTitle";
import { usePageProvenance } from "../pageProvenance";

/** Step 5b stage 3: what pump-copilot is and is not, how it is built, where its data comes
 * from (with licences), and the evaluation's headline numbers (from the API). */

const BOXES = [
  { id: "cira", x: 20, y: 20, w: 250, h: 64, t: "CIRA pump telemetry", s: "Zenodo · CC BY 4.0" },
  { id: "zema", x: 630, y: 20, w: 250, h: 64, t: "ZeMA hydraulic test rig", s: "UCI · CC BY 4.0" },
  { id: "ingest", x: 20, y: 130, w: 250, h: 64, t: "Ingest and audit", s: "operating state, quality flags" },
  { id: "db", x: 325, y: 130, w: 250, h: 64, t: "TimescaleDB", s: "readings, segments, scores, cases" },
  { id: "bench", x: 630, y: 130, w: 250, h: 64, t: "Benchmark (offline)", s: "pre-registered, evaluated once" },
  { id: "worker", x: 20, y: 240, w: 250, h: 64, t: "Replay worker", s: "detector 3a-3, cases as of a cursor" },
  { id: "api", x: 325, y: 240, w: 250, h: 64, t: "API (read-only role)", s: "FastAPI, live stream (SSE)" },
  { id: "reports", x: 630, y: 240, w: 250, h: 64, t: "Stored results", s: "reports, evaluations" },
  { id: "web", x: 325, y: 350, w: 250, h: 64, t: "Web app", s: "fleet, replay, cases, evaluation" },
  { id: "assist", x: 630, y: 350, w: 250, h: 64, t: "Assistant", s: "Claude, checked; one host only" },
];
/** The connectors, as right-angled paths in the diagram's coordinates; "both" draws an arrow
 * at each end (the worker reads the database and writes scores and cases back). */
const PATHS: { id: string; points: [number, number][]; both?: boolean }[] = [
  { id: "cira-ingest", points: [[145, 84], [145, 130]] },
  { id: "ingest-db", points: [[270, 162], [325, 162]] },
  { id: "db-worker", points: [[390, 194], [390, 216], [145, 216], [145, 240]], both: true },
  { id: "db-api", points: [[470, 194], [470, 240]] },
  { id: "zema-bench", points: [[755, 84], [755, 130]] },
  { id: "bench-reports", points: [[755, 194], [755, 240]] },
  { id: "reports-api", points: [[630, 272], [575, 272]] },
  { id: "api-web", points: [[450, 304], [450, 350]] },
  { id: "api-assist", points: [[575, 292], [602, 292], [602, 382], [630, 382]] },
];

function Architecture() {
  const W = 900, H = 430;
  const pc = (v: number, of: number) => `${(v / of) * 100}%`;
  return (
    <div className="arch-scroll" role="region" aria-label="Architecture diagram" tabIndex={0}>
      <figure className="architecture" aria-describedby="arch-desc">
        {/* the connectors: an SVG drawing; the boxes are text laid over it */}
        <svg viewBox={`0 0 ${W} ${H}`} className="arch-lines" aria-hidden="true" focusable="false">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
                    markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 10 5 0 10z" className="arch-arrowhead" />
            </marker>
          </defs>
          {PATHS.map((l) => (
            <polyline key={l.id} points={l.points.map((q) => q.join(",")).join(" ")}
                      className="arch-line" fill="none" markerEnd="url(#arrow)"
                      markerStart={l.both ? "url(#arrow)" : undefined} />))}
        </svg>
        {BOXES.map((b) => (
          <div key={b.id} className={`arch-box${b.id === "zema" || b.id === "bench"
            ? " arch-rig" : ""}`} style={{ left: pc(b.x, W), top: pc(b.y, H),
                                            width: pc(b.w, W), height: pc(b.h, H) }}>
            <span className="arch-title">{b.t}</span>
            <span className="arch-sub">{b.s}</span>
          </div>))}
        <figcaption id="arch-desc" className="visually-hidden">How pump-copilot is built: CIRA
          pump telemetry is ingested and audited into TimescaleDB. A replay worker scores it
          with the 3a-3 detector and writes scores and cases, as of a replay cursor. The ZeMA
          test-rig benchmark runs offline into stored results. A read-only API serves both to
          the web app, and passes a case's context to the checked assistant.</figcaption>
      </figure>
    </div>
  );
}

/** The same architecture as a stacked flow, for narrow screens. */
const FLOWS = [["cira", "ingest", "db", "worker", "api", "web"], ["zema", "bench", "reports", "api"],
               ["api", "assist"]];
function ArchitectureFlow() {
  const at = (id: string) => BOXES.find((b) => b.id === id)!;
  return (
    <div className="arch-flow">
      {FLOWS.map((f) => (
        <ol key={f.join("-")} className="arch-steps">
          {f.map((id) => <li key={id} className={`arch-step${id === "zema" || id === "bench"
            ? " arch-rig" : ""}`}><span className="arch-title">{at(id).t}</span>
            <span className="arch-sub">{at(id).s}</span></li>)}
        </ol>))}
    </div>
  );
}

function Highlights() {
  const q = useQuery({ queryKey: ["evaluation-chapters"],
                       queryFn: () => call(client.GET("/api/evaluation/chapters")) });
  const z = q.data?.zema?.splits.find((s) => s.split === "chronological");
  const r2 = q.data?.assistant?.revisions.find((r) => r.revision === "r2");
  return (
    <Loading q={q} lines={3}>
      <ul className="highlights">
        {z && <li><strong>ZeMA, chronological split:</strong> macro-F1{" "}
          <span className="num">{z.headline.macro_f1.toFixed(2)}</span> (95% interval{" "}
          <span className="num">{z.headline.lo?.toFixed(2)}–{z.headline.hi?.toFixed(2)}</span>)
          against <span className="num">{z.majority.macro_f1.toFixed(2)}</span> for always
          guessing; test rig only.</li>}
        {q.data?.cira?.key_finding && <li><strong>CIRA detector:</strong>{" "}
          {q.data.cira.key_finding}</li>}
        {r2 && q.data?.assistant && <li><strong>Assistant:</strong> <span className="num">
          {r2.questions.served_checked} of {r2.questions.total}</span> blind holdout questions
          answered by the checked model;{" "}
          <span className="num">{q.data.assistant.instructions_served}</span> control
          instructions served.</li>}
      </ul>
      <p><Link to="/evaluation">The full evaluation</Link></p>
    </Loading>
  );
}

export function About() {
  usePageTitle("About");
  usePageProvenance("About this project");
  const about = useAbout();
  const wide = useMediaQuery("(min-width: 721px)");
  return (
    <section className="about">
      <header className="masthead panel">
        <div className="masthead-accent" aria-hidden="true" />
        <div>
          <p className="eyebrow">About</p>
          <h1>pump-copilot</h1>
          <p className="lede">A read-only decision-support prototype for pump condition review,
            built on public data.</p>
        </div>
      </header>
      <div className="about-grid">
        <Card as="section" accent={false} title="What it is">
          <ul>
            <li>A replay of real centrifugal-pump telemetry, scored by a pre-registered
              detector that opens review cases.</li>
            <li>A place to review a case: its evidence, its band charts, and an assistant
              whose every answer is checked before it is shown.</li>
            <li>An honest evaluation, with the results that did not go well kept in.</li>
          </ul>
        </Card>
        <Card as="section" accent={false} title="What it is not">
          <ul>
            <li>Not a control system: there are no control actions, and nothing is sent
              anywhere; escalation is an export.</li>
            <li>Not a fault diagnosis: the CIRA data has no fault labels, so a case means
              "look here".</li>
            <li>Not an alarm system, and not validated for any plant.</li>
          </ul>
        </Card>
      </div>
      <Card as="section" accent={false} title="Architecture">
        {wide ? <Architecture /> : <ArchitectureFlow />}
      </Card>
      <Card as="section" accent={false} title="Data sources"
            sub="Raw files are downloaded locally and never redistributed.">
        <Loading q={about} lines={3}>
          <ul className="sources">{about.data?.sources.map((s) => (
            <li key={s.id}>
              <p><strong>{s.title}.</strong> {s.citation}.{" "}
                <a href={s.landing_page} target="_blank" rel="noreferrer">{s.landing_page}</a></p>
              <p className="muted">Licence: {s.license_url
                ? <a href={s.license_url} target="_blank" rel="noreferrer">{s.license}</a>
                : s.license}
                {s.license === "CC BY 4.0" && <> (Creative Commons Attribution 4.0
                  International), used with attribution to the authors above</>}.
                {s.changes && <> Changes: {s.changes}.</>} Role: {s.role}.</p>
            </li>))}</ul>
        </Loading>
      </Card>
      <Card as="section" accent={false} title="Evaluation highlights"><Highlights /></Card>
      <div className="about-grid">
        <Card as="section" accent={false} title="Tech stack">
          <Loading q={about} lines={4}>
            <dl className="facts">{about.data?.stack.map((x) => (
              <div key={x.layer}><dt>{x.layer}</dt><dd>{x.what}</dd></div>))}</dl>
          </Loading>
        </Card>
        <Card as="section" accent={false} title="Author">
          <Loading q={about} lines={2}>
            {about.data && <>
              <p className="author-name">{about.data.author.name}</p>
              {about.data.author.role && <p className="muted">{about.data.author.role}</p>}
              {about.data.author.links.length > 0 && <ul className="profile-links">
                {about.data.author.links.map((l) => <li key={l.url}>
                  <a href={l.url} target="_blank" rel="noreferrer">{l.label}</a></li>)}</ul>}
            </>}
          </Loading>
        </Card>
        <Card as="section" accent={false} title="Credits">
          <Loading q={about} lines={3}>
            {about.data?.repository && <p>Repository: <a href={about.data.repository}
              target="_blank" rel="noreferrer">{about.data.repository}</a></p>}
            <p>{about.data?.ai_assistance}</p>
          </Loading>
        </Card>
      </div>
    </section>
  );
}
