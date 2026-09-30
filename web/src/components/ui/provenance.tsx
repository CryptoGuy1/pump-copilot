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
  model_version: string[];
  assumptions: string[];
}

/** Where the data on screen comes from: real or SYNTHETIC, the model versions that produced
 * it and the assumptions that apply (each opening the register). */
export function ProvenanceStrip({ p, label = "Provenance" }:
                                { p: ProvenanceData; label?: string }) {
  return (
    <div className="provenance-strip" data-testid="provenance-strip">
      <span className="label">{label}</span>
      {p.synthetic === true ? <Synthetic show />
        : <span>{p.synthetic === "mixed" ? "real and SYNTHETIC (each marked)" : "real data"}</span>}
      <span>model <span className="mono">
        {p.model_version.length ? p.model_version.join(", ") : "none"}</span></span>
      <span className="chips" role="group" aria-label="assumptions">
        {p.assumptions.map((a) => <AssumptionChip key={a} id={a} />)}</span>
    </div>
  );
}
