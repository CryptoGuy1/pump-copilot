import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, call, client } from "../api/client";
import type { CaseDetail as Detail, CaseState, EvidencePage } from "../api/types";
import { Chart, toSeconds } from "../components/Chart";
import { A8Notice, Loading, Provenance, Synthetic, fmtTime }
  from "../components/common";

const DISPOSITIONS = ["monitor", "known condition, no action", "data quality issue",
                      "escalate to reliability engineer (export only)"] as const;
type Disposition = (typeof DISPOSITIONS)[number];

function useActor(): [string, (s: string) => void] {
  const [actor, setActor] = useState(() => localStorage.getItem("actor") ?? "operator");
  return [actor, (s: string) => { localStorage.setItem("actor", s); setActor(s); }];
}

function RelatedLink({ c }: { c: CaseState }) {
  return <Link to={`/cases/${c.case_id}`}>#{c.case_id} ({c.status})</Link>;
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
  const can = (a: string) => d.actions.includes(a);
  const err = act.error as ApiError | null;
  return (
    <fieldset className="actions">
      <legend>Actions</legend>
      <label>acting as <input value={actor} onChange={(e) => setActor(e.target.value)} /></label>
      <div>
        <button disabled={!can("acknowledge")} onClick={() => act.mutate("acknowledge")}>
          Acknowledge</button>
      </div>
      <div>
        <input placeholder="note" value={note} onChange={(e) => setNote(e.target.value)} />
        <button disabled={!can("note") || !note.trim()} onClick={() => act.mutate("note")}>
          Add note</button>
      </div>
      <div>
        <select aria-label="disposition" value={disp}
                onChange={(e) => setDisp(e.target.value as Disposition)}>
          {DISPOSITIONS.map((x) => <option key={x}>{x}</option>)}
        </select>
        <input placeholder="reason (required)" value={reason}
               onChange={(e) => setReason(e.target.value)} />
        <button disabled={!can("disposition") || !reason.trim()}
                onClick={() => act.mutate("disposition")}>Set disposition</button>
      </div>
      <div>
        <button disabled={!can("close")} onClick={() => act.mutate("close")}>Close case</button>
      </div>
      {err && <p className="error" role="alert">{err.status} {err.code}: {err.message}</p>}
      <p className="muted">Escalation is export-only: nothing is sent anywhere.</p>
    </fieldset>
  );
}

function Evidence({ id }: { id: number }) {
  const q = useInfiniteQuery({
    queryKey: ["evidence", id], initialPageParam: 0,
    queryFn: ({ pageParam }) => call<EvidencePage>(client.GET("/api/cases/{case_id}/evidence",
      { params: { path: { case_id: id }, query: { offset: pageParam, limit: 100 } } })),
    getNextPageParam: (p) => p.next_offset ?? undefined,
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <details>
      <summary>All evidence windows ({q.data?.pages[0]?.total ?? "…"})</summary>
      <table>
        <thead><tr><th>window</th><th>signal</th><th>median</th><th>band</th><th>score</th>
          </tr></thead>
        <tbody>{items.map((w) => (
          <tr key={`${w.signal_name}-${w.window_end}`}>
            <td>{fmtTime(w.window_start)}–{fmtTime(w.window_end)}</td><td>{w.signal_name}</td>
            <td>{w.median?.toPrecision(5)}</td>
            <td>{w.band_low?.toPrecision(5)} to {w.band_high?.toPrecision(5)}</td>
            <td>{w.score?.toFixed(2)}</td></tr>))}</tbody>
      </table>
      {q.hasNextPage && <button onClick={() => q.fetchNextPage()}>Load more</button>}
    </details>
  );
}

function Export({ id }: { id: number }) {
  const [md, setMd] = useState<string | null>(null);
  const load = async () => {
    const r = await fetch(`/api/cases/${id}/export?format=markdown`);
    setMd(await r.text());
  };
  return (
    <section>
      <h2>Export</h2>
      <a href={`/api/cases/${id}/export`} target="_blank" rel="noreferrer">JSON</a>{" · "}
      <a href={`/api/cases/${id}/export?format=markdown`} target="_blank" rel="noreferrer">
        Markdown evidence pack</a>{" · "}
      <button onClick={load}>Preview evidence pack</button>
      {md && <pre className="export" data-testid="export-preview">{md}</pre>}
    </section>
  );
}

export function CaseDetail() {
  const id = Number(useParams().id);
  const q = useQuery({ queryKey: ["case", id], queryFn: () =>
    call<Detail>(client.GET("/api/cases/{case_id}", { params: { path: { case_id: id } } })) });
  const d = q.data;
  return (
    <section>
      <Loading q={q}>
        {d && <>
          <h1>Case #{id} <Synthetic show={d.case.synthetic} />{" "}
            <span data-testid="case-status">{d.case.status}</span></h1>
          <p>{d.case.asset_id} · <Link to={`/assets/${d.case.asset_id}/${d.case.source_day}` +
            `?session=${d.case.session_id}`}>{d.case.source_day}</Link> · run {d.case.stretch} ·
            session #{d.case.session_id} · evidence {fmtTime(d.case.evidence_start)}–
            {fmtTime(d.case.evidence_end)} · {d.case.episodes} episodes,{" "}
            {d.case.evidence_windows} windows</p>
          {d.case.disposition && <p>disposition: <strong>{d.case.disposition}</strong> —{" "}
            {d.case.disposition_reason}</p>}
          <Provenance model_version={d.model_version} assumptions={d.assumptions} />
          <A8Notice />
          {(d.related.related_case || d.related.related_by.length > 0) && <p>
            related: {d.related.related_case && <>opened after closed case{" "}
              <RelatedLink c={d.related.related_case} /></>}
            {d.related.related_by.map((c) => <span key={c.case_id}> followed by{" "}
              <RelatedLink c={c} /></span>)}</p>}
          <Actions d={d} />
          <h2>Signals against their band</h2>
          <table>
            <thead><tr><th>signal</th><th>windows</th><th>episodes</th><th>first–last</th>
              <th>max score</th><th>band</th></tr></thead>
            <tbody>{Object.entries(d.signals).map(([sig, s]) => (
              <tr key={sig}><td>{sig}</td><td>{s.summary.windows}</td><td>{s.summary.episodes}</td>
                <td>{fmtTime(s.summary.first_window_start)}–{fmtTime(s.summary.last_window_end)}</td>
                <td>{s.summary.max_score?.toFixed(2)}</td>
                <td>{s.band ? `${s.band.low.toPrecision(5)} to ${s.band.high.toPrecision(5)} ` +
                  s.band.unit : "-"}</td></tr>))}</tbody>
          </table>
          {Object.entries(d.signals).map(([sig, s]) => {
            const n = s.chart.t.length;
            return <Chart key={sig} title={sig} x={toSeconds(s.chart.t)} series={[
              { label: "median", values: s.chart.median, color: "#2060c0" },
              { label: "min", values: s.chart.min, color: "#9ab", dash: [2, 3] },
              { label: "max", values: s.chart.max, color: "#9ab", dash: [2, 3] },
              { label: "band low", values: Array(n).fill(s.band?.low ?? null), color: "#999",
                dash: [4, 4] },
              { label: "band high", values: Array(n).fill(s.band?.high ?? null), color: "#999",
                dash: [4, 4] },
              { label: "reviewed", points: true, color: "#c02020",
                values: s.chart.review.map((r, i) => (r ? s.chart.median[i] : null)) },
            ]} />;
          })}
          <Evidence id={id} />
          <h2>Timeline</h2>
          <ul>{d.timeline.map((e) => (
            <li key={e.event_id}>{e.recorded_at.slice(0, 19)} {e.event_type} by {e.actor}
              {e.note && `: ${e.note}`}{e.disposition && `: ${e.disposition} (${e.reason})`}
            </li>))}</ul>
          <Export id={id} />
        </>}
      </Loading>
    </section>
  );
}
