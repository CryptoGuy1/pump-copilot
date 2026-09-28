import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { call, client } from "../api/client";
import type { CaseList } from "../api/types";
import { Loading, Provenance, Synthetic, fmtTime } from "../components/common";

type Status = "open" | "acknowledged" | "dispositioned" | "closed";

export function Cases() {
  const [params, setParams] = useSearchParams();
  const query = {
    asset_id: params.get("asset_id") ?? undefined,
    session_id: params.get("session_id") ? Number(params.get("session_id")) : undefined,
    status: (params.get("status") as Status | null) ?? undefined,
  };
  const q = useQuery({ queryKey: ["cases", query],
                       queryFn: () => call<CaseList>(client.GET("/api/cases",
                                                                { params: { query } })) });
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next);
  };
  return (
    <section>
      <h1>Cases</h1>
      <p>
        status <select value={query.status ?? ""} onChange={(e) => set("status", e.target.value)}>
          <option value="">any</option>
          {["open", "acknowledged", "dispositioned", "closed"].map((s) =>
            <option key={s}>{s}</option>)}
        </select>{" "}
        session <input size={5} value={params.get("session_id") ?? ""}
                       onChange={(e) => set("session_id", e.target.value)} />{" "}
        asset <input size={12} value={query.asset_id ?? ""}
                     onChange={(e) => set("asset_id", e.target.value)} />
      </p>
      <Loading q={q}>
        {q.data && <>
          <p>{q.data.real_count} real · {q.data.synthetic_count} synthetic</p>
          <Provenance model_version={q.data.model_version} assumptions={q.data.assumptions} />
          <table>
            <thead><tr><th>case</th><th>data</th><th>asset · day · run</th><th>status</th>
              <th>evidence</th><th>signals</th><th>episodes</th></tr></thead>
            <tbody>
              {q.data.cases.map((c) => (
                <tr key={c.case_id} data-testid={`case-row-${c.case_id}`}>
                  <td><Link to={`/cases/${c.case_id}`}>#{c.case_id}</Link></td>
                  <td>{c.synthetic ? <Synthetic show /> : "real"}</td>
                  <td>{c.asset_id} · {c.source_day} · run {c.stretch}<div className="muted">
                    session #{c.session_id}</div></td>
                  <td>{c.status}{c.disposition && <div className="muted">{c.disposition}</div>}
                  </td>
                  <td>{fmtTime(c.evidence_start)}–{fmtTime(c.evidence_end)}</td>
                  <td>{c.signals.join(", ")}</td>
                  <td>{c.episodes}</td>
                </tr>))}
            </tbody>
          </table>
        </>}
      </Loading>
    </section>
  );
}
