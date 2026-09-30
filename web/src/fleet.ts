import type { Schema } from "./api/client";

type ScoreRow = Schema<"ScoreRow">;

/** The presentation states, in the order they matter. Meaning colour is reserved for them. */
export const STATES = ["review_suggested", "insufficient_evidence", "data_unavailable",
                       "normal"] as const;

export interface Series { signal: string; points: [number, number][] }

/** Score over time per signal (one point per window end; abstentions are gaps). */
export function scoreSeries(rows: ScoreRow[], signals: string[]): Series[] {
  return signals.map((signal) => {
    const byEnd = new Map<number, number>();
    for (const r of rows) {
      if (r.signal_name === signal && r.score != null)
        byEnd.set(new Date(r.window_end).getTime(), r.score);
    }
    return { signal, points: [...byEnd.entries()].sort((a, b) => a[0] - b[0]) };
  });
}

/** The signals worth drawing: those not normal first, then the rest, at most `n`. */
export function signalsToDraw(states: Record<string, string>, n = 3): string[] {
  const rank = (s: string) => STATES.indexOf(s as (typeof STATES)[number]);
  return Object.keys(states)
    .sort((a, b) => rank(states[a]) - rank(states[b]) || a.localeCompare(b))
    .slice(0, n);
}
