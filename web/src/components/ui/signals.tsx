import { useQuery } from "@tanstack/react-query";
import { call, client } from "../../api/client";

/** The names people see for signals (GET /api/signal-names, from data/cira_columns.yaml). */
export function useSignalNames() {
  return useQuery({ queryKey: ["signal-names"], staleTime: Infinity,
                    queryFn: () => call(client.GET("/api/signal-names")) });
}

/** A signal by its display name (or short name), with its technical id in a tooltip. Falls
 * back to the id while the names load or for a signal the map does not know. */
export function SignalName({ id, short = false }: { id: string; short?: boolean }) {
  const n = useSignalNames().data?.signals[id];
  const label = n ? (short ? n.short_name : n.display_name) : id;
  return <span className="signal-name" title={id} data-signal={id}>{label}</span>;
}

/** The display name as text (for chart titles and labels that cannot hold an element). */
export function useSignalLabel() {
  const names = useSignalNames().data?.signals;
  return (id: string, short = false) => {
    const n = names?.[id];
    return n ? (short ? n.short_name : n.display_name) : id;
  };
}
