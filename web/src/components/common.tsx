import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { call, client } from "../api/client";

/** Shown wherever data is synthetic (an injected, in-memory fault). */
export function Synthetic({ show }: { show: boolean | null | undefined }) {
  if (!show) return null;
  return <span className="synthetic" data-testid="synthetic-label">SYNTHETIC</span>;
}

export function StateBadge({ state }: { state: string }) {
  return <span className={`state state-${state}`}>{String(state).replace(/_/g, " ")}</span>;
}

export function Provenance({ model_version, assumptions }:
                           { model_version: string[]; assumptions: string[] }) {
  return (
    <p className="provenance">
      model version {model_version.length ? model_version.join(", ") : "-"} · assumptions{" "}
      {assumptions.join(", ")}
    </p>
  );
}

function useAssumptions() {
  return useQuery({
    queryKey: ["assumptions"], staleTime: Infinity,
    queryFn: () => call(client.GET("/api/assumptions")),
  });
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

export function Loading({ q, children }: { q: { isLoading: boolean; error: unknown };
                                         children: ReactNode }) {
  if (q.isLoading) return <p>loading…</p>;
  if (q.error) return <p className="error">{String((q.error as Error).message)}</p>;
  return <>{children}</>;
}

export const fmtTime = (t: string | null | undefined) =>
  t ? new Date(t).toISOString().slice(11, 19) : "-";

/** Who is acting (remembered in this browser). */
export function useActor(): [string, (s: string) => void] {
  const [actor, setActor] = useState(() => localStorage.getItem("actor") ?? "operator");
  return [actor, (s: string) => { localStorage.setItem("actor", s); setActor(s); }];
}
