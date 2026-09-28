import { useQuery } from "@tanstack/react-query";
import { call, client } from "../api/client";
import { Loading } from "../components/common";

// cases_all_modes is the stored evaluation JSON (free-form in the contract); these are the
// fields this page reads from it
interface CaseSummary { cases?: number; cases_per_running_hour?: number;
                        case_time_fraction?: number; abstained?: string }

export function Evaluation() {
  const ev = useQuery({ queryKey: ["evaluation"],
                        queryFn: () => call(client.GET("/api/evaluation")) });
  const as = useQuery({ queryKey: ["assumptions"], queryFn: () =>
    call(client.GET("/api/assumptions")) });
  return (
    <section>
      <h1>Evaluation</h1>
      <Loading q={ev}>
        <p className="muted">{ev.data?.labels}</p>
        <ul>{Object.entries(ev.data?.modes ?? {}).map(([m, v]) => (
          <li key={m}><strong>{m}</strong>: {v.label} ({v.file}){v.note && ` — ${v.note}`}</li>))}
        </ul>
        <h2>REAL: cases for every mode (October)</h2>
        <table>
          <thead><tr><th>mode</th><th>pump</th><th>cases</th><th>per running hour</th>
            <th>running time in a case</th></tr></thead>
          <tbody>{Object.entries(ev.data?.cases_all_modes ?? {}).flatMap(([mode, pumps]) =>
            Object.entries(pumps as Record<string, CaseSummary>).map(([pump, c]) => (
              <tr key={`${mode}-${pump}`}><td>{mode}</td><td>{pump}</td>
                <td>{c.abstained ? "abstained" : c.cases}</td>
                <td>{c.cases_per_running_hour ?? "-"}</td>
                <td>{c.case_time_fraction != null
                  ? `${(c.case_time_fraction * 100).toFixed(1)}%` : "-"}</td></tr>)))}
          </tbody>
        </table>
      </Loading>
      <h2>Assumptions register</h2>
      <Loading q={as}>
        {as.data?.assumptions.map((a) => (
          <details key={a.id}><summary><strong>{a.id}</strong>: {a.title}</summary>
            <pre className="assumption">{a.assumption}</pre></details>))}
      </Loading>
    </section>
  );
}
