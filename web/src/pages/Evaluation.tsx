import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { type Schema, call, client } from "../api/client";
import { Loading } from "../components/common";

// cases_all_modes is the stored evaluation JSON (free-form in the contract); these are the
// fields this page reads from it
interface CaseSummary { cases?: number; cases_per_running_hour?: number;
                        case_time_fraction?: number; abstained?: string }

// The benchmark's config and results are stored JSON (free-form in the contract); these are the
// fields this page reads from them.
interface Selected { params: Record<string, unknown>; val_macro_f1: number }
interface Interval { lo: number | null; hi: number | null }
interface ModelResult {
  test: { macro_f1: number; recall: Record<string, number | null> };
  ci95: { macro_f1: Interval; n_blocks: number };
  calibration: { brier: number; ece_top_label: number };
}

// the same plain-language grade as the API and the report (zema_bench.calibration_grade)
const grade = (ece: number) =>
  ece < 0.05 ? "good" : ece < 0.1 ? "fair" : ece < 0.2 ? "poor" : "very poor";

function Calibration({ c }: { c: Schema<"ZemaScore">["calibration"] }) {
  if (!c) return <>calibration measured (values not available)</>;
  return <span title={c.text}>calibration measured: <strong>{c.grade}</strong>
    <div className="muted">Brier {c.brier.toFixed(3)} · ECE {c.ece.toFixed(3)}</div></span>;
}
const SPLITS: [string, string][] = [
  ["chronological", "(c) chronological, 50-cycle gaps: PRIMARY"],
  ["grouped", "(b) grouped by leakage run"], ["random", "(a) stratified random (naive)"]];
const MODELS = ["majority", "logreg", "gradient_boosting", "shortcut_stable_flag",
                "conditions_only"];

function Zema({ z }: { z: Schema<"ZemaBenchmark"> }) {
  const selected = (z.config?.frozen as { selected?: Record<string, Record<string, Selected>> })
    ?.selected ?? {};
  const results = (z.results as { splits?: Record<string, Record<string, ModelResult>> } | null)
    ?.splits;
  const f = (x: number | null | undefined) => (x == null ? "-" : x.toFixed(3));
  return (
    <section data-testid="zema-benchmark">
      <h2>ZeMA benchmark: hydraulic test rig pump leakage</h2>
      <aside className="notice"><strong>Hydraulic test rig only.</strong> {z.scope_note}</aside>
      <p>status: <strong>{z.status}</strong> · pre-registration {z.preregistration_tag} ·
        output labels read "{z.output_label}" · report {z.report}</p>
      <table>
        <thead><tr><th>split</th>{MODELS.map((m) => <th key={m}>{m}</th>)}</tr></thead>
        <tbody>{SPLITS.map(([split, title]) => (
          <tr key={split}><td>{title}</td>{MODELS.map((m) => {
            const r = results?.[split]?.[m];
            const v = selected[split]?.[m];
            return <td key={m}>{r ? <>{f(r.test.macro_f1)}<div className="muted">
              95% {f(r.ci95.macro_f1.lo)}–{f(r.ci95.macro_f1.hi)} over {r.ci95.n_blocks} runs ·
              calibration {grade(r.calibration.ece_top_label)}: Brier {f(r.calibration.brier)}
              · ECE {f(r.calibration.ece_top_label)}</div></> : v ? <>{f(v.val_macro_f1)}
              <div className="muted">validation</div></> : "-"}</td>;
          })}</tr>))}</tbody>
      </table>
      {z.config?.headline != null && <p>headline model per split (pre-registered):{" "}
        {Object.entries(z.config.headline as Record<string, string>).map(([k, v]) =>
          `${k}: ${v}`).join(" · ")}</p>}
      {z.scores.length > 0 && <>
        <h3>Headline-model scores, first test cycles</h3>
        <p className="muted">ZeMA cycles have no clock: they are shown by cycle number. The
          time window in each score is a placeholder (cycle × 60 s), marked
          time_is_placeholder.</p>
        <table data-testid="zema-scores">
          <thead><tr><th>cycle</th><th>split</th><th>model</th><th>output</th>
            <th>probability</th><th>calibration</th></tr></thead>
          <tbody>{z.scores.map((s) => (
            <tr key={`${s.split}-${s.evidence.cycle_id}`}>
              <td>cycle {s.evidence.cycle_id}</td><td>{s.split}</td><td>{s.model}</td>
              <td>{s.evidence.output_label}</td><td>{s.evidence.score?.toFixed(2)}</td>
              <td>{s.evidence.confidence_calibration_status === "calibration_measured"
                ? <Calibration c={s.calibration} />
                : s.evidence.confidence_calibration_status.replace(/_/g, " ")}</td></tr>))}
          </tbody>
        </table>
      </>}
      <p className="muted">{results ? "Test macro-F1, evaluated once after the "
        + "pre-registration." : "Validation macro-F1 of the frozen settings; the test parts "
        + "are not yet evaluated."} shortcut_stable_flag uses the stable flag alone;
        conditions_only uses the four other condition labels, which are not observable in
        practice: both show the confounding.</p>
    </section>
  );
}

export function Evaluation() {
  const ev = useQuery({ queryKey: ["evaluation"],
                        queryFn: () => call(client.GET("/api/evaluation")) });
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
      {ev.data && <Zema z={ev.data.zema} />}
      <h2>Assumptions register</h2>
      <p>The assumptions behind every score and case: <Link to="/assumptions">open the
        register</Link>.</p>
    </section>
  );
}
