import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { type Schema, call, client } from "../api/client";
import { AssumptionChip, Card, EmptyState, Loading, ValueReadout } from "../components/ui";
import { usePageTitle } from "../hooks/usePageTitle";

/** Step 5b stage 3: data quality per pump-day (cadence, gaps, stale and spike flags) and the
 * known issues in the data, each tied to its assumption. All of it from the API. */

type DayQuality = Schema<"AssetDayQuality">;
const secs = (t: string) => Date.parse(t.replace(" ", "T")) / 1000;
const hm = (s: number) => new Date(s * 1000).toISOString().slice(11, 16);

function Timeline({ q }: { q: DayQuality }) {
  const span = q.audit?.span;
  if (!span || span.length < 2) return <p className="muted">No audit span for this day.</p>;
  const [lo, hi] = [secs(span[0]), secs(span[1])], w = Math.max(1, hi - lo);
  const at = (t: number) => `${((t - lo) / w) * 100}%`;
  const segs = q.audit?.cadence_segments ?? [];
  const gaps = (q.gaps?.items ?? []) as { after: string; gap_s: number }[];
  const site = (q.site_gaps as { after: string; seconds: number; pumps?: string[] }[]);
  return (
    <div className="dq-timelines">
      <div className="dq-axis" aria-hidden="true"><span>{hm(lo)}</span><span>{hm(hi)} UTC</span></div>
      <div className="dq-track-row">
        <span className="dq-track-label">Reading cadence</span>
        <div className="dq-track" role="list" aria-label="Reading cadence over the day">
          {segs.map((s, i) => {
            const a = secs(s.start), b = secs(s.end);
            return <span key={i} role="listitem" className={`dq-seg dq-seg-${i % 2}`}
                         style={{ left: at(a), width: `${((b - a) / w) * 100}%` }}
                         title={`${hm(a)}–${hm(b)} UTC: one reading every ${s.cadence_s} s`}>
              <span className="dq-seg-text">{s.cadence_s} s</span>
              <span className="visually-hidden">{`${hm(a)}–${hm(b)} UTC, every ${s.cadence_s} s`}</span>
            </span>;
          })}
        </div>
      </div>
      <div className="dq-track-row">
        <span className="dq-track-label">Gaps</span>
        <div className="dq-track dq-gaps" role={gaps.length || site.length ? "list" : undefined}
             aria-label={gaps.length || site.length ? "Gaps in the readings" : undefined}>
          {[...gaps.map((g) => ({ t: secs(g.after), s: g.gap_s, site: false })),
            ...site.map((g) => ({ t: secs(g.after), s: g.seconds, site: true }))].map((g, i) => (
            <span key={i} role="listitem" className={`dq-gap${g.site ? " dq-gap-site" : ""}`}
                  style={{ left: at(g.t) }} title={`${hm(g.t)} UTC: ${g.s} s without readings${
                    g.site ? " (all pumps)" : ""}`}>
              <span className="visually-hidden">{`${hm(g.t)} UTC, ${g.s} s${g.site
                ? ", all pumps" : ""}`}</span></span>))}
          {!gaps.length && !site.length && <span className="dq-none">none</span>}
        </div>
      </div>
      {site.length > 0 && <p className="muted">Site-wide gap: {site.map((g) =>
        `${hm(secs(g.after))} UTC, ${g.seconds} s (${(g.pumps ?? []).join(", ")})`).join("; ")}.</p>}
    </div>
  );
}

function DayCard({ asset, day, q }: { asset: string; day: string; q?: DayQuality }) {
  const sum = (k: string) => Object.values(q?.flag_counts ?? {}).reduce((n, v) => n + (v[k] ?? 0), 0);
  return (
    <Card as="article" accent={false} title={<><span className="mono">{asset}</span> · {day}</>}
          sub={<Link to={`/assets/${asset}/${day}`}>Open the day</Link>}>
      {!q ? <p className="muted">loading…</p> : <>
        <div className="readouts">
          <ValueReadout label="Readings" value={sum("readings")} size="sm" />
          <ValueReadout label="Stale suspected" value={sum("stale_suspected")} size="sm" />
          <ValueReadout label="Spikes suspected" value={sum("spike_suspected")} size="sm" />
          <ValueReadout label={`Gaps over ${q.gaps?.factor ?? "-"}× cadence`}
                        value={q.gaps?.count ?? 0} size="sm" />
          <ValueReadout label="Audit issues" value={q.audit?.issues?.length ?? 0} size="sm" />
        </div>
        <Timeline q={q} />
      </>}
    </Card>
  );
}

export function DataQuality() {
  usePageTitle("Data quality");
  const q = useQuery({ queryKey: ["data-quality"],
                       queryFn: () => call(client.GET("/api/data-quality")) });
  const days = q.data?.asset_days ?? [];
  const per = useQueries({ queries: days.map((d) => ({
    queryKey: ["asset-day-quality", d.asset_id, d.source_day],
    queryFn: () => call(client.GET("/api/assets/{asset_id}/days/{source_day}/data-quality",
      { params: { path: { asset_id: d.asset_id, source_day: d.source_day } } })) })) });
  return (
    <section className="data-quality">
      <header className="masthead panel">
        <div className="masthead-accent" aria-hidden="true" />
        <div>
          <p className="eyebrow">Audit and ingest flags</p>
          <h1>Data quality</h1>
          <p className="lede">How often each pump reported, where readings stopped, and how
            many were flagged as stale or as spikes. Flags are kept, never used to change a
            value.</p>
        </div>
      </header>
      <Loading q={q} lines={4}>
        <Card as="section" accent={false} title="Known issues in the data"
              sub="Each is handled by an assumption in the register.">
          {q.data?.known_issues.length ? <ul className="known-issues">
            {q.data.known_issues.map((k) => (
              <li key={k.title}><AssumptionChip id={k.assumption} />
                <div><strong>{k.title}.</strong> {k.detail}</div></li>))}
          </ul> : <EmptyState title="No known issues recorded" />}
          <p className="muted">Audit: {q.data?.audit_ok == null ? "not run"
            : q.data.audit_ok ? "no file-level issues" : "issues found"}
            {q.data?.audit_issues?.length ? `: ${q.data.audit_issues.join("; ")}` : ""}.</p>
        </Card>
        <div className="dq-grid">
          {days.map((d, i) => <DayCard key={`${d.asset_id}-${d.source_day}`} asset={d.asset_id}
                                       day={d.source_day} q={per[i]?.data} />)}
        </div>
      </Loading>
    </section>
  );
}
