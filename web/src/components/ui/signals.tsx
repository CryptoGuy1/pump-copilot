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

/** Display units, one form everywhere: vibration velocity in mm/s (stored in m/s),
 * acceleration in m/s², temperatures in °C and temperature differences in K. */
export function displayUnit(unit: string | undefined, signal = ""): { unit: string;
                                                                     factor: number } {
  if (signal.endsWith("_rel_ambient")) return { unit: "K", factor: 1 };
  switch (unit) {
    case "m/s": return { unit: "mm/s", factor: 1000 };
    case "m/s^2": return { unit: "m/s²", factor: 1 };
    case "degC": return { unit: "°C", factor: 1 };
    default: return { unit: unit ?? "", factor: 1 };
  }
}

/** About three significant figures, the way values are read (tabular in the UI). */
export const sig3 = (v: number | null | undefined) =>
  v == null ? "-" : Math.abs(v) >= 1000 ? v.toFixed(0) : Number(v.toPrecision(3)).toString();

/** A signal's display unit and a converter for its values. */
export function useSignalFormat() {
  const names = useSignalNames().data?.signals;
  return (signal: string) => {
    const d = displayUnit(names?.[signal]?.unit, signal);
    const one = (v: number | null | undefined) => (v == null ? null : v * d.factor);
    return { unit: d.unit, one, all: (vs: (number | null)[]) => vs.map(one),
             text: (v: number | null | undefined) => (v == null ? "-" : sig3(v * d.factor)) };
  };
}
