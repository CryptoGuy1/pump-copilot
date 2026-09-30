import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useTheme } from "../theme";

/** A colour token for charts (defined per theme in styles/tokens.css). */
export type ChartColor = "--chart-median" | "--chart-band" | "--chart-minmax" | "--chart-raw"
  | "--st-review" | "--chart-running" | "--chart-highlight";

export interface ChartSeries {
  label: string;
  values: (number | null)[];
  color: ChartColor;
  dash?: number[];
  points?: boolean; // markers only (e.g. reviewed windows)
}

export interface Shade { from: number; to: number; color: ChartColor }

declare global {
  interface Window { __firstChartAt?: number }
}

const secs = (t: string) => new Date(t).getTime() / 1000;
export const toSeconds = (ts: string[]) => ts.map(secs);

/** A uPlot time-series chart. `shade` paints background spans (state segments). */
export function Chart({ title, x, series, shade = [], height = 180, highlighted = false }:
                      { title: string; x: number[]; series: ChartSeries[]; shade?: Shade[];
                        height?: number; highlighted?: boolean }) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useTheme();  // redraw in the new theme's colours
  useEffect(() => {
    if (!el.current || !x.length) return;
    const css = getComputedStyle(el.current);
    const tok = (t: string) => css.getPropertyValue(t).trim();
    const axis = { stroke: tok("--chart-axis"), grid: { stroke: tok("--chart-grid") },
                   ticks: { stroke: tok("--chart-grid") } };
    const draw = (u: uPlot) => {
      const { ctx } = u;
      const top = u.bbox.top, h = u.bbox.height;
      for (const s of shade) {
        const a = u.valToPos(s.from, "x", true), b = u.valToPos(s.to, "x", true);
        ctx.fillStyle = tok(s.color);
        ctx.fillRect(a, top, Math.max(1, b - a), h);
      }
    };
    const plot = new uPlot({
      title, width: el.current.clientWidth || 800, height,
      scales: { x: { time: true } }, axes: [axis, axis],
      series: [{}, ...series.map((s) => ({
        label: s.label, stroke: tok(s.color), dash: s.dash, spanGaps: false,
        width: s.points ? 0 : 1.5,
        // review points: the state's colour with its edge, and a shape (a filled dot)
        points: s.points ? { show: true, size: 6, stroke: tok(`${s.color}-edge`) || tok(s.color),
                             fill: tok(s.color) } : { show: false },
      }))],
      hooks: { drawClear: [draw] },
    }, [x, ...series.map((s) => s.values)] as uPlot.AlignedData, el.current);
    if (window.__firstChartAt === undefined) {
      window.__firstChartAt = performance.now();
      performance.mark("first-chart");
    }
    return () => plot.destroy();
  }, [title, x, series, shade, height, theme]);
  useEffect(() => {
    if (highlighted) el.current?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [highlighted]);
  return <div className={`chart${highlighted ? " highlighted" : ""}`} ref={el}
              data-testid="chart" data-highlighted={highlighted ? "true" : "false"} />;
}
