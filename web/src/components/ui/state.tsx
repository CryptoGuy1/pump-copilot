/** State badge and SYNTHETIC marker. Every state has a text label, a shape and a pattern:
 * never colour alone. Meaning colour (tokens --st-*, --syn*) is used here and nowhere else. */

export const STATES = ["review_suggested", "insufficient_evidence", "data_unavailable",
                       "normal"] as const;

export const stateLabel = (s: string) => String(s).replace(/_/g, " ");

function StateIcon({ state }: { state: string }) {
  const common = { viewBox: "0 0 16 16", "aria-hidden": true, focusable: false,
                   className: "state-icon" } as const;
  switch (state) {
    case "review_suggested":  // a triangle with a bar: look at this
      return <svg {...common}><path d="M8 1.5 15 14.5H1z" fill="currentColor" />
        <path d="M8 6v4.2M8 11.8v.9" stroke="var(--icon-cut)" strokeWidth="1.8"
              strokeLinecap="round" /></svg>;
    case "insufficient_evidence":  // a half-filled circle: not enough to say
      return <svg {...common}><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor"
                                      strokeWidth="1.8" />
        <path d="M8 1.8a6.2 6.2 0 0 1 0 12.4z" fill="currentColor" /></svg>;
    case "data_unavailable":  // a slashed circle: no data
      return <svg {...common}><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor"
                                      strokeWidth="1.8" />
        <path d="M3.6 12.4 12.4 3.6" stroke="currentColor" strokeWidth="1.8" /></svg>;
    case "normal":  // a small check: nothing to review
      return <svg {...common}><path d="M3 8.5 6.5 12 13 4.5" fill="none" stroke="currentColor"
                                    strokeWidth="2" strokeLinecap="round" /></svg>;
    default:
      return null;
  }
}

export function StateBadge({ state, size }: { state: string; size?: "lg" }) {
  return (
    <span className={`state state-${state}${size ? ` state-${size}` : ""}`} data-state={state}>
      <span className="pattern" aria-hidden="true" />
      <StateIcon state={state} />
      <span className="state-label">{stateLabel(state)}</span>
    </span>
  );
}

/** Shown wherever data is synthetic (an injected, in-memory fault). */
export function Synthetic({ show }: { show: boolean | null | undefined }) {
  if (!show) return null;
  return (
    <span className="synthetic" data-testid="synthetic-label">
      <span className="pattern" aria-hidden="true" />
      <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" className="state-icon">
        <path d="M8 1 15 8 8 15 1 8z" fill="currentColor" /></svg>
      SYNTHETIC
    </span>
  );
}
