import type { ReactNode } from "react";
import { ApiError } from "../../api/client";

/** Nothing to show yet, and why; optionally what to do about it. */
export function EmptyState({ title, children, action }:
                           { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty" data-testid="empty-state">
      <p className="empty-title">{title}</p>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

/** A placeholder while data loads: lines, or a card's shape. */
export function Skeleton({ lines = 3, card = false, label = "loading" }:
                         { lines?: number; card?: boolean; label?: string }) {
  return (
    <div className={`skeleton-stack${card ? " card" : ""}`} role="status" aria-busy="true"
         aria-label={label} data-testid="skeleton">
      {card && <span className="skeleton" style={{ width: "40%", height: "var(--space-7)" }} />}
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton"
              style={{ width: `${[92, 76, 84, 60, 70][i % 5]}%` }} />))}
      {card && <span className="skeleton" style={{ height: "var(--space-10)" }} />}
    </div>
  );
}

/** A request failed: what failed, the error, and what can be tried. */
export function ErrorPanel({ title = "Could not load this", error, action }:
                           { title?: string; error: unknown; action?: ReactNode }) {
  const e = error as ApiError | Error;
  const code = e instanceof ApiError ? `${e.status} ${e.code}` : null;
  return (
    <div className="error-panel" role="alert" data-testid="error-panel">
      <svg className="error-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <rect x="2" y="2" width="20" height="20" rx="3" fill="none" stroke="currentColor"
              strokeWidth="2" />
        <path d="M12 6.5v7M12 16.5v1" stroke="currentColor" strokeWidth="2.4"
              strokeLinecap="round" /></svg>
      <div>
        <h3>{title}</h3>
        <p>{String(e?.message ?? e)}</p>
        {code && <p><code>{code}</code></p>}
        {action}
      </div>
    </div>
  );
}

/** Loading, error or the content, for one query. */
export function Loading({ q, children, lines }:
                        { q: { isLoading: boolean; error: unknown; refetch?: () => unknown };
                          children: ReactNode; lines?: number }) {
  if (q.isLoading) return <Skeleton lines={lines} />;
  if (q.error) return <ErrorPanel error={q.error} action={q.refetch && (
    <button type="button" className="btn-sm" onClick={() => q.refetch?.()}>Try again</button>)} />;
  return <>{children}</>;
}
