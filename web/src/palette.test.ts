import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** The signal palette is expressive colour: it must never be mistaken for the meaning
 * colours (SYNTHETIC and review suggested), and lines must stand out from the chart. */

const tokens = readFileSync(join(process.cwd(), "src/styles/tokens.css"), "utf8");
const block = (sel: string) => {
  const i = tokens.indexOf(sel);
  return tokens.slice(i, tokens.indexOf("\n}", i));
};
const THEMES = { industrial: ':root:not([data-theme]), [data-theme="industrial"] {',
                 aurora: '[data-theme="aurora"] {', daylight: '[data-theme="daylight"] {' };
const val = (b: string, t: string) => b.match(new RegExp(`${t}:\\s*(#[0-9a-fA-F]{6})`))?.[1];

const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const lum = (h: string) => { const [r, g, b] = rgb(h).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a: string, b: string) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};
function lab(h: string) {  // sRGB -> CIE Lab (D65)
  const [r, g, b] = rgb(h).map(lin);
  const X = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047;
  const Y = r * 0.2126 + g * 0.7152 + b * 0.0722;
  const Z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883;
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : (841 / 108) * t + 4 / 29);
  return [116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))];
}
const deltaE = (a: string, b: string) => {
  const [p, q] = [lab(a), lab(b)];
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
};

describe("signal palette", () => {
  for (const [theme, sel] of Object.entries(THEMES)) {
    it(`${theme}: six signal colours, clear of SYNTHETIC and review, and visible on the chart`, () => {
      const b = block(sel);
      const sigs = [1, 2, 3, 4, 5, 6].map((i) => val(b, `--sig-${i}`));
      expect(sigs.every(Boolean)).toBe(true);
      const meaning = ["--syn", "--syn-edge", "--st-review", "--st-review-edge"]
        .map((t) => [t, val(b, t)!] as const);
      const bg = val(b, "--chart-bg")!;
      const bad: string[] = [];
      for (const s of sigs as string[]) {
        for (const [t, m] of meaning)
          if (deltaE(s, m) < 20) bad.push(`${s} too close to ${t} ${m} (ΔE ${deltaE(s, m).toFixed(1)})`);
        if (contrast(s, bg) < 3) bad.push(`${s} contrast ${contrast(s, bg).toFixed(2)} on ${bg}`);
      }
      for (let i = 0; i < sigs.length; i++)
        for (let j = i + 1; j < sigs.length; j++)
          if (deltaE(sigs[i]!, sigs[j]!) < 15) bad.push(`${sigs[i]} and ${sigs[j]} too alike`);
      expect(bad).toEqual([]);
    });
  }
});
