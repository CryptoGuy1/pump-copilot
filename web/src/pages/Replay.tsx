import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, type Schema, call, client } from "../api/client";
import { A8Notice, fmtTime } from "../components/common";
import { Button, Card, EmptyState, Loading, SignalName, Synthetic, Table } from "../components/ui";
import { usePageTitle } from "../hooks/usePageTitle";

type Speed = 1 | 10 | 60;
type Session = Schema<"SessionList">["sessions"][number];
const secs = (t: string) => Date.parse(t) / 1000;
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");

/** Where the cursor is in the session's source day, 0 to 1. */
const progressOf = (s: Session) => s.cursor_at
  ? Math.min(1, Math.max(0, (secs(s.cursor_at) - secs(s.source_start))
    / Math.max(1, secs(s.source_end) - secs(s.source_start)))) : 0;

function Progress({ id }: { id: number }) {
  const q = useQuery({ queryKey: ["baseline", id], queryFn: () => call(client.GET(
    "/api/replay/sessions/{session_id}/baseline", { params: { path: { session_id: id } } })) });
  const runs = q.data?.baseline_progress?.runs ?? [];
  return (
    <Card as="section" accent={false}
          title={<>Baseline progress, session #{id} <Synthetic show={q.data?.synthetic} /></>}
          sub="Per signal: settling after the run start (A7), then forming its band, then formed.">
      <Loading q={q} lines={4}>
        {!runs.length && <EmptyState title="No run seen yet">The cursor has not reached a
          running period of this day.</EmptyState>}
        {runs.map((run) => (
          <div key={run.run} className="run-block">
            <h3>Run {run.run + 1}: <span className="num">{fmtTime(run.start)}–{fmtTime(run.end)}</span>{" "}
              UTC {run.closed ? "(closed)" : "(running)"}</h3>
            <ul className="baseline-list" data-testid={`progress-run-${run.run}`}>
              {Object.entries(run.signals).map(([sig, p]) => (
                <li key={sig} className={`bl bl-${p.status}`}>
                  <SignalName id={sig} />
                  <span className="bl-status">{cap(p.status)}</span>
                  <span className="bl-track" role="progressbar" aria-valuemin={0}
                        aria-valuemax={100} aria-valuenow={Math.round(p.fraction * 100)}
                        aria-label={`${sig} baseline`}>
                    <span style={{ width: `${p.fraction * 100}%` }} /></span>
                  <span className="num">{Math.round(p.fraction * 100)}%</span>
                  <span className="muted bl-detail">{p.band
                    ? `band ${p.band.low.toPrecision(3)} to ${p.band.high.toPrecision(3)}`
                    : p.reason ?? (p.settled_at ? `settled ${fmtTime(p.settled_at)}` : "")}</span>
                </li>))}
            </ul>
          </div>))}
      </Loading>
    </Card>
  );
}

function WorkerHealth() {
  const q = useQuery({ queryKey: ["health"], refetchInterval: 5000,
                       queryFn: () => call(client.GET("/api/health")) });
  const h = q.data;
  const w = h?.worker.workers[0];
  return (
    <Card as="section" accent={false} title="Worker" sub="The replay worker advances cursors.">
      <Loading q={q} lines={2}>
        {h && <div className="health">
          <span className="live" data-status={h.worker.alive ? "open" : "closed"}>
            <span className="live-dot" aria-hidden="true" />
            {h.worker.alive ? "A worker is running" : "No worker is running"}</span>
          <p className="muted">{w ? <>Last seen {fmtTime(w.last_seen)} UTC ({w.worker_id},
            {" "}{w.status}); stale after {h.worker.stale_after_s} s.</>
            : "No worker has reported yet."} {!h.worker.alive && <>New and resumed replays wait
            until one runs (<span className="mono">pumpcopilot worker</span>).</>}</p>
          <p className="muted">Database {h.database.ok ? "reachable" : "unreachable"}
            {h.database.latency_ms != null && <> ({h.database.latency_ms.toFixed(1)} ms)</>}.</p>
        </div>}
      </Loading>
    </Card>
  );
}

export function Replay() {
  usePageTitle("Replay");
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () =>
    call(client.GET("/api/replay/sessions")) });
  const assets = useQuery({ queryKey: ["assets"], queryFn: () => call(client.GET("/api/assets")) });
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: () =>
    call(client.GET("/api/replay/scenarios")) });
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
    <section className="replay">
      <header className="masthead panel">
        <div className="masthead-accent" aria-hidden="true" />
        <div>
          <p className="eyebrow">Replay control</p>
          <h1>Replay</h1>
          <p className="lede">Replay a stored pump-day at 1x, 10x or 60x. Scores and cases
            appear as the cursor passes them; a scenario injects a SYNTHETIC fault in memory.</p>
        </div>
      </header>
      <A8Notice />
      <div className="replay-top">
        <Card as="section" accent={false} title="New replay">
          <div className="fields">
            <label className="field"><span className="field-label">Pump and day</span>
              <select aria-label="asset-day" value={pick} onChange={(e) => setPick(e.target.value)}>
                {assets.data?.asset_days.map((a) => (
                  <option key={`${a.asset_id}|${a.source_day}`}
                          value={`${a.asset_id}|${a.source_day}`}>
                    {a.asset_id} {a.source_day}</option>))}
              </select></label>
            <label className="field"><span className="field-label">Speed</span>
              <select aria-label="speed" value={speed}
                      onChange={(e) => setSpeed(Number(e.target.value) as Speed)}>
                {[1, 10, 60].map((s) => <option key={s} value={s}>{s}x</option>)}
              </select></label>
            <label className="field"><span className="field-label">Scenario</span>
              <select aria-label="scenario" value={scenario}
                      onChange={(e) => setScenario(e.target.value)}>
                <option value="">Real data (no scenario)</option>
                {scenarios.data?.scenarios.map((s) => (
                  <option key={s.name} value={s.name}>SYNTHETIC: {s.name}</option>))}
              </select></label>
          </div>
          <div className="btn-row">
            <Button variant="primary" onClick={() => create.mutate()}
                    disabled={create.isPending}>Start replay</Button>
            <span className="muted">A new session waits until a worker claims it.</span>
          </div>
        </Card>
        <WorkerHealth />
      </div>
      {err && <p className="error" role="alert">{err.status} {err.code}: {err.message}</p>}
      <Card as="section" accent={false} title="Sessions" sub="Newest first; the cursor moves live.">
        <Loading q={sessions} lines={4}>
          {sessions.data && (sessions.data.sessions.length === 0
            ? <EmptyState title="No replays yet">Start one above.</EmptyState>
            : <Table label="Replay sessions">
                <thead><tr><th>Session</th><th>Pump · day</th><th>Data</th><th>Status</th>
                  <th>Cursor (UTC)</th><th>Speed</th><th>Control</th></tr></thead>
                <tbody>{sessions.data.sessions.map((s) => (
                  <tr key={s.session_id} data-testid={`session-${s.session_id}`}
                      className={selected === s.session_id ? "selected" : undefined}>
                    <td><button className="link" onClick={() => setSelected(s.session_id)}>
                      #{s.session_id}</button></td>
                    <td><Link to={`/assets/${s.asset_id}/${s.source_day}?session=${s.session_id}`}>
                      <span className="mono">{s.asset_id}</span> · {s.source_day}</Link></td>
                    <td>{s.synthetic ? <><Synthetic show /> <span className="mono">
                      {s.scenario}</span></> : "Real"}</td>
                    <td><span className="status-pill" data-testid={`session-status-${s.session_id}`}>
                      {s.status}</span></td>
                    <td className="cursor-cell"><span className="num">{fmtTime(s.cursor_at)}</span>
                      <span className="share-bar" role="progressbar" aria-valuemin={0}
                            aria-valuemax={100} aria-valuenow={Math.round(progressOf(s) * 100)}
                            aria-label={`session ${s.session_id} progress through the day`}>
                        <span style={{ width: `${progressOf(s) * 100}%` }} /></span></td>
                    <td><select aria-label={`speed-${s.session_id}`} value={s.speed}
                                onChange={(e) => setRate.mutate({ id: s.session_id,
                                  s: Number(e.target.value) as Speed })}>
                      {[1, 10, 60].map((x) => <option key={x} value={x}>{x}x</option>)}</select></td>
                    <td><div className="btn-row">
                      <Button size="sm" onClick={() => control.mutate({ id: s.session_id,
                        action: "start" })} disabled={!["paused", "pending"].includes(s.status)}>
                        {s.status === "paused" ? "resume" : "start"}</Button>
                      <Button size="sm" onClick={() => control.mutate({ id: s.session_id,
                        action: "pause" })} disabled={!["running", "pending"].includes(s.status)}>
                        pause</Button>
                      <Button size="sm" onClick={() => control.mutate({ id: s.session_id,
                        action: "rewind" })}>rewind</Button>
                      <Link to={`/cases?session_id=${s.session_id}`}>cases</Link>
                    </div></td>
                  </tr>))}</tbody>
              </Table>)}
        </Loading>
      </Card>
      {selected != null && <Progress id={selected} />}
    </section>
  );
}
