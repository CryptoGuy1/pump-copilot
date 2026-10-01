import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { type Schema, call, client } from "../api/client";
import { Card, Loading, RepoCommit, RepoFile, Table, ValueReadout } from "../components/ui";
import { usePageTitle } from "../hooks/usePageTitle";
import { usePageProvenance } from "../pageProvenance";

/** Step 5b stage 3: the evaluation as three chapters. Every number comes from
 * GET /api/evaluation/chapters (the stored results); nothing here is hard-coded. */

type Chapters = Schema<"EvaluationChapters">;
const pct = (x: number | null | undefined, d = 1) => (x == null ? "-" : `${(x * 100).toFixed(d)}%`);
const f3 = (x: number | null | undefined) => (x == null ? "-" : x.toFixed(3));

function HowToRead({ text, links }: { text: string; links: ReactNode }) {
  return (
    <div className="how-to-read">
      <p><strong>How to read this.</strong> {text}</p>
      <p className="muted chapter-links">{links}</p>
    </div>
  );
}

/** A value from 0 to 1 as a bar, with its interval as a whisker with end caps. */
function IntervalBar({ value, lo, hi, label }: { value: number; lo?: number | null;
                                                 hi?: number | null; label: string }) {
  return (
    <div className="ibar" role="img" aria-label={`${label}: ${f3(value)}${lo != null && hi != null
      ? `, 95% interval ${f3(lo)} to ${f3(hi)}` : ""}`}>
      <span className="ibar-fill" style={{ width: `${value * 100}%` }} />
      {lo != null && hi != null && <span className="ibar-ci"
        style={{ left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` }}>
        <span className="ibar-cap ibar-cap-lo" /><span className="ibar-cap ibar-cap-hi" /></span>}
    </div>
  );
}

function Zema({ z }: { z: NonNullable<Chapters["zema"]> }) {
  const max = Math.max(...z.confusion.matrix.flat(), 1);
  const sc = z.shortcut;
  return (
    <Card as="section" accent={false} title="1 · ZeMA hydraulic test rig"
          sub="Pump leakage state from a laboratory test rig (public benchmark)">
      <aside className="notice"><strong>Test rig only.</strong> {z.scope_note}</aside>
      <HowToRead text={z.how_to_read} links={<>Report: <RepoFile path={z.report} /> ·
        pre-registration <RepoCommit sha={z.preregistration.commit}
                                     label={z.preregistration.tag ?? "commit"} /></>} />
      {z.contrast && <p className="key-finding"><strong>{z.contrast}.</strong></p>}
      <h3>Macro-F1 by split (test), headline model against always guessing</h3>
      <div className="f1-rows">
        {z.splits.map((s) => (
          <div key={s.split} className="f1-row">
            <span className="f1-split">{s.split}</span>
            <div className="f1-bars">
              <div><span className="muted">{s.headline.model}</span>
                <IntervalBar value={s.headline.macro_f1} lo={s.headline.lo} hi={s.headline.hi}
                             label={`${s.split}, ${s.headline.model}`} />
                <span className="num">{f3(s.headline.macro_f1)} [{f3(s.headline.lo)}–
                  {f3(s.headline.hi)}]</span></div>
              <div><span className="muted">always guess</span>
                <IntervalBar value={s.majority.macro_f1} lo={s.majority.lo} hi={s.majority.hi}
                             label={`${s.split}, always guessing`} />
                <span className="num">{f3(s.majority.macro_f1)}</span></div>
            </div>
          </div>))}
      </div>
      <div className="chapter-grid">
        <div>
          <h3>Confusion, {z.confusion.split} split ({z.confusion.model}, {z.confusion.n} cycles)</h3>
          <Table label="Confusion matrix">
            <thead><tr><th>True \ predicted</th>
              {z.confusion.labels.map((l) => <th key={l} className="num">leakage {l}</th>)}</tr>
            </thead>
            <tbody>{z.confusion.matrix.map((row, i) => (
              <tr key={i}><th scope="row">leakage {z.confusion.labels[i]}</th>
                {row.map((v, j) => (
                  <td key={j} className={`num cm${i === j ? " cm-diag" : ""}`}>{v}
                    <span className="cm-bar" style={{ width: `${(v / max) * 100}%` }}
                          aria-hidden="true" /></td>))}</tr>))}</tbody>
          </Table>
        </div>
        <div>
          <h3>The stable-flag shortcut</h3>
          <p>The rig's "stable" flag alone carries <span className="num">
            {f3(sc.mutual_information_bits)}</span> bits about leakage, {pct(
            sc.share_of_leakage_entropy)} of its uncertainty, over {sc.n_cycles} cycles: a
            model can look good by learning the rig's test schedule.</p>
          {sc.table && <Table label="Stable flag by leakage state">
            <thead><tr><th>Stable flag</th>{Object.keys(Object.values(sc.table)[0] ?? {}).map(
              (k) => <th key={k} className="num">leakage {k}</th>)}</tr></thead>
            <tbody>{Object.entries(sc.table).map(([flag, row]) => (
              <tr key={flag}><th scope="row">{flag}</th>{Object.values(row).map((v, i) =>
                <td key={i} className="num">{v}</td>)}</tr>))}</tbody>
          </Table>}
          {z.calibration && <div className="readouts">
            <ValueReadout label="Calibration grade" value={z.calibration.grade} />
            <ValueReadout label="Top-label ECE" value={f3(z.calibration.ece)} size="sm" />
            <ValueReadout label="Brier" value={f3(z.calibration.brier)} size="sm" />
          </div>}
          {z.calibration && <p className="muted">{z.calibration.text}.</p>}
        </div>
      </div>
    </Card>
  );
}

function Cira({ c }: { c: NonNullable<Chapters["cira"]> }) {
  const pumps = [...new Set(c.modes.flatMap((m) => m.pumps.map((p) => p.pump)))];
  const faults = [...new Set(c.synthetic.cells.map((x) => x.fault))];
  const bySize = (f: string) => c.synthetic.cells.filter((x) => x.fault === f)
    .sort((a, b) => a.size - b.size);
  const classes = faults.length ? bySize(faults[0]).map((x) => x.size_class) : [];
  const explore = c.modes.filter((m) => m.exploratory.length);
  return (
    <Card as="section" accent={false} title="2 · CIRA detector"
          sub="Real centrifugal pumps, no fault labels; synthetic faults injected in memory">
      <HowToRead text={c.how_to_read} links={<>Report: <RepoFile path={c.report} /> ·
        pre-registration <RepoCommit sha={c.preregistration.commit}
                                     label={c.preregistration.tag ?? "commit"} /></>} />
      {c.key_finding && <p className="key-finding"><strong>Key finding.</strong> {c.key_finding}</p>}
      <h3>Running time in a review case, by detector mode (October)</h3>
      <Table label="Detector modes">
        <thead><tr><th>Mode</th>{pumps.map((p) => <th key={p}>Pump {p}</th>)}</tr></thead>
        <tbody>{c.modes.map((m) => (
          <tr key={m.mode}><th scope="row">{m.name} <code className="mode-tag">{m.mode}</code></th>
            {pumps.map((p) => {
            const x = m.pumps.find((q) => q.pump === p);
            const note = m.exploratory.some((e) => e.pump === p)
              && <sup><a href="#cira-exploratory" aria-label="exploratory run, see note">*</a></sup>;
            if (!x) return <td key={p} className="muted">-{note}</td>;
            if (x.abstained) return <td key={p} className="muted">abstained{note}</td>;
            return <td key={p}><span className="num">{pct(x.case_time_fraction)}</span>
              <span className="share-bar" aria-hidden="true">
                <span style={{ width: `${(x.case_time_fraction ?? 0) * 100}%` }} /></span>
              <span className="muted"> {x.cases} case{x.cases === 1 ? "" : "s"}</span></td>;
          })}</tr>))}</tbody>
      </Table>
      {explore.length > 0 && <p id="cira-exploratory" className="muted footnote">* {
        c.exploratory_note} {explore.map((m) => m.exploratory.map((e) => `${m.name}, pump ${
          e.pump}: ${pct(e.case_time_fraction)} (${e.cases} case${e.cases === 1 ? "" : "s"})`)
          .join("; ")).join("; ")}.</p>}
      <h3>Synthetic faults detected, by type and size ({c.synthetic.day})</h3>
      <div className="heatmap" role="table" aria-label="Synthetic detection rate by fault and size">
        {classes.every(Boolean) && <div role="row" className="hm-row hm-head">
          <span role="columnheader" className="hm-fault">Fault size</span>
          {classes.map((k) => <span key={k} role="columnheader">{k}</span>)}</div>}
        {faults.map((f) => (
          <div key={f} role="row" className="hm-row">
            <span role="rowheader" className="hm-fault">{f}</span>
            {bySize(f).map((x) => (
              <span key={x.size_label} role="cell" className="hm-cell"
                    style={{ "--rate": x.detection_rate } as React.CSSProperties}>
                <span className="hm-label"><span className="hm-size">{x.size_label}</span>
                  <span className="hm-rate num">{pct(x.detection_rate, 0)}</span>
                  <span className="hm-n num">{x.detected}/{x.injections}</span></span>
              </span>))}
          </div>))}
      </div>
    </Card>
  );
}

function Assistant({ a }: { a: NonNullable<Chapters["assistant"]> }) {
  return (
    <Card as="section" accent={false} title="3 · Copilot assistant"
          sub="Real-model runs, each answer checked before it is shown">
      <HowToRead text={a.how_to_read} links={<>Report: <RepoFile path={a.report} /> ·
        holdouts {a.revisions.filter((r) => r.holdout_commit).map((r) => <span key={r.revision}>
          <RepoCommit sha={r.holdout_commit} label={r.revision} />{" "}</span>)}</>} />
      <div className="readouts headline-readouts">
        <ValueReadout label="Instructions served" value={a.instructions_served} />
        <ValueReadout label="Answers served by the model" value={a.served_answers} size="sm" />
      </div>
      <p className="muted">Counted with {a.instructions_rule}.
        {a.flagged.length > 0 && <> The rule flags {a.flagged.length} served answer
          {a.flagged.length === 1 ? "" : "s"}, each read by a person:</>}</p>
      {a.flagged.length > 0 && <ul className="flagged">{a.flagged.map((f) => (
        <li key={`${f.run}-${f.id}-${f.where}`}><span className="mono">{f.run} · {f.id}</span>:
          "{f.text}" <strong>{f.verdict ?? "not yet reviewed (counted)"}</strong>
          {f.why && <> — {f.why}</>} <span className="muted">({f.reviewed_by})</span></li>))}
      </ul>}
      <Table label="Assistant revisions">
        <thead><tr><th>Checker revision</th><th>Questions</th><th>Served as checked</th>
          <th>Adversarial: raw answers passing</th><th>Adversarial: final</th></tr></thead>
        <tbody>{a.revisions.map((r) => (
          <tr key={r.revision}><th scope="row">{r.checker_version} ({r.revision})</th>
            <td>{r.questions.set}</td>
            <td><span className="num">{r.questions.served_checked}/{r.questions.total}</span>
              <span className="share-bar" aria-hidden="true"><span style={{
                width: `${(r.questions.served_checked / r.questions.total) * 100}%` }} /></span></td>
            <td><span className="num">{r.adversarial.raw_passed}/{r.adversarial.total}</span>
              <span className="share-bar" aria-hidden="true"><span style={{
                width: `${(r.adversarial.raw_passed / r.adversarial.total) * 100}%` }} /></span></td>
            <td><span className="num">{r.adversarial.final_passed}/{r.adversarial.total}</span>
              {r.adversarial.final_passed_corrected != null && <span className="muted"> ({
                r.adversarial.final_passed_corrected}/{r.adversarial.total} with corrected
                expectations)</span>}</td></tr>))}</tbody>
      </Table>
    </Card>
  );
}

export function Evaluation() {
  usePageTitle("Evaluation");
  usePageProvenance("Stored evaluation results from committed reports · nothing re-scored");
  const q = useQuery({ queryKey: ["evaluation-chapters"],
                       queryFn: () => call(client.GET("/api/evaluation/chapters")) });
  return (
    <section className="evaluation">
      <header className="masthead panel">
        <div className="masthead-accent" aria-hidden="true" />
        <div>
          <p className="eyebrow">Stored results · nothing re-scored</p>
          <h1>Evaluation</h1>
          <p className="lede">Three chapters: a public test-rig benchmark, the detector on real
            pumps, and the checked assistant. Each says how to read it and where its numbers
            come from.</p>
        </div>
      </header>
      <Loading q={q} lines={8}>
        {q.data?.zema && <Zema z={q.data.zema} />}
        {q.data?.cira && <Cira c={q.data.cira} />}
        {q.data?.assistant && <Assistant a={q.data.assistant} />}
      </Loading>
    </section>
  );
}
