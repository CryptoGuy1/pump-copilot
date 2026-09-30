import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, call, client } from "../api/client";
import { A8Notice, Loading, Synthetic, fmtTime } from "../components/common";
import { SignalName } from "../components/ui";

type Speed = 1 | 10 | 60;

function Progress({ id }: { id: number }) {
  const q = useQuery({ queryKey: ["baseline", id], queryFn: () =>
    call(
      client.GET("/api/replay/sessions/{session_id}/baseline",
                 { params: { path: { session_id: id } } })) });
  const runs = q.data?.baseline_progress?.runs ?? [];
  return (
    <Loading q={q}>
      <h2>Baseline progress, session #{id} <Synthetic show={q.data?.synthetic} /></h2>
      {!runs.length && <p className="muted">no run seen yet</p>}
      {runs.map((run) => (
        <div key={run.run}>
          <h3>run {run.run}: {fmtTime(run.start)}–{fmtTime(run.end)}
            {run.closed ? " (closed)" : " (running)"}</h3>
          <table data-testid={`progress-run-${run.run}`}>
            <thead><tr><th>signal</th><th>status</th><th>progress</th><th>settled</th>
              <th>baseline window</th><th>band / reason</th></tr></thead>
            <tbody>{Object.entries(run.signals).map(([sig, p]) => (
              <tr key={sig}>
                <td><SignalName id={sig} /></td>
                <td className={`baseline-${p.status}`}>{p.status.replace(/_/g, " ")}</td>
                <td><progress max={1} value={p.fraction} /> {Math.round(p.fraction * 100)}%</td>
                <td>{fmtTime(p.settled_at)}</td>
                <td>{fmtTime(p.baseline_start)}–{fmtTime(p.baseline_end)}</td>
                <td>{p.band ? `${p.band.low.toPrecision(4)} to ${p.band.high.toPrecision(4)} `
                  + p.band.unit : p.reason ?? ""}</td>
              </tr>))}</tbody>
          </table>
        </div>))}
    </Loading>
  );
}

export function Replay() {
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () =>
    call(client.GET("/api/replay/sessions")) });
  const assets = useQuery({ queryKey: ["assets"], queryFn: () =>
    call(
      client.GET("/api/assets")) });
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: () =>
    call(
      client.GET("/api/replay/scenarios")) });
  const [pick, setPick] = useState("cira-pump-B|2024-10-30");
  const [speed, setSpeed] = useState<Speed>(60);
  const [scenario, setScenario] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["sessions"] });
  const create = useMutation({
    mutationFn: () => {
      const [asset_id, source_day] = pick.split("|");
      return call(client.POST("/api/replay/sessions", { body: {
        asset_id, source_day, speed, scenario: scenario || null } }));
    },
    onSuccess: (r) => { setSelected(r.session.session_id); refresh(); },
  });
  const control = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "start" | "pause" | "rewind" }) => {
      const params = { params: { path: { session_id: id } } };
      if (action === "start")
        return call(client.POST("/api/replay/sessions/{session_id}/start", params));
      if (action === "pause")
        return call(client.POST("/api/replay/sessions/{session_id}/pause", params));
      return call(client.POST("/api/replay/sessions/{session_id}/rewind", params));
    },
    onSuccess: refresh,
  });
  const setRate = useMutation({
    mutationFn: ({ id, s }: { id: number; s: Speed }) => call(client.PUT(
      "/api/replay/sessions/{session_id}/speed",
      { params: { path: { session_id: id } }, body: { speed: s } })),
    onSuccess: refresh,
  });
  const err = (create.error ?? control.error ?? setRate.error) as ApiError | null;
  return (
    <section>
      <h1>Replay control</h1>
      <A8Notice />
      <fieldset>
        <legend>New replay</legend>
        <select aria-label="asset-day" value={pick} onChange={(e) => setPick(e.target.value)}>
          {assets.data?.asset_days.map((a) => (
            <option key={`${a.asset_id}|${a.source_day}`} value={`${a.asset_id}|${a.source_day}`}>
              {a.asset_id} {a.source_day}</option>))}
        </select>{" "}
        <select aria-label="speed" value={speed}
                onChange={(e) => setSpeed(Number(e.target.value) as Speed)}>
          {[1, 10, 60].map((s) => <option key={s} value={s}>{s}x</option>)}
        </select>{" "}
        <select aria-label="scenario" value={scenario} onChange={(e) => setScenario(e.target.value)}>
          <option value="">real data (no scenario)</option>
          {scenarios.data?.scenarios.map((s) => (
            <option key={s.name} value={s.name}>SYNTHETIC: {s.name}</option>))}
        </select>{" "}
        <button onClick={() => create.mutate()}>Start replay</button>
        <p className="muted">A new session is pending until a worker claims it (`make dev` runs
          one).</p>
      </fieldset>
      {err && <p className="error" role="alert">{err.status} {err.code}: {err.message}</p>}
      <Loading q={sessions}>
        <table>
          <thead><tr><th>session</th><th>asset · day</th><th>data</th><th>status</th>
            <th>cursor</th><th>speed</th><th>control</th></tr></thead>
          <tbody>{sessions.data?.sessions.map((s) => (
            <tr key={s.session_id} data-testid={`session-${s.session_id}`}
                className={selected === s.session_id ? "selected" : ""}>
              <td><button className="link" onClick={() => setSelected(s.session_id)}>
                #{s.session_id}</button></td>
              <td><Link to={`/assets/${s.asset_id}/${s.source_day}?session=${s.session_id}`}>
                {s.asset_id} · {s.source_day}</Link></td>
              <td>{s.synthetic ? <><Synthetic show /> {s.scenario}</> : "real"}</td>
              <td data-testid={`session-status-${s.session_id}`}>{s.status}</td>
              <td>{fmtTime(s.cursor_at)}</td>
              <td><select aria-label={`speed-${s.session_id}`} value={s.speed} onChange={(e) =>
                setRate.mutate({ id: s.session_id, s: Number(e.target.value) as Speed })}>
                {[1, 10, 60].map((x) => <option key={x} value={x}>{x}x</option>)}</select></td>
              <td>
                <button onClick={() => control.mutate({ id: s.session_id, action: "start" })}>
                  start</button>{" "}
                <button onClick={() => control.mutate({ id: s.session_id, action: "pause" })}>
                  pause</button>{" "}
                <button onClick={() => control.mutate({ id: s.session_id, action: "rewind" })}>
                  rewind</button>{" "}
                <Link to={`/cases?session_id=${s.session_id}`}>cases</Link>
              </td>
            </tr>))}</tbody>
        </table>
      </Loading>
      {selected != null && <Progress id={selected} />}
    </section>
  );
}
