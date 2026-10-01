import { useQuery } from "@tanstack/react-query";
import { usePageTitle } from "../hooks/usePageTitle";
import { Link, useSearchParams } from "react-router-dom";
import { call, client } from "../api/client";
import { Loading, Synthetic, fmtTime } from "../components/common";
import { SignalName } from "../components/ui";
import { useArrivals } from "../hooks/useArrivals";
import { usePageProvenance } from "../pageProvenance";

type Status = "open" | "acknowledged" | "dispositioned" | "closed";

export function Cases() {
  usePageTitle("Cases");
  const [params, setParams] = useSearchParams();
  const query = {
    asset_id: params.get("asset_id") ?? undefined,
    session_id: params.get("session_id") ? Number(params.get("session_id")) : undefined,
    status: (params.get("status") as Status | null) ?? undefined,
  };
  const q = useQuery({ queryKey: ["cases", query],
                       queryFn: () => call(client.GET("/api/cases",
                                                                { params: { query } })) });
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next);
  };
  const arrived = useArrivals(q.data?.cases.map((c) => c.case_id));
  usePageProvenance(q.data && {
    synthetic: q.data.synthetic_count && q.data.real_count ? "mixed" : q.data.synthetic_count > 0,
    model_version: q.data.model_version, assumptions: q.data.assumptions });
  return (
    <section>
      <h1>Cases</h1>
      <p>
        <label>status <select value={query.status ?? ""}
                              onChange={(e) => set("status", e.target.value)}>
          <option value="">any</option>
          {["open", "acknowledged", "dispositioned", "closed"].map((s) =>
            <option key={s}>{s}</option>)}
        </select></label>{" "}
        <label>session <input size={5} value={params.get("session_id") ?? ""}
                              onChange={(e) => set("session_id", e.target.value)} /></label>{" "}
        <label>asset <input size={12} value={query.asset_id ?? ""}
                            onChange={(e) => set("asset_id", e.target.value)} /></label>
      </p>
      <Loading q={q}>
        {q.data && <>
          <p>{q.data.real_count} real · {q.data.synthetic_count} synthetic</p>
          <table>
            <thead><tr><th>case</th><th>data</th><th>asset · day · run</th><th>status</th>
              <th>evidence</th><th>signals</th><th>episodes</th></tr></thead>
            <tbody>
              {q.data.cases.map((c) => (
                <tr key={c.case_id} data-testid={`case-row-${c.case_id}`}
                    className={arrived.has(c.case_id) ? "enter" : undefined}>
                  <td><Link to={`/cases/${c.case_id}`}>#{c.case_id}</Link></td>
                  <td>{c.synthetic ? <Synthetic show /> : "real"}</td>
                  <td>{c.asset_id} · {c.source_day} · run {c.stretch + 1}<div className="muted">
                    session #{c.session_id}</div></td>
                  <td>{c.status}{c.disposition && <div className="muted">{c.disposition}</div>}
                  </td>
                  <td>{fmtTime(c.evidence_start)}–{fmtTime(c.evidence_end)}</td>
                  <td>{(c.signals ?? []).map((s, i) => <span key={s}>{i > 0 && ", "}
                    <SignalName id={s} /></span>)}</td>
                  <td>{c.episodes}</td>
                </tr>))}
            </tbody>
          </table>
        </>}
      </Loading>
    </section>
  );
}
