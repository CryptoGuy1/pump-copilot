import type { ReactNode } from "react";

/** A labelled value: tabular monospace figures, with the unit set apart. */
export function ValueReadout({ label, value, unit, size }:
                             { label: string; value: ReactNode; unit?: string; size?: "sm" }) {
  return (
    <span className={`readout${size ? ` readout-${size}` : ""}`}>
      <span className="readout-label">{label}</span>
      <span className="readout-value">{value ?? "-"}
        {unit && <span className="readout-unit">{unit}</span>}</span>
    </span>
  );
}

/** A card: optional accent bar, head, body and a foot (provenance goes there). */
export function Card({ title, sub, aside, foot, children, testId, className = "",
                       accent = true, as = "article" }:
                     { title?: ReactNode; sub?: ReactNode; aside?: ReactNode; foot?: ReactNode;
                       children?: ReactNode; testId?: string; className?: string;
                       accent?: boolean; as?: "article" | "section" | "div" }) {
  const Tag = as;
  return (
    <Tag className={`card ${className}`.trim()} data-testid={testId}>
      {accent && <div className="card-accent" aria-hidden="true" />}
      {(title || aside) && <header className="card-head">
        <div>{title && <h2>{title}</h2>}{sub && <p className="muted">{sub}</p>}</div>
        {aside}
      </header>}
      {children}
      {foot && <footer className="card-foot">{foot}</footer>}
    </Tag>
  );
}

/** A table in a scrollable, focusable region (narrow screens scroll it sideways). */
export function Table({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      <table className="table">{children}</table>
    </div>
  );
}
