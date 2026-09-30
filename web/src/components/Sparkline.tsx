import { useId } from "react";
import type { Series } from "../fleet";
import { SignalName, useSignalLabel } from "./ui";

/** A small score-over-time chart: one line per signal, in the signal palette (colour and dash
 * style, so signals stay apart without colour). Expressive colour only; states are shown in
 * the badges next to it. */
export function Sparkline({ series, label, height = 64 }:
                          { series: Series[]; label: string; height?: number }) {
  const name = useSignalLabel();
  const id = useId().replace(/:/g, "");
  const w = 320;
  const pts = series.flatMap((s) => s.points);
  if (!pts.length) return <p className="spark-empty">no scores yet</p>;
  const [x0, x1] = [Math.min(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[0]))];
  const y1 = Math.max(1, ...pts.map((p) => p[1]));
  const X = (t: number) => (x1 === x0 ? w / 2 : ((t - x0) / (x1 - x0)) * (w - 4) + 2);
  const Y = (v: number) => height - 4 - (v / y1) * (height - 10);
  const path = (p: [number, number][]) =>
    p.map(([t, v], i) => `${i ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const first = series.find((s) => s.points.length);
  return (
    <svg className="sparkline" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none"
         role="img"
         aria-label={`${label}: ${series.map((s) => name(s.signal)).join(", ")}`}>
      <defs>
        <linearGradient id={`fill${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: "var(--spark-fill)", stopOpacity: 0.55 }} />
          <stop offset="1" style={{ stopColor: "var(--spark-fill)", stopOpacity: 0 }} />
        </linearGradient>
      </defs>
      <line x1="0" x2={w} y1={Y(0)} y2={Y(0)} className="spark-axis" />
      {first && <path d={`${path(first.points)}L${X(first.points.at(-1)![0])},${Y(0)}`
                          + `L${X(first.points[0][0])},${Y(0)}Z`}
                      fill={`url(#fill${id})`} stroke="none" />}
      {series.map((s, i) => s.points.length > 0 && (
        <path key={s.signal} d={path(s.points)} fill="none" className={`sig sig-${i + 1}`}
              vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}

/** The key for a sparkline: each signal's swatch (colour and dash) and name. */
export function SignalKey({ signals }: { signals: string[] }) {
  return (
    <ul className="signal-key">
      {signals.map((s, i) => (
        <li key={s}>
          <svg width="18" height="6" aria-hidden="true" focusable="false">
            <line x1="0" x2="18" y1="3" y2="3" className={`sig sig-${i + 1}`} />
          </svg>
          <SignalName id={s} short />
        </li>
      ))}
    </ul>
  );
}
