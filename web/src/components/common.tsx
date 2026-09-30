import { useState } from "react";
import { ProvenanceStrip, useAssumptions } from "./ui";

export { Loading, StateBadge, Synthetic } from "./ui";

/** A provenance line inside a page (the shell also shows the page's provenance). */
export function Provenance({ model_version, assumptions, synthetic = false }:
                           { model_version: string[]; assumptions: string[];
                             synthetic?: boolean | "mixed" }) {
  return <ProvenanceStrip p={{ model_version, assumptions, synthetic }} />;
}

/** The A8 notice on replay screens: what replay takes from the whole stored day. */
export function A8Notice() {
  const a8 = useAssumptions().data?.assumptions.find((a) => a.id === "A8");
  return (
    <aside className="notice" data-testid="a8-notice">
      <strong>A8: {a8?.title ?? "Replay uses declared per-day constants and stored ingest"
                                   + " annotations"}</strong>
      <p>
        Replay scores only readings at or before its cursor, except for two declared inputs
        taken from the whole stored day: each signal's median reading interval (it sets the
        window length) and the ingest annotations (operating state, stale and spike flags).
        Batch 3a-3 uses the same, so replay equals batch; a live system would not know them
        in advance.
      </p>
    </aside>
  );
}

export const fmtTime = (t: string | null | undefined) =>
  t ? new Date(t).toISOString().slice(11, 19) : "-";

/** Who is acting (remembered in this browser). */
export function useActor(): [string, (s: string) => void] {
  const [actor, setActor] = useState(() => localStorage.getItem("actor") ?? "operator");
  return [actor, (s: string) => { localStorage.setItem("actor", s); setActor(s); }];
}
