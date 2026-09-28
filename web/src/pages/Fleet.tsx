import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { call, client } from "../api/client";
import type { Pump } from "../api/types";
import { Loading, Provenance, StateBadge, Synthetic, fmtTime } from "../components/common";

export function Fleet() {
  const q = useQuery({ queryKey: ["fleet"],
                       queryFn: () => call<{ pumps: Pump[] }>(client.GET("/api/fleet")) });
  return (
    <section>
      <h1>Fleet overview</h1>
      <Loading q={q}>
        <table>
          <thead><tr><th>pump</th><th>state</th><th>latest replay</th><th>open cases</th>
            <th>data quality</th><th>provenance</th></tr></thead>
          <tbody>
            {q.data?.pumps.map((p) => (
              <tr key={p.asset_id} data-testid={`pump-${p.asset_id}`}>
                <td><strong>{p.asset_id}</strong><br />
                  {p.days.map((d) => (
                    <Link key={d} to={`/assets/${p.asset_id}/${d}`}>{d} </Link>))}
                </td>
                <td><StateBadge state={p.state.state} />{p.state.reason &&
                  <div className="muted">{p.state.reason}</div>}</td>
                <td>{p.latest_session ? <>
                  #{p.latest_session.session_id} {p.latest_session.source_day}{" "}
                  {p.latest_session.status} at {p.latest_session.speed}x{" "}
                  <Synthetic show={p.latest_session.synthetic} />
                  <div className="muted">cursor {fmtTime(p.latest_session.cursor_at)}</div>
                </> : "none"}</td>
                <td><Link to={`/cases?asset_id=${p.asset_id}`}>
                  {p.open_cases.latest_session} in latest</Link>
                  <div className="muted">all sessions: {p.open_cases.all_sessions.real} real,{" "}
                    {p.open_cases.all_sessions.synthetic} synthetic</div></td>
                <td>{p.data_quality.status} ({p.data_quality.source_day})
                  <div className="muted">stale {p.data_quality.flag_counts.stale_suspected} ·
                    spike {p.data_quality.flag_counts.spike_suspected} · gaps{" "}
                    {p.data_quality.gaps ?? "-"}</div></td>
                <td><Synthetic show={p.synthetic} />
                  <Provenance model_version={p.model_version} assumptions={p.assumptions} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Loading>
    </section>
  );
}
