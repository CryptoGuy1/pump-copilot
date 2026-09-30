import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { call, client } from "../../api/client";
import { Synthetic } from "./state";

/** The assumptions register, fetched once (titles for chips, entries for /assumptions). */
export function useAssumptions() {
  return useQuery({ queryKey: ["assumptions"], staleTime: Infinity,
                    queryFn: () => call(client.GET("/api/assumptions")) });
}

/** One assumption ID; opens its entry in the assumptions register. */
export function AssumptionChip({ id }: { id: string }) {
  const title = useAssumptions().data?.assumptions.find((a) => a.id === id)?.title;
  return (
    <Link className="chip" to={`/assumptions#${id}`} title={title}
          aria-label={title ? `${id}: ${title}` : `assumption ${id}`}>{id}</Link>
  );
}

export interface ProvenanceData {
  synthetic: boolean | "mixed";
  scenarios?: number;  // with "mixed": how many synthetic scenarios the page shows
  model_version: string[];
  assumptions: string[];
}

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** Where the page's data comes from, in plain words. */
export function provenanceSentence(p: ProvenanceData): string {
  const what = p.synthetic === true ? "Synthetic scenario on stored CIRA data"
    : p.synthetic === "mixed"
      ? `Real CIRA data + ${plural(p.scenarios ?? 1, "synthetic scenario")}`
      : "Real CIRA data";
  return `${what} · read-only`;
}

/** The chips and model versions behind a provenance line. */
function Details({ p }: { p: ProvenanceData }) {
  return (
    <div className="prov-more">
      <span>model <span className="mono">
        {p.model_version.length ? p.model_version.join(", ") : "none"}</span></span>
      <span className="chips" role="group" aria-label="assumptions">
        {p.assumptions.map((a) => <AssumptionChip key={a} id={a} />)}</span>
    </div>
  );
}

/** One quiet line ("Real data · model b400e37 · 6 assumptions") that expands to the chips:
 * the foot of a card. */
export function ProvenanceLine({ p }: { p: ProvenanceData }) {
  const model = p.model_version.length
    ? p.model_version.map((v) => v.slice(0, 7)).join(", ") : "none";
  return (
    <details className="prov-line" data-testid="provenance-line">
      <summary>{p.synthetic === true ? <Synthetic show /> : <span>Real data</span>}
        <span aria-hidden="true">·</span><span>model <span className="mono">{model}</span></span>
        <span aria-hidden="true">·</span><span>{plural(p.assumptions.length, "assumption")}</span>
      </summary>
      <Details p={p} />
    </details>
  );
}

/** The page's provenance in plain words, expanding to the details. */
export function ProvenanceStrip({ p }: { p: ProvenanceData }) {
  return (
    <details className="prov-line provenance-strip" data-testid="provenance-strip">
      <summary>{p.synthetic === true && <Synthetic show />}
        <span className="prov-sentence">{provenanceSentence(p)}</span></summary>
      <Details p={p} />
    </details>
  );
}
