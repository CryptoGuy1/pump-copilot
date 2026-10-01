import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { type Schema, call, client } from "../api/client";
import { ChartGroup, type Line, type Span, StateStrip, TimeChart, signalColor, toSeconds,
         utc } from "../components/charts";
import { A8Notice, fmtTime } from "../components/common";
import { EmptyState, Loading, Synthetic, Tabs, useSignalFormat, useSignalLabel }
  from "../components/ui";
import { usePageTitle } from "../hooks/usePageTitle";
import { usePageProvenance } from "../pageProvenance";

type ScoreRow = Schema<"ScoreRow">;
const secs = (t: string) => Date.parse(t) / 1000;

/** Review windows as spans, neighbouring windows merged. */
function reviewSpans(rows: ScoreRow[]): Span[] {
  const out: Span[] = [];
  for (const r of rows) {
    if (r.state !== "review_suggested") continue;
    const a = secs(r.window_start), b = secs(r.window_end), last = out[out.length - 1];
    if (last && a <= last.to) last.to = Math.max(last.to, b);
    else out.push({ from: a, to: b, kind: "review" });
  }
  return out;
}

export function AssetDay() {
  const { asset = "", day = "" } = useParams();
  usePageTitle(`${asset} · ${day}`);
  const [params, setParams] = useSearchParams();
  const session = params.get("session") ? Number(params.get("session")) : undefined;
  const path = { asset_id: asset, source_day: day };
  const info = useQuery({ queryKey: ["asset-day", asset, day], queryFn: () => call(
    client.GET("/api/assets/{asset_id}/days/{source_day}", { params: { path } })) });
  const segs = useQuery({ queryKey: ["segments", asset, day], queryFn: () =>
    call(client.GET("/api/assets/{asset_id}/days/{source_day}/segments",
                    { params: { path } })) });
  const oneMin = useQuery({ queryKey: ["signals-1m", asset, day], queryFn: () =>
    call(client.GET("/api/assets/{asset_id}/days/{source_day}/signals",
                    { params: { path, query: { resolution: "1m" } } })) });
  const hasSessions = !!info.data?.sessions.length;
  const scores = useQuery({ queryKey: ["scores", asset, day, session], enabled: hasSessions,
    queryFn: () => call(client.GET("/api/assets/{asset_id}/days/{source_day}/scores",
                                   { params: { path, query: { session_id: session } } })) });
  const name = useSignalLabel();
  const format = useSignalFormat();

  const current = info.data?.sessions.find((s) => s.session_id === scores.data?.session_id);
  usePageProvenance(scores.data ? { synthetic: scores.data.synthetic,
                                    model_version: scores.data.model_version,
                                    assumptions: scores.data.assumptions }
    : info.data && { synthetic: false, model_version: [], assumptions: info.data.assumptions });

  const segments = segs.data?.segments ?? [];
  const extent = useMemo<[number, number] | null>(() => segments.length
    ? [secs(segments[0].start_at), secs(segments[segments.length - 1].end_at)] : null,
    [segments]);
  // replay context: opened from a session, or a session that has not finished
  const cursorAt = current?.cursor_at && (session != null || current.status !== "completed")
    ? secs(current.cursor_at) : null;
  // one colour per signal, the same in both tabs (each chart has one signal: solid lines)
  const order = useMemo(() => Object.keys(oneMin.data?.signals ?? {}).sort(), [oneMin.data]);
  const colorOf = (sig: string) => signalColor(Math.max(0, order.indexOf(
    sig.replace(/_rel_ambient$/, ""))));

  const scored = useMemo(() => {
    const by: Record<string, ScoreRow[]> = {};
    for (const r of scores.data?.scores ?? []) (by[r.signal_name] ??= []).push(r);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [scores.data]);
  const synthetic = !!scores.data?.synthetic;
  // by default: from the start of the run (or the first scored window) to the replay cursor
  // (or the end of the run), with some room either side; "Show the full day" shows it all
  const focus = useMemo<[number, number] | null>(() => {
    if (!extent) return null;
    const runs = segments.filter((s) => s.state === "running");
    const firstScore = scores.data?.scores.reduce<number | null>((m, r) => {
      const t = secs(r.window_start); return m == null || t < m ? t : m; }, null) ?? null;
    const lastScore = scores.data?.scores.reduce<number | null>((m, r) => {
      const t = secs(r.window_end); return m == null || t > m ? t : m; }, null) ?? null;
    const start = runs.length ? secs(runs[0].start_at) : firstScore;
    const end = cursorAt ?? (runs.length ? secs(runs[runs.length - 1].end_at) : lastScore);
    if (start == null || end == null || end <= start) return null;
    // a little room before the run; more after the cursor, for the "not yet replayed" label
    const pad = Math.max(600, (end - start) * 0.05);
    const after = cursorAt != null ? Math.max(1200, (end - start) * 0.15) : pad;
    return [Math.max(extent[0], start - pad), Math.min(extent[1], end + after)];
  }, [extent, segments, scores.data, cursorAt]);

  // before each signal's first scored window its baseline is still forming: from the start
  // of the run that window falls in
  const formingOf = (rows: ScoreRow[]): [number, number] | null => {
    if (!rows.length) return null;
    const first = Math.min(...rows.map((r) => secs(r.window_start)));
    let i = segments.findIndex((s) => s.state === "running" && secs(s.start_at) <= first
                                       && secs(s.end_at) >= first);
    if (i < 0) return null;
    // the run: back through running segments that follow on without a gap
    while (i > 0 && segments[i - 1].state === "running"
           && secs(segments[i].start_at) - secs(segments[i - 1].end_at) <= 5) i -= 1;
    const from = secs(segments[i].start_at);
    return from != null && from < first ? [from, first] : null;
  };
  const scoredCharts = scored.length === 0
    ? <EmptyState title={cursorAt != null ? `No scores yet at ${utc(cursorAt)} UTC`
                                          : "No scores for this day"}>
        {hasSessions ? "The first windows are scored once each signal has settled after the run "
          + "start (A7)." : <>No replay session for this day yet: <Link to="/replay">start
            one</Link>.</>}</EmptyState>
    : scored.map(([sig, rows], i) => {
        const color = colorOf(sig), f = format(sig);
        const lines: Line[] = [{ label: "Window median", values: f.all(rows.map((r) => r.median)),
                                 color, width: 2 }];
        return <TimeChart key={sig} title={name(sig)} titleTip={sig} unit={f.unit}
          x={toSeconds(rows.map((r) => r.window_end))} lines={lines} swatch={{ color }}
          band={{ low: f.all(rows.map((r) => r.band_low)),
                  high: f.all(rows.map((r) => r.band_high)) }}
          spans={reviewSpans(rows)} synthetic={synthetic} labelFuture={i === 0}
          forming={formingOf(rows)} />;
      });
  const rawCharts = Object.entries(oneMin.data?.signals ?? {}).map(([sig, points], i) => {
    const rows = points as Schema<"MinutePoint">[];  // resolution=1m
    const color = colorOf(sig), f = format(sig);
    return <TimeChart key={sig} title={name(sig)} titleTip={sig} unit={f.unit}
      x={toSeconds(rows.map((r) => r.bucket))} swatch={{ color }} labelFuture={i === 0}
      lines={[{ label: "1-minute mean", values: f.all(rows.map((r) => r.mean)), color,
                future: true }]} />;
  });

  return (
    <section className="asset-day">
      <header className="masthead panel">
        <div className="masthead-accent" aria-hidden="true" />
        {synthetic && <div className="synthetic-banner banner-top"><Synthetic show />
          <span>Replay #{current?.session_id} injects <span className="mono">
            {current?.scenario}</span> into stored data: not a real event</span></div>}
        <div>
          <p className="eyebrow">Asset day</p>
          <h1><span className="mono">{asset}</span> · <span className="num">{day}</span></h1>
          <p className="lede">{cursorAt != null && current
            ? <>Replay #{current.session_id}, {current.status}, at {utc(cursorAt)} UTC: charts
              stop at the replay cursor.</>
            : current ? <>Replay #{current.session_id}, {current.status}.</> : null}</p>
        </div>
        <Loading q={info} lines={1}>
          <label className="field">
            <span className="field-label">Replay session</span>
            <select value={scores.data?.session_id ?? ""} disabled={!hasSessions}
                    onChange={(e) => setParams({ session: e.target.value })}>
              {!hasSessions && <option value="">none yet</option>}
              {info.data?.sessions.map((s) => (
                <option key={s.session_id} value={s.session_id}>
                  #{s.session_id} {s.status}{s.synthetic ? " (SYNTHETIC)" : ""}
                  {s.cursor_at ? ` · ${fmtTime(s.cursor_at)}` : ""}</option>))}
            </select>
          </label>
        </Loading>
      </header>
      {hasSessions && <A8Notice />}
      <Loading q={segs} lines={2}>
        <ChartGroup extent={extent} focus={focus} cursorAt={cursorAt} label="Asset-day charts"
                    legend={[
            { kind: "op", op: "running", label: "Running" },
            { kind: "op", op: "transition", label: "Transition" },
            { kind: "op", op: "off", label: "Off" },
            { kind: "forming", label: "Baseline forming" },
            { kind: "band", label: "Baseline band" },
            { kind: "review", label: "Review suggested windows" },
            ...(cursorAt != null ? [{ kind: "cursor" as const, label: "Replay cursor" },
                                    { kind: "future" as const, label: "Not yet replayed" }]
              : []),
          ]}>
          <StateStrip segments={segments} />
          <Tabs label="Signals" tabs={[
            { id: "scored", label: `Scored signals (${scored.length})`,
              content: <Loading q={scores} lines={6}>{scoredCharts}</Loading> },
            { id: "raw", label: "Raw signals, 1-minute mean",
              content: <Loading q={oneMin} lines={6}>{rawCharts}</Loading> },
          ]} />
        </ChartGroup>
      </Loading>
    </section>
  );
}
