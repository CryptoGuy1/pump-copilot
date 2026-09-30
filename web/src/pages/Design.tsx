import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import { call, client } from "../api/client";
import { fmtTime } from "../components/common";
import { AssumptionChip, Button, Card, EmptyState, ErrorPanel, Loading, ProvenanceStrip, STATES,
         SelectField, Skeleton, StateBadge, Synthetic, Table, Tabs, TextAreaField, TextField,
         ValueReadout, useAssumptions } from "../components/ui";
import { usePageProvenance } from "../pageProvenance";
import { THEMES } from "../theme";

/** /design: every component of the design system, in the current theme, and the core ones
 * in all three side by side. Components that show data show real data from the API; the
 * rest (buttons, fields, the error example) are labelled as examples. */

function Specimen({ title, note, children }: { title: string; note?: ReactNode;
                                                 children: ReactNode }) {
  const id = `sp-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section className="panel specimen" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {note && <p className="note">{note}</p>}
      {children}
    </section>
  );
}

const SWATCHES: [string, string[]][] = [
  ["Expressive", ["--brand", "--tile-a", "--tile-b", "--tile-c", "--tile-d", "--sig-1",
                  "--sig-2", "--sig-3"]],
  ["Meaning (states only)", ["--st-review", "--st-insuf", "--st-unav", "--syn"]],
  ["Surfaces and text", ["--bg", "--surface", "--surface-2", "--border-strong", "--text",
                         "--muted"]],
];

function Swatches() {
  return (
    <div className="specimen-row" style={{ alignItems: "flex-start" }}>
      {SWATCHES.map(([group, tokens]) => (
        <div key={group} className="field">
          <span className="field-label">{group}</span>
          <ul className="chips" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {tokens.map((t) => (
              <li key={t} className="chip" style={{ color: "var(--text)", gap: "var(--space-2)" }}>
                <span aria-hidden="true" style={{ width: "var(--space-5)", height: "var(--space-5)",
                  background: `var(${t})`, border: "var(--border-1) solid var(--border-strong)",
                  borderRadius: "var(--radius-sm)" }} />{t}</li>))}
          </ul>
        </div>))}
    </div>
  );
}

function Core() {
  return (
    <div className="specimen-row">
      {STATES.map((s) => <StateBadge key={s} state={s} />)}
      <Synthetic show />
      <Button variant="primary">Primary</Button>
      <Button>Secondary</Button>
      <Button variant="ghost">Ghost</Button>
    </div>
  );
}

function AllThemes() {
  const fleet = useQuery({ queryKey: ["fleet"], queryFn: () => call(client.GET("/api/fleet")) });
  const pump = fleet.data?.pumps.find((p) => p.state_session_id != null);
  const real = pump?.sessions.find((x) => x.session.session_id === pump.state_session_id);
  return (
    <div className="theme-compare">
      {THEMES.map((t) => (
        <div key={t} data-theme={t} className="theme-scope" aria-label={`${t} theme`}
             role="group">
          <p className="eyebrow">{t}</p>
          <Core />
          {pump && real && <Card title={<span className="mono">{pump.asset_id}</span>}
            sub={`as of ${fmtTime(real.state.as_of)} UTC`}
            aside={<StateBadge state={pump.state.state} />}>
            <div className="readouts">
              <ValueReadout label="open, this replay" value={real.open_cases} />
              <ValueReadout label="stale readings"
                            value={pump.data_quality.flag_counts.stale_suspected} size="sm" />
            </div>
          </Card>}
        </div>))}
    </div>
  );
}

function Gallery() {
  const fleet = useQuery({ queryKey: ["fleet"], queryFn: () => call(client.GET("/api/fleet")) });
  const casesQuery = { limit: 5 };
  const cases = useQuery({ queryKey: ["cases", casesQuery], queryFn: () =>
    call(client.GET("/api/cases", { params: { query: casesQuery } })) });
  const register = useAssumptions();
  const pump = fleet.data?.pumps.find((p) => p.state_session_id != null);
  const real = pump?.sessions.find((x) => x.session.session_id === pump.state_session_id);
  const syn = fleet.data?.pumps.flatMap((p) => p.sessions).find((x) => x.synthetic);
  const synCase = cases.data?.cases.find((c) => c.synthetic);
  const [text, setText] = useState("");
  const [pick, setPick] = useState("monitor");
  const [note, setNote] = useState("");
  const [again, setAgain] = useState(0);

  return (
    <div className="gallery-grid">
      <Specimen title="State badge" note="Label, shape and pattern; meaning colour is reserved
        for these. Normal stays quiet.">
        <div className="specimen-row">{STATES.map((s) => <StateBadge key={s} state={s} />)}</div>
        <div className="specimen-row">{STATES.map((s) =>
          <StateBadge key={s} state={s} size="lg" />)}</div>
      </Specimen>

      <Specimen title="SYNTHETIC marker" note="Hazard stripes and a diamond, on every synthetic
        score, case, card and export.">
        <div className="specimen-row"><Synthetic show />
          {synCase && <span>inline: <Link to={`/cases/${synCase.case_id}`}>case
            #{synCase.case_id}</Link> <Synthetic show /></span>}</div>
      </Specimen>

      <Specimen title="Provenance strip" note="From the API: a real session and a synthetic one.">
        <Loading q={fleet} lines={2}>
          {real && <ProvenanceStrip p={{ synthetic: false, model_version: real.model_version,
                                         assumptions: real.assumptions }} />}
          {syn && <ProvenanceStrip p={{ synthetic: true, model_version: syn.model_version,
                                        assumptions: syn.assumptions }} />}
        </Loading>
      </Specimen>

      <Specimen title="Assumption chip" note={<>Each opens its entry in the{" "}
        <Link to="/assumptions">assumptions register</Link>.</>}>
        <Loading q={register} lines={1}>
          <div className="chips">{register.data?.assumptions.map((a) =>
            <AssumptionChip key={a.id} id={a.id} />)}</div>
        </Loading>
      </Specimen>

      <Specimen title="Value readout" note="Tabular monospace figures with the unit apart; real
        values from the fleet.">
        <Loading q={fleet} lines={2}>
          {pump && <div className="readouts">
            <ValueReadout label="pumps" value={fleet.data?.pumps.length} />
            <ValueReadout label="open cases, real" value={pump.open_cases.real} />
            <ValueReadout label="stale readings" value={pump.data_quality.flag_counts.stale_suspected}
                          unit={`on ${pump.data_quality.source_day}`} />
            <ValueReadout label="replay cursor" value={fmtTime(real?.state.as_of)} unit="UTC"
                          size="sm" />
          </div>}
        </Loading>
      </Specimen>

      <Specimen title="Card" note="Accent bar, head with a state, body, and a foot for
        provenance. A SYNTHETIC card adds the striped frame and banner (see the fleet).">
        <Loading q={fleet} lines={3}>
          {pump && real && <Card title={<span className="mono">{pump.asset_id}</span>}
            sub={`as of ${fmtTime(real.state.as_of)} UTC (replay cursor)`}
            aside={<StateBadge state={pump.state.state} />}
            foot={<ProvenanceStrip p={{ synthetic: false, model_version: real.model_version,
                                        assumptions: real.assumptions }} />}>
            <div className="readouts"><ValueReadout label="open, this replay"
                                                    value={real.open_cases} /></div>
          </Card>}
        </Loading>
      </Specimen>

      <Specimen title="Table" note="In a focusable region that scrolls sideways on narrow
        screens; real cases.">
        <Loading q={cases} lines={4}>
          {cases.data && (cases.data.cases.length ? <Table label="Latest cases">
            <thead><tr><th>case</th><th>data</th><th>asset</th><th>status</th>
              <th className="num">windows</th></tr></thead>
            <tbody>{cases.data.cases.map((c) => (
              <tr key={c.case_id}><td><Link to={`/cases/${c.case_id}`}>#{c.case_id}</Link></td>
                <td>{c.synthetic ? <Synthetic show /> : "real"}</td>
                <td className="mono">{c.asset_id}</td><td>{c.status}</td>
                <td className="num">{c.evidence_windows}</td></tr>))}</tbody>
          </Table> : <EmptyState title="No cases yet" />)}
        </Loading>
      </Specimen>

      <Specimen title="Tabs" note="Arrow keys, Home and End move between tabs.">
        <Tabs label="Example tabs" tabs={[
          { id: "a", label: "Evidence", content: <p>Tab panels hold one view each.</p> },
          { id: "b", label: "Timeline", content: <p>The selected tab is underlined.</p> },
          { id: "c", label: "Export", content: <p>Focus moves with the arrow keys.</p> }]} />
      </Specimen>

      <Specimen title="Buttons" note="Primary for the one main step of a view. Nothing here
        controls equipment.">
        <div className="btn-row">
          <Button variant="primary">Acknowledge</Button>
          <Button>Add note</Button>
          <Button variant="ghost">Preview</Button>
          <Button size="sm">Small</Button>
          <Button disabled>Disabled</Button>
        </div>
      </Specimen>

      <Specimen title="Form fields" note="Label, hint and error; example inputs, not saved.">
        <div className="fields">
          <TextField label="Acting as" hint="remembered in this browser" value={text}
                     onChange={setText} placeholder="operator" />
          <SelectField label="Disposition" value={pick} onChange={setPick} options={[
            { value: "monitor", label: "monitor" },
            { value: "known", label: "known condition, no action" },
            { value: "dq", label: "data quality issue" }]} />
        </div>
        <TextAreaField label="Reason" hint="required for a disposition" value={note}
                       onChange={setNote} error={note.trim() ? undefined : "a reason is required"} />
      </Specimen>

      <Specimen title="Empty state" note="Says why nothing is shown and what would change it.">
        <EmptyState title="No open cases">A case opens when a replay reaches evidence worth
          review.</EmptyState>
      </Specimen>

      <Specimen title="Loading skeleton" note="Shapes of what is coming; still under reduced
        motion.">
        <Skeleton lines={3} label="example loading" />
      </Specimen>

      <Specimen title="Error panel" note="What failed, the error, and what to try (an example).">
        <ErrorPanel title="Could not load the fleet"
                    error={new Error("example: the API did not answer")}
                    action={<Button size="sm">Try again</Button>} />
      </Specimen>

      <Specimen title="Motion" note="Small and purposeful: a new live case slides in (on the
        fleet and the case list). Off under reduced motion.">
        <Loading q={cases} lines={1}>
          {cases.data?.cases[0] && <ul className="case-list">
            <li key={again} className="case-item enter">
              <Link to={`/cases/${cases.data.cases[0].case_id}`}>
                Case #{cases.data.cases[0].case_id}</Link></li></ul>}
          <Button size="sm" onClick={() => setAgain((n) => n + 1)}>Replay the arrival</Button>
        </Loading>
      </Specimen>

      <Specimen title="Tokens" note="Every colour, space, radius, shadow and type size is a token
        (styles/tokens.css); a theme is a set of token values.">
        <Swatches />
      </Specimen>
    </div>
  );
}

export function Design() {
  const fleet = useQuery({ queryKey: ["fleet"], queryFn: () => call(client.GET("/api/fleet")) });
  const sessions = fleet.data?.pumps.flatMap((p) => p.sessions) ?? [];
  usePageProvenance(fleet.data && {
    synthetic: sessions.some((x) => x.synthetic) ? "mixed" : false,
    model_version: [...new Set(sessions.flatMap((x) => x.model_version))].sort(),
    assumptions: [...new Set(fleet.data.pumps.flatMap((p) => p.assumptions))]
      .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1))) });
  return (
    <section className="gallery">
      <header className="masthead panel">
        <div className="masthead-accent" aria-hidden="true" />
        <div>
          <p className="eyebrow">Step 5b · design system</p>
          <h1>Components</h1>
          <p className="lede">Expressive colour for backgrounds, brand, tiles and charts; meaning
            colour for the states and SYNTHETIC only, always with a label, a shape and a
            pattern.</p>
        </div>
      </header>
      <Tabs label="Gallery view" tabs={[
        { id: "this", label: "This theme", content: <Gallery /> },
        { id: "all", label: "All three themes", content: <AllThemes /> }]} />
    </section>
  );
}
