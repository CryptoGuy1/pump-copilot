import { type ReactNode, createContext, useContext, useEffect, useId, useMemo, useRef,
         useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { useTheme } from "../theme";
import { Button } from "./ui";

/** Step 5b charts: stacked time series with a shared, synchronised cursor, shared zoom and
 * pan, the baseline band as a soft fill with edge lines, review windows as a thin strip
 * along the bottom, and, in a replay, the cursor line with the part of the day not yet
 * replayed greyed out. Colours are tokens (styles/tokens.css), read at draw time. Values
 * arrive in display units (see useSignalFormat). */

export type ChartColor = `--${string}`;

export interface Line {
  label: string;
  values: (number | null)[];
  color: ChartColor;
  dash?: number[];      // only where several signals share one chart
  width?: number;
  points?: boolean;     // markers only
  future?: boolean;     // raw data: hidden (or greyed) beyond the replay cursor
}
export interface Band { low: (number | null)[]; high: (number | null)[] }
export interface Span { from: number; to: number; kind: "review" | "highlight" }

declare global {
  interface Window { __firstChartAt?: number }
}

const secs = (t: string) => new Date(t).getTime() / 1000;
export const toSeconds = (ts: string[]) => ts.map(secs);
export const utc = (s: number, withSeconds = true) =>
  new Date(s * 1000).toISOString().slice(11, withSeconds ? 19 : 16);
const fmt = (v: number) => Number(v.toPrecision(3)).toString();

/** Colour per signal; the dash styles are for charts where signals share the plot. */
export const DASHES: number[][] = [[], [6, 3], [2, 3], [8, 3, 2, 3], [4, 2], [1, 3]];
export const signalColor = (i: number) => `--sig-${(i % 6) + 1}` as ChartColor;
export const signalStyle = (i: number) => ({ color: signalColor(i),
                                              dash: DASHES[i % DASHES.length] });

// --- the group: shared cursor, zoom and pan, replay cursor ----------------------------------

interface Group {
  syncKey: string;
  view: [number, number] | null;   // what every chart shows
  setRange: (r: [number, number] | null) => void;
  extent: [number, number] | null;
  cursorAt: number | null;
  showFuture: boolean;
}
const GroupCtx = createContext<Group | null>(null);
export const useChartGroup = () => useContext(GroupCtx);

export type LegendItem = { kind: "line" | "band" | "review" | "cursor" | "future" | "highlight"
  | "op" | "forming"; label: string; color?: ChartColor; dash?: number[]; op?: string };

/** Charts in a group share the hover cursor (and its readouts), zoom and pan. By default
 * they show `focus` (the run, up to the replay cursor); "Show the full day" shows `extent`
 * and, beyond the cursor, the raw signals greyed. One sticky control bar holds the tools and
 * the legend (collapsible on small screens). */
export function ChartGroup({ extent, focus, cursorAt = null, children, label = "Charts",
                             legend = [] }:
                           { extent: [number, number] | null; focus?: [number, number] | null;
                             cursorAt?: number | null; children: ReactNode; label?: string;
                             legend?: LegendItem[] }) {
  const syncKey = useId();
  const wide = useMediaQuery("(min-width: 721px)");
  const [range, setRange] = useState<[number, number] | null>(null);
  const [showFuture, setShowFuture] = useState(false);
  const base = showFuture || !focus ? extent : focus;
  const view = range ?? base;
  const zoom = (f: number) => {
    if (!view || !extent) return;
    const mid = (view[0] + view[1]) / 2, half = ((view[1] - view[0]) * f) / 2;
    const lo = Math.max(extent[0], mid - half), hi = Math.min(extent[1], mid + half);
    setRange(hi - lo >= 120 ? [lo, hi] : view);
  };
  const pan = (f: number) => {
    if (!view || !extent) return;
    const w = view[1] - view[0], d = w * f;
    const lo = Math.min(Math.max(extent[0], view[0] + d), extent[1] - w);
    setRange([lo, lo + w]);
  };
  const legendList = <ChartLegend items={legend} />;
  return (
    <GroupCtx.Provider value={{ syncKey, view, setRange, extent, cursorAt, showFuture }}>
      <div className="control-bar" role="region" aria-label={`${label}: controls and legend`}>
        <div className="chart-tools" role="toolbar" aria-label={`${label}: zoom and pan`}>
          <span className="num range">{view ? `${utc(view[0], false)}–${utc(view[1], false)} UTC`
                                            : ""}</span>
          <Button size="sm" onClick={() => zoom(0.5)} disabled={!extent}>Zoom in</Button>
          <Button size="sm" onClick={() => zoom(2)} disabled={!range}>Zoom out</Button>
          <Button size="sm" onClick={() => pan(-0.5)} disabled={!range}
                  aria-label="Pan earlier">←</Button>
          <Button size="sm" onClick={() => pan(0.5)} disabled={!range}
                  aria-label="Pan later">→</Button>
          <Button size="sm" variant="ghost" onClick={() => setRange(null)} disabled={!range}>
            Reset</Button>
          {focus && (
            <label className="toggle">
              <input type="checkbox" checked={showFuture}
                     onChange={(e) => { setShowFuture(e.target.checked); setRange(null); }} />
              Show the full day</label>)}
        </div>
        {legend.length > 0 && (wide ? legendList
          : <details className="legend-fold"><summary>Legend</summary>{legendList}</details>)}
      </div>
      {children}
    </GroupCtx.Provider>
  );
}

// --- one chart ---------------------------------------------------------------------------------

export function TimeChart({ title, titleTip, unit, x, lines, band, spans = [], height,
                            highlighted = false, synthetic = false, swatch, labelFuture = false,
                            forming }:
                          { title: string; titleTip?: string; unit?: string; x: number[];
                            lines: Line[]; band?: Band; spans?: Span[]; height?: number;
                            highlighted?: boolean; synthetic?: boolean;
                            swatch?: { color: ChartColor; dash?: number[] };
                            labelFuture?: boolean; forming?: [number, number] | null }) {
  const el = useRef<HTMLDivElement>(null);
  const readout = useRef<HTMLSpanElement>(null);
  const future = useRef<HTMLSpanElement>(null);
  const formingLabel = useRef<HTMLSpanElement>(null);
  const wide = useMediaQuery("(min-width: 721px)");  // on phones the label sits in the header
  const theme = useTheme();
  const g = useChartGroup();
  const cursorAt = g?.cursorAt ?? null;
  const showFuture = g?.showFuture ?? false;
  const plot = useRef<uPlot | null>(null);

  // raw lines beyond the replay cursor: hidden, or drawn grey with "Show the full day"
  const series = useMemo(() => {
    const out: (Line & { grey?: boolean })[] = [];
    for (const l of lines) {
      if (!l.future || cursorAt == null) { out.push(l); continue; }
      out.push({ ...l, values: l.values.map((v, i) => (x[i] <= cursorAt ? v : null)) });
      if (showFuture)
        out.push({ ...l, label: `${l.label} (not yet replayed)`, color: "--chart-future-line",
                   grey: true, values: l.values.map((v, i) =>
                     (x[i] >= cursorAt || (i + 1 < x.length && x[i + 1] > cursorAt) ? v : null)) });
    }
    return out;
  }, [lines, x, cursorAt, showFuture]);

  useEffect(() => {
    const box = el.current;
    if (!box || !x.length) return;
    const css = getComputedStyle(box);
    const tok = (t: string) => css.getPropertyValue(t).trim();
    const px = (t: string) => parseFloat(tok(t)) || 0;
    const axis = { stroke: tok("--chart-axis"), grid: { stroke: tok("--chart-grid") },
                   ticks: { stroke: tok("--chart-grid") }, font: `11px ${tok("--font-sans")}` };
    // times are UTC, 24-hour, on the axis as in the readouts
    const xAxis = { ...axis, values: (_u: uPlot, splits: number[]) =>
      splits.map((s) => utc(s, false)) };
    const yAxis = { ...axis, size: px("--axis-w"), values: (_u: uPlot, splits: number[]) => {
      // as many decimals as the tick step needs, so labels never repeat
      const step = splits.length > 1 ? Math.abs(splits[1] - splits[0]) : 1;
      const d = Math.min(6, Math.max(0, Math.ceil(-Math.log10(step || 1))));
      return splits.map((v) => v.toFixed(d));
    } };
    const bandIdx = band ? [1, 2] : [];
    const dpr = devicePixelRatio;
    const data: (number | null)[][] = [x, ...(band ? [band.low, band.high] : []),
                                        ...series.map((s) => s.values)];
    const hatch = (() => {  // the baseline-forming zone: a light diagonal hatch
      const c = document.createElement("canvas");
      c.width = c.height = 8 * dpr;
      const k = c.getContext("2d")!;
      k.strokeStyle = tok("--chart-forming-line");
      k.lineWidth = dpr;
      k.beginPath(); k.moveTo(0, 8 * dpr); k.lineTo(8 * dpr, 0); k.stroke();
      return c;
    })();
    const under = (u: uPlot) => {  // baseline forming, highlighted evidence, not yet replayed
      const { ctx } = u;
      const top = u.bbox.top, h = u.bbox.height, right = u.bbox.left + u.bbox.width;
      const X = (v: number) => u.valToPos(v, "x", true);
      const fl = formingLabel.current;
      if (forming) {
        const a = Math.max(X(forming[0]), u.bbox.left), b = Math.min(X(forming[1]), right);
        if (b > a) {
          ctx.fillStyle = tok("--chart-forming");
          ctx.fillRect(a, top, b - a, h);
          ctx.fillStyle = ctx.createPattern(hatch, "repeat")!;
          ctx.fillRect(a, top, b - a, h);
        }
        if (fl) {  // the label inside the zone, when it fits
          fl.hidden = (b - a) / dpr < (fl.offsetWidth || 110) + 12;
          fl.style.left = `${a / dpr + 6}px`;
        }
      } else if (fl) fl.hidden = true;
      for (const s of spans.filter((q) => q.kind === "highlight")) {
        ctx.fillStyle = tok("--chart-highlight");
        ctx.fillRect(X(s.from), top, Math.max(1, X(s.to) - X(s.from)), h);
      }
      if (cursorAt != null) {
        const c = Math.max(X(cursorAt), u.bbox.left);
        if (c < right) { ctx.fillStyle = tok("--chart-future"); ctx.fillRect(c, top, right - c, h); }
      }
    };
    const over = (u: uPlot) => {  // review windows along the bottom, then the replay cursor
      const { ctx } = u;
      const X = (v: number) => u.valToPos(v, "x", true);
      const sh = px("--review-strip") * dpr, y = u.bbox.top + u.bbox.height - sh;
      for (const s of spans.filter((q) => q.kind === "review")) {
        const a = X(s.from), w = Math.max(dpr, X(s.to) - a);
        ctx.fillStyle = tok("--st-review");
        ctx.fillRect(a, y, w, sh);
        ctx.strokeStyle = tok("--st-review-edge");
        ctx.lineWidth = dpr;
        ctx.strokeRect(a, y, w, sh);
      }
      const lbl = future.current;
      if (cursorAt == null) { if (lbl) lbl.hidden = true; return; }
      const c = u.valToPos(cursorAt, "x", true);
      const inView = c >= u.bbox.left && c <= u.bbox.left + u.bbox.width;
      if (inView) {
        ctx.save();
        ctx.strokeStyle = tok("--chart-cursor");
        ctx.lineWidth = 2 * dpr;
        ctx.beginPath(); ctx.moveTo(c, u.bbox.top); ctx.lineTo(c, u.bbox.top + u.bbox.height);
        ctx.stroke();
        ctx.restore();
      }
      if (lbl) {  // the label, once, inside the grey area (top chart only)
        const plotRight = u.over.offsetLeft + u.over.clientWidth;
        const at = u.valToPos(cursorAt, "x") + u.over.offsetLeft;
        lbl.hidden = !inView && c > u.bbox.left;  // hidden only if the cursor is past the view
        const w = lbl.offsetWidth || 120;
        // just after the cursor, or against the right edge when the grey area is narrow
        lbl.style.left = `${Math.max(u.over.offsetLeft + 8,
                                     Math.min(at + 8, plotRight - w - 8))}px`;
      }
    };
    const u = new uPlot({
      width: box.clientWidth || 800, height: height ?? px("--chart-h"),
      padding: [8, px("--plot-pad-r"), 0, 0],
      legend: { show: false },
      scales: { x: { time: true } },
      axes: [xAxis, yAxis],
      tzDate: (ts: number) => uPlot.tzDate(new Date(ts * 1000), "Etc/UTC"),
      cursor: { sync: g ? { key: g.syncKey } : undefined, drag: { x: true, y: false, setScale: false },
                points: { size: 7 } },
      series: [{}, ...bandIdx.map(() => ({ stroke: tok("--chart-band"), width: 1,
                                             points: { show: false } })),
               ...series.map((s) => ({
                 label: s.label, stroke: tok(s.color), dash: s.dash, spanGaps: false,
                 width: s.points ? 0 : s.width ?? 1.6,
                 points: s.points ? { show: true, size: 6, stroke: tok(`${s.color}-edge`)
                                        || tok(s.color), fill: tok(s.color) }
                                  : { show: false } }))],
      bands: band ? [{ series: [2, 1], fill: tok("--chart-band-fill") }] : undefined,
      hooks: {
        drawClear: [under], draw: [over],
        setSelect: [(u) => {
          if (u.select.width > 4 && g) {
            const a = u.posToVal(u.select.left, "x"), b = u.posToVal(u.select.left + u.select.width, "x");
            g.setRange([a, b]);
          }
          u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
        }],
        setCursor: [(u) => {
          const span = readout.current;
          const i = u.cursor.idx;
          if (!span) return;
          if (i == null) { span.textContent = ""; return; }
          const parts = [`${utc(u.data[0][i] as number)} UTC`];
          series.forEach((s, k) => {
            const v = u.data[k + 1 + bandIdx.length]?.[i];
            if (v != null && !s.grey) parts.push(`${s.label} ${fmt(v)}${unit ? ` ${unit}` : ""}`);
          });
          if (band) {
            const lo = u.data[1][i], hi = u.data[2][i];
            if (lo != null && hi != null) parts.push(`band ${fmt(lo)}–${fmt(hi)}`);
          }
          span.textContent = parts.join(" · ");
        }],
      },
    }, data as uPlot.AlignedData, box);
    plot.current = u;
    const v0 = g?.view;  // in a group, every chart shows the same time
    if (v0) u.setScale("x", { min: v0[0], max: v0[1] });
    // touch: a tap or a drag moves the cursor, so the readouts work on phones
    const ov = u.over;
    const touch = (e: TouchEvent) => {
      const t = e.touches[0], r = ov.getBoundingClientRect();
      if (t) u.setCursor({ left: t.clientX - r.left, top: t.clientY - r.top });
    };
    ov.addEventListener("touchstart", touch, { passive: true });
    ov.addEventListener("touchmove", touch, { passive: true });
    const ro = new ResizeObserver(() => u.setSize({ width: box.clientWidth,
                                                    height: height ?? px("--chart-h") }));
    ro.observe(box);
    if (window.__firstChartAt === undefined) {
      window.__firstChartAt = performance.now();
      performance.mark("first-chart");
    }
    return () => { ro.disconnect(); u.destroy(); plot.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [x, series, band, spans, height, theme, cursorAt, g?.syncKey, unit, forming?.[0],
      forming?.[1]]);

  useEffect(() => {  // shared zoom and pan, and the default range
    const u = plot.current;
    if (!u || !x.length) return;
    const v = g?.view;
    u.setScale("x", v ? { min: v[0], max: v[1] } : { min: x[0], max: x[x.length - 1] });
  }, [g?.view?.[0], g?.view?.[1], x]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (highlighted) el.current?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [highlighted]);

  return (
    <figure className={`tchart${synthetic ? " tchart-synthetic" : ""}${highlighted
      ? " highlighted" : ""}`}>
      <figcaption className="tchart-head">
        {swatch && <svg className="tchart-swatch" width="22" height="8" aria-hidden="true"
                        focusable="false"><line x1="0" x2="22" y1="4" y2="4"
          style={{ stroke: `var(${swatch.color})`, strokeWidth: 2.4,
                   strokeDasharray: (swatch.dash ?? []).join(" ") }} /></svg>}
        <span className="tchart-title" title={titleTip}>{title}</span>
        {unit && <span className="muted">{unit}</span>}
        {labelFuture && cursorAt != null && !wide && (
          <span className="not-yet-head" data-testid="not-yet">
            Not yet replayed after {utc(cursorAt, false)}{showFuture ? " (shown grey)" : ""}
          </span>)}
        <span className="tchart-readout num" ref={readout} aria-hidden="true" />
      </figcaption>
      <div className="chart" ref={el} data-testid="chart"
           data-highlighted={highlighted ? "true" : "false"} role="img"
           aria-label={`${title}${unit ? ` (${unit})` : ""}, over time`}>
        {forming && <span className="forming-label" ref={formingLabel} hidden>
          Baseline forming</span>}
        {labelFuture && cursorAt != null && wide && (
          <span className="not-yet" ref={future} data-testid="not-yet" hidden>
            Not yet replayed{showFuture ? " (shown grey)" : ""}</span>)}
      </div>
    </figure>
  );
}

// --- the state strip and the legend --------------------------------------------------------

export interface Segment { state: string; start_at: string; end_at: string }
const OP_LABEL: Record<string, string> = { running: "Running", off: "Off",
                                           transition: "Transition" };

/** Operating state along the same time axis as the charts (expressive colour plus a
 * pattern and a label: these are not the presentation states). */
export function StateStrip({ segments }: { segments: Segment[] }) {
  const g = useChartGroup();
  const ext = g?.view ?? g?.extent;
  if (!ext || !segments.length) return null;
  const [lo, hi] = ext, w = hi - lo;
  return (
    <div className="state-strip-wrap">
      <span className="strip-label">Operating state</span>
      <div className="state-strip" role="list" aria-label="Operating state over the day">
        {segments.map((s) => {
          const a = Math.max(lo, secs(s.start_at)), b = Math.min(hi, secs(s.end_at));
          if (b <= a) return null;
          const left = ((a - lo) / w) * 100, width = ((b - a) / w) * 100;
          const label = OP_LABEL[s.state] ?? s.state;
          return (
            <span key={s.start_at} role="listitem" className={`op op-${s.state}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  title={`${label} ${utc(secs(s.start_at))}–${utc(secs(s.end_at))} UTC`}>
              {width > 16 && <span className="op-text">{label}</span>}
              <span className="visually-hidden">{`${label} ${utc(secs(s.start_at), false)}–${
                utc(secs(s.end_at), false)} UTC`}</span>
            </span>);
        })}
        {g?.cursorAt != null && g.cursorAt >= lo && g.cursorAt <= hi &&
          <span className="strip-cursor" aria-hidden="true"
                style={{ left: `${((g.cursorAt - lo) / w) * 100}%` }} />}
      </div>
    </div>
  );
}

export function ChartLegend({ items }: { items: LegendItem[] }) {
  return (
    <ul className="chart-legend" aria-label="Legend">
      {items.map((it) => (
        <li key={it.label}>
          {it.kind === "line" && <svg width="24" height="8" aria-hidden="true" focusable="false">
            <line x1="0" x2="24" y1="4" y2="4" style={{ stroke: `var(${it.color})`,
              strokeWidth: 2.2, strokeDasharray: (it.dash ?? []).join(" ") }} /></svg>}
          {it.kind === "cursor" && <span className="lg lg-cursor" aria-hidden="true" />}
          {it.kind === "band" && <span className="lg lg-band" aria-hidden="true" />}
          {it.kind === "review" && <span className="lg lg-review" aria-hidden="true" />}
          {it.kind === "future" && <span className="lg lg-future" aria-hidden="true" />}
          {it.kind === "forming" && <span className="lg lg-forming" aria-hidden="true" />}
          {it.kind === "highlight" && <span className="lg lg-highlight" aria-hidden="true" />}
          {it.kind === "op" && <span className={`lg op op-${it.op}`} aria-hidden="true" />}
          {it.label}
        </li>))}
    </ul>
  );
}
