import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, type Schema, call, client } from "../api/client";
import { AssistantPanel, type Highlight } from "../components/Assistant";
import { ChartGroup, type Span, TimeChart, signalColor, toSeconds, utc }
  from "../components/charts";
import { A8Notice, fmtTime, useActor } from "../components/common";
import { Actions as Disabled, Button, Card, Loading, SignalName, StateBadge, Synthetic, Table,
         useSignalFormat, useSignalLabel } from "../components/ui";
import { usePageTitle } from "../hooks/usePageTitle";
import { usePageProvenance } from "../pageProvenance";
import { SNAPSHOT } from "../snapshot";

const DISPOSITIONS = ["monitor", "known condition, no action", "data quality issue",
                      "escalate to reliability engineer (export only)"] as const;
type Disposition = (typeof DISPOSITIONS)[number];
type Detail = Schema<"CaseDetail">;
type Item = Schema<"EvidencePage">["items"][number];
const STEPS = ["open", "acknowledged", "dispositioned", "closed"] as const;
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const secs = (t: string) => Date.parse(t) / 1000;

/** Every evidence window of the case (all pages), for the timeline and the table. */
function useEvidence(id: number) {
  return useQuery({ queryKey: ["evidence", id], queryFn: async () => {
    const items: Item[] = [];
    let offset: number | null = 0;
    while (offset != null) {
      const p: Schema<"EvidencePage"> = await call(client.GET("/api/cases/{case_id}/evidence",
        { params: { path: { case_id: id }, query: { offset, limit: 500 } } }));
      items.push(...p.items);
      offset = p.next_offset ?? null;
    }
    return items;
  } });
}

/** Where the case is: open, acknowledged, dispositioned, closed. */
function Stepper({ status }: { status: string }) {
  const at = STEPS.indexOf(status as (typeof STEPS)[number]);
  return (
    <ol className="stepper" aria-label="Case status">
      {STEPS.map((s, i) => (
        <li key={s} className={i < at ? "done" : i === at ? "current" : "todo"}
            aria-current={i === at ? "step" : undefined}>
          <span className="step-mark" aria-hidden="true">{i < at ? "✓" : i + 1}</span>
          {cap(s)}{i === at && <span className="visually-hidden"> (current)</span>}
        </li>))}
    </ol>
  );
}

function Actions({ d }: { d: Detail }) {
  const id = d.case.case_id;
  const path = { case_id: id };
  const qc = useQueryClient();
  const [actor, setActor] = useActor();
  const [note, setNote] = useState("");
  const [disp, setDisp] = useState<Disposition>("monitor");
  const [reason, setReason] = useState("");
  const act = useMutation({
    mutationFn: (kind: string) => {
      if (kind === "acknowledge")
        return call(client.POST("/api/cases/{case_id}/acknowledge",
                                { params: { path }, body: { actor } }));
      if (kind === "note")
        return call(client.POST("/api/cases/{case_id}/notes",
                                { params: { path }, body: { actor, text: note } }));
      if (kind === "disposition")
        return call(client.POST("/api/cases/{case_id}/disposition",
                                { params: { path }, body: { actor, disposition: disp, reason } }));
      return call(client.POST("/api/cases/{case_id}/close", { params: { path }, body: { actor } }));
    },
    onSuccess: () => {
      setNote("");
      qc.invalidateQueries({ queryKey: ["case", id] });
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });
  const can = (a: Detail["actions"][number]) => d.actions.includes(a);
  const st = d.case.status;
  const err = act.error as ApiError | null;
  const why = (a: string) => can(a as Detail["actions"][number]) ? "Available now"
    : st === "closed" ? "The case is closed"
    : a === "acknowledge" ? "Done" : a === "disposition" ? "Acknowledge the case first"
    : a === "close" ? "Set a disposition first" : "Not available";
  return (
    <Card title="Actions" sub="Nothing here controls equipment; escalation is export-only."
          accent={false} as="section">
      <Disabled>
      <label className="field"><span className="field-label">Acting as</span>
        <input value={actor} onChange={(e) => setActor(e.target.value)} /></label>
      <div className="action-grid">
        <div className="action">
          <h3>Acknowledge</h3><p className="muted">{why("acknowledge")}</p>
          <Button variant={can("acknowledge") ? "primary" : "secondary"}
                  disabled={!can("acknowledge") || act.isPending}
                  onClick={() => act.mutate("acknowledge")}>Acknowledge</Button>
        </div>
        <div className="action">
          <h3>Note</h3><p className="muted">{why("note")}</p>
          <input placeholder="note" aria-label="note" value={note} disabled={!can("note")}
                 onChange={(e) => setNote(e.target.value)} />
          <Button disabled={!can("note") || !note.trim() || act.isPending}
                  onClick={() => act.mutate("note")}>Add note</Button>
        </div>
        <div className="action">
          <h3>Disposition</h3><p className="muted">{why("disposition")}
            {d.case.disposition && <> · now <strong>{d.case.disposition}</strong></>}</p>
          <select aria-label="disposition" value={disp} disabled={!can("disposition")}
                  onChange={(e) => setDisp(e.target.value as Disposition)}>
            {DISPOSITIONS.map((x) => <option key={x}>{x}</option>)}
          </select>
          <input placeholder="reason (required)" aria-label="reason" value={reason}
                 disabled={!can("disposition")} onChange={(e) => setReason(e.target.value)} />
          <Button variant={can("disposition") && !can("close") ? "primary" : "secondary"}
                  disabled={!can("disposition") || !reason.trim() || act.isPending}
                  onClick={() => act.mutate("disposition")}>Set disposition</Button>
        </div>
        <div className="action">
          <h3>Close</h3><p className="muted">{why("close")}</p>
          <Button variant={can("close") ? "primary" : "secondary"}
                  disabled={!can("close") || act.isPending}
                  onClick={() => act.mutate("close")}>Close case</Button>
        </div>
      </div>
      </Disabled>
      {err && <p className="error" role="alert">{err.status} {err.code}: {err.message}</p>}
    </Card>
  );
}

interface Row { sig: string; episodes: [number, number][]; windows: number; first: number;
                last: number }

/** The evidence over time: one row per signal, its episodes as bars. A row opens that
 * signal's chart with its evidence highlighted. */
function EvidenceTimeline({ items, onPick, picked }:
                          { items: Item[]; onPick: (sig: string, from: number, to: number) => void;
                            picked: string | null }) {
  const rows = useMemo(() => {
    const by = new Map<string, Row>();
    for (const w of [...items].sort((a, b) => a.window_start.localeCompare(b.window_start))) {
      const a = secs(w.window_start), b = secs(w.window_end);
      const r = by.get(w.signal_name) ?? { sig: w.signal_name, episodes: [], windows: 0,
                                           first: a, last: b };
      const e = r.episodes[r.episodes.length - 1];
      if (e && !w.episode_start && a <= e[1] + 60) e[1] = Math.max(e[1], b);
      else r.episodes.push([a, b]);
      r.windows += 1; r.first = Math.min(r.first, a); r.last = Math.max(r.last, b);
      by.set(w.signal_name, r);
    }
    return [...by.values()].sort((a, b) => b.windows - a.windows);
  }, [items]);
  if (!rows.length) return null;
  const lo = Math.min(...rows.map((r) => r.first)), hi = Math.max(...rows.map((r) => r.last));
  const w = Math.max(60, hi - lo);
  const hours: number[] = [];
  for (let t = Math.ceil(lo / 3600) * 3600; t <= hi; t += 3600) hours.push(t);
  return (
    <div className="evidence-timeline" role="list" aria-label="Evidence over time">
      <div className="et-axis" aria-hidden="true">
        {hours.map((t) => <span key={t} className="et-tick"
                                style={{ left: `${((t - lo) / w) * 100}%` }}>{utc(t, false)}</span>)}
      </div>
      {rows.map((r) => (
        <div key={r.sig} role="listitem" className={`et-row${picked === r.sig ? " picked" : ""}`}>
          <button type="button" className="et-name" onClick={() => onPick(r.sig, r.first, r.last)}
                  aria-label={`Show on the chart: ${r.sig}, ${r.episodes.length} episodes, ${
                    r.windows} windows, ${utc(r.first, false)}–${utc(r.last, false)} UTC`}>
            <SignalName id={r.sig} /><span className="muted num"> {r.windows}</span></button>
          <div className="et-track" aria-hidden="true">
            {hours.map((t) => <span key={t} className="et-hour"
                                    style={{ left: `${((t - lo) / w) * 100}%` }} />)}
            {r.episodes.map(([a, b]) => (
              <span key={a} className="et-bar" style={{ left: `${((a - lo) / w) * 100}%`,
                width: `max(${((b - a) / w) * 100}%, var(--space-1))` }} />))}
          </div>
        </div>))}
    </div>
  );
}

function Export({ id }: { id: number }) {
  const [md, setMd] = useState<string | null>(null);
  const load = async () => {
    const r = await fetch(`/api/cases/${id}/export?format=markdown`);
    setMd(await r.text());
  };
  return (
    <Card title="Export" sub="An evidence pack for a reliability engineer, as of the replay cursor."
          accent={false} as="section">
      {SNAPSHOT ? <Disabled><div className="btn-row">
        <Button>JSON</Button><Button>Markdown evidence pack</Button>
        <Button>Preview evidence pack</Button></div></Disabled>
      : <div className="btn-row">
        <a href={`/api/cases/${id}/export`} target="_blank" rel="noreferrer">JSON</a>
        <a href={`/api/cases/${id}/export?format=markdown`} target="_blank" rel="noreferrer">
          Markdown evidence pack</a>
        <Button onClick={load}>Preview evidence pack</Button>
      </div>}
      {md && <pre className="export" data-testid="export-preview">{md}</pre>}
    </Card>
  );
}

/** One signal's band chart for the case window. */
function SignalChart({ sig, s, i, hl, synthetic }:
                     { sig: string; s: Detail["signals"][string]; i: number;
                       hl: Highlight | null; synthetic: boolean }) {
  const name = useSignalLabel();
  const f = useSignalFormat()(sig);
  const n = s.chart.t.length;
  const x = useMemo(() => toSeconds(s.chart.t), [s.chart.t]);
  const color = signalColor(i);
  const spans = useMemo(() => {
    const out: Span[] = [];
    s.chart.review.forEach((r, k) => {
      if (!r) return;
      const a = k ? x[k - 1] : x[k], b = x[k], last = out[out.length - 1];
      if (last && a <= last.to) last.to = b; else out.push({ from: a, to: b, kind: "review" });
    });
    if (hl && (hl.signal === sig || hl.signal === null) && hl.from && hl.to)
      out.push({ from: secs(hl.from), to: secs(hl.to), kind: "highlight" });
    return out;
  }, [s.chart.review, x, hl, sig]);
  // one signal per chart: solid lines (the min and max are thin and muted)
  const lines = useMemo(() => [
    { label: "Median", values: f.all(s.chart.median), color, width: 2 },
    { label: "Min", values: f.all(s.chart.min), color: "--chart-minmax" as const, width: 1 },
    { label: "Max", values: f.all(s.chart.max), color: "--chart-minmax" as const, width: 1 },
  ], [s.chart, color]);  // eslint-disable-line react-hooks/exhaustive-deps
  const band = useMemo(() => ({ low: Array(n).fill(f.one(s.band?.low)),
                                high: Array(n).fill(f.one(s.band?.high)) }),
                       [n, s.band]);  // eslint-disable-line react-hooks/exhaustive-deps
  const on = !!hl && (hl.signal === sig || hl.signal === null);
  return <TimeChart title={name(sig)} titleTip={sig} unit={f.unit} x={x} lines={lines}
                    band={s.band ? band : undefined} spans={spans} highlighted={on}
                    synthetic={synthetic} swatch={{ color }} />;
}

export function CaseDetail() {
  const id = Number(useParams().id);
  usePageTitle(`Case #${id}`);
  const q = useQuery({ queryKey: ["case", id], queryFn: () =>
    call(client.GET("/api/cases/{case_id}", { params: { path: { case_id: id } } })) });
  const ev = useEvidence(id);
  const d = q.data;
  const [actor] = useActor();
  const [hl, setHl] = useState<Highlight | null>(null);
  const [showAll, setShowAll] = useState(false);
  const name = useSignalLabel();
  usePageProvenance(d && { synthetic: d.synthetic, model_version: d.model_version,
                           assumptions: d.assumptions });
  const format = useSignalFormat();
  const ordered = useMemo(() => Object.entries(d?.signals ?? {}).sort(
    ([, a], [, b]) => b.summary.windows - a.summary.windows), [d]);
  const driving = ordered.slice(0, 3), others = ordered.slice(3);
  const extent = useMemo<[number, number] | null>(() => {
    const ts = ordered.flatMap(([, s]) => s.chart.t).map(secs);
    return ts.length ? [Math.min(...ts), Math.max(...ts)] : null;
  }, [ordered]);
  const asOf = d?.case.as_of ? secs(d.case.as_of) : null;
  const cursorAt = asOf != null && extent && asOf < extent[1] ? asOf : null;
  // a highlight (from the timeline or an evidence ID) opens the chart it points at
  const highlight = (h: Highlight | null) => {
    setHl(h);
    if (h && (h.signal === null || others.some(([s]) => s === h.signal))) setShowAll(true);
  };
  const pick = (sig: string, from: number, to: number) =>
    highlight({ id: sig, signal: sig, from: new Date(from * 1000).toISOString(),
                to: new Date(to * 1000).toISOString() });
  return (
    <section className="case-detail">
      <Loading q={q} lines={6}>
        {d && <>
          <header className={`masthead panel${d.case.synthetic ? " masthead-synthetic" : ""}`}>
            <div className="masthead-accent" aria-hidden="true" />
            {d.case.synthetic && <div className="synthetic-banner banner-top"><Synthetic show />
              <span>This case comes from a replay with an injected fault: it is not evidence
                about the real pump.</span></div>}
            <div>
              <p className="eyebrow"><span className="mono">{d.case.asset_id}</span> ·{" "}
                <Link to={`/assets/${d.case.asset_id}/${d.case.source_day}` +
                  `?session=${d.case.session_id}`}>{d.case.source_day}</Link> · run{" "}
                {d.case.stretch + 1} · replay #{d.case.session_id}</p>
              <h1>Case #{id} <Synthetic show={d.case.synthetic} /></h1>
              <p className="lede">Evidence <span className="num">{fmtTime(d.case.evidence_start)}–
                {fmtTime(d.case.evidence_end)}</span> UTC · {d.case.episodes} episodes,{" "}
                {d.case.evidence_windows} windows · as of <span className="num">
                  {fmtTime(d.case.as_of)}</span> UTC (replay cursor) · status{" "}
                <strong data-testid="case-status">{d.case.status}</strong></p>
            </div>
            <div className="case-state">
              <StateBadge state="review_suggested" size="lg" />
              <Stepper status={d.case.status} />
            </div>
          </header>
          <A8Notice />
          <div className="case-grid">
            <div className="case-main">
              <Card title="Evidence timeline" accent={false} as="section"
                    sub="Each row is a signal; bars are its review episodes (hours in UTC). Choose a row to see it on the chart.">
                <Loading q={ev} lines={4}>
                  <EvidenceTimeline items={ev.data ?? []} onPick={pick}
                                    picked={hl?.signal ?? null} />
                </Loading>
              </Card>
              <Card title="Driving signals against their band" accent={false} as="section"
                    sub="The signals with the most review windows in this case.">
                <ChartGroup extent={extent} cursorAt={cursorAt} label="Case charts" legend={[
                    { kind: "band", label: "Baseline band" },
                    { kind: "review", label: "Review windows" },
                    { kind: "line", label: "Min and max of the window medians",
                      color: "--chart-minmax" },
                    { kind: "highlight", label: "Highlighted evidence" },
                    ...(cursorAt != null ? [{ kind: "cursor" as const, label: "Replay cursor" }]
                      : [])]}>
                  {hl && <p className="muted" data-testid="highlight-note">Highlighting {hl.id}
                    {hl.signal ? ` (${name(hl.signal)})` : " (the whole case)"}{" "}
                    <button className="link" onClick={() => setHl(null)}>clear</button></p>}
                  {driving.map(([sig, s], i) => <SignalChart key={sig} sig={sig} s={s} i={i}
                                                             hl={hl} synthetic={d.synthetic} />)}
                  {others.length > 0 && <details open={showAll}
                    onToggle={(e) => setShowAll((e.target as HTMLDetailsElement).open)}>
                    <summary>Other signals ({others.length})</summary>
                    {showAll && others.map(([sig, s], i) => <SignalChart key={sig} sig={sig} s={s}
                      i={i + 3} hl={hl} synthetic={d.synthetic} />)}
                  </details>}
                </ChartGroup>
              </Card>
              <Card title="Signals in this case" accent={false} as="section">
                <Table label="Signals in this case">
                  <thead><tr><th>Signal</th><th className="num">Windows</th>
                    <th className="num">Episodes</th><th>First–last (UTC)</th>
                    <th className="num">Max score</th><th>Band</th></tr></thead>
                  <tbody>{ordered.map(([sig, s]) => (
                    <tr key={sig}><td><SignalName id={sig} /></td>
                      <td className="num">{s.summary.windows}</td>
                      <td className="num">{s.summary.episodes}</td>
                      <td className="num">{fmtTime(s.summary.first_window_start)}–
                        {fmtTime(s.summary.last_window_end)}</td>
                      <td className="num">{s.summary.max_score?.toFixed(2)}</td>
                      <td className="num">{s.band ? `${format(sig).text(s.band.low)} to ${
                        format(sig).text(s.band.high)} ${format(sig).unit}` : "-"}</td></tr>))}
                  </tbody>
                </Table>
                <details>
                  <summary>All evidence windows ({ev.data?.length ?? "…"})</summary>
                  <Table label="All evidence windows">
                    <thead><tr><th>Window (UTC)</th><th>Signal</th><th className="num">Median</th>
                      <th className="num">Band</th><th className="num">Score</th></tr></thead>
                    <tbody>{(ev.data ?? []).map((w) => (
                      <tr key={`${w.signal_name}-${w.window_end}`}>
                        <td className="num">{fmtTime(w.window_start)}–{fmtTime(w.window_end)}</td>
                        <td><SignalName id={w.signal_name} /></td>
                        <td className="num">{format(w.signal_name).text(w.median)}{" "}
                          {format(w.signal_name).unit}</td>
                        <td className="num">{format(w.signal_name).text(w.band_low)} to{" "}
                          {format(w.signal_name).text(w.band_high)}</td>
                        <td className="num">{w.score?.toFixed(2)}</td></tr>))}</tbody>
                  </Table>
                </details>
              </Card>
            </div>
            <div className="case-side">
              <AssistantPanel caseId={id} actor={actor} canNote={d.actions.includes("note")}
                              onHighlight={highlight} />
              <Actions d={d} />
              <Card title="History" accent={false} as="section" sub="Wall-clock times.">
                <ol className="history" data-testid="history">{d.timeline.map((e) => (
                  <li key={e.event_id}><span className="num muted">
                    {e.recorded_at.slice(0, 19).replace("T", " ")}</span>{" "}
                    <span>{e.event_type} by {e.actor}{e.note && `: ${e.note}`}
                      {e.disposition && `: ${e.disposition} (${e.reason})`}</span>
                  </li>))}</ol>
              </Card>
              <Card title="Related cases" accent={false} as="section">
                {d.related.related_case || d.related.related_by.length ? <ul className="related">
                  {d.related.related_case && <li>Opened after closed case{" "}
                    <Link to={`/cases/${d.related.related_case.case_id}`}>
                      #{d.related.related_case.case_id}</Link> ({d.related.related_case.status})</li>}
                  {d.related.related_by.map((c) => <li key={c.case_id}>Followed by{" "}
                    <Link to={`/cases/${c.case_id}`}>#{c.case_id}</Link> ({c.status})</li>)}
                </ul> : <p className="muted">None: no case was closed and reopened here.</p>}
              </Card>
              <Export id={id} />
            </div>
          </div>
        </>}
      </Loading>
    </section>
  );
}
