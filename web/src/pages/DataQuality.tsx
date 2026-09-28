import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { call, client } from "../api/client";
import { Loading } from "../components/common";

export function DataQuality() {
  const q = useQuery({ queryKey: ["data-quality"],
                       queryFn: () => call(client.GET("/api/data-quality")) });
  return (
    <section>
      <h1>Data quality</h1>
      <Loading q={q}>
        <p>audit: {q.data?.audit_ok == null ? "not run" : q.data.audit_ok ? "ok" : "issues"}</p>
        {!!q.data?.audit_issues?.length && <ul>{q.data.audit_issues.map((i) =>
          <li key={i}>{i}</li>)}</ul>}
        <table>
          <thead><tr><th>asset · day</th><th>readings</th><th>stale suspected</th>
            <th>spike suspected</th><th>audit issues</th><th>gaps</th></tr></thead>
          <tbody>{q.data?.asset_days.map((a) => (
            <tr key={`${a.asset_id}-${a.source_day}`}>
              <td><Link to={`/assets/${a.asset_id}/${a.source_day}`}>
                {a.asset_id} · {a.source_day}</Link></td>
              <td>{a.flag_counts.readings}</td><td>{a.flag_counts.stale_suspected}</td>
              <td>{a.flag_counts.spike_suspected}</td><td>{a.audit_issues ?? "-"}</td>
              <td>{a.gaps ?? "-"}</td></tr>))}</tbody>
        </table>
      </Loading>
    </section>
  );
}
