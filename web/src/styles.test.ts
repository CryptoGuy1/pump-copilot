import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/** Design tokens: every colour, spacing, radius, shadow and type size lives in
 * src/styles/tokens.css, and nothing else contains a raw one. A theme is only token values. */

const SRC = join(process.cwd(), "src");
const TOKENS = "styles/tokens.css";
const walk = (dir: string): string[] => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f);
  return statSync(p).isDirectory() ? walk(p) : [p];
});
const rel = (p: string) => relative(SRC, p);
const all = walk(SRC);
const css = all.filter((f) => f.endsWith(".css") && rel(f) !== TOKENS);
const code = all.filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$|schema\.d\.ts$/.test(f)
                                && !rel(f).startsWith("test/"));

const HEX = /(^|[^\w&/$-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b/;
const FN = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/;
const NAMED = new Set(["white", "black", "red", "green", "blue", "gray", "grey", "orange",
  "yellow", "purple", "pink", "brown", "navy", "teal", "maroon", "olive", "silver", "lime",
  "aqua", "fuchsia", "cyan", "magenta", "gold", "crimson", "indigo", "violet", "beige",
  "coral", "salmon", "tomato", "khaki", "ivory", "lavender", "tan", "wheat", "orchid"]);
const COLOR_PROPS = /^(color|background(-color)?|border(-\w+)*|outline(-color)?|fill|stroke|box-shadow|text-decoration(-color)?|caret-color|accent-color|column-rule(-color)?|stop-color)$/;
const SCALE_PROPS = /^(font-size|padding(-\w+)*|margin(-\w+)*|gap|row-gap|column-gap|border-radius|border-(top|bottom)-(left|right)-radius|box-shadow|inset|top|right|bottom|left)$/;

const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "");
/** "prop: value" pairs of a stylesheet (custom properties are declarations too). */
const declarations = (s: string) => [...stripComments(s).matchAll(
  /(^|[{;\s])([a-z-]+)\s*:\s*([^;{}]+)(?=[;}])/g)].map((m) => ({ prop: m[2], value: m[3].trim() }));
const withoutVars = (v: string) => {
  let out = v;
  while (/var\([^()]*\)/.test(out)) out = out.replace(/var\([^()]*\)/g, " ");
  return out;
};
/** A value part is fine if it is a token, a calc() over tokens, 0, a keyword or a percentage. */
const scaled = (value: string) => {
  const parts = value.match(/(?:[a-z-]+\((?:[^()]|\([^()]*\))*\)|[^\s,]+)/g) ?? [];
  return parts.every((p) => /^var\(/.test(p) || (/^calc\(/.test(p) && /var\(/.test(p))
    || /^(0|auto|none|inherit|initial|unset|inset|-?\d+(\.\d+)?%|max-content|min-content)$/.test(p));
};

describe("design tokens", () => {
  it("the token file defines every theme", () => {
    const t = readFileSync(join(SRC, TOKENS), "utf8");
    for (const theme of ["industrial", "aurora", "daylight"])
      expect(t).toContain(`[data-theme="${theme}"]`);
    expect(t).toContain(":root:not([data-theme])"); // industrial is the default
  });

  it("no stylesheet outside tokens.css has a raw colour", () => {
    const bad: string[] = [];
    for (const f of css) {
      for (const { prop, value } of declarations(readFileSync(f, "utf8"))) {
        const v = withoutVars(value);
        if (HEX.test(` ${v}`) || FN.test(v)
            || (COLOR_PROPS.test(prop) && v.split(/[\s,()]+/).some((w) => NAMED.has(w))))
          bad.push(`${rel(f)}: ${prop}: ${value}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("no component has a raw colour", () => {
    const bad: string[] = [];
    for (const f of code) {
      const text = readFileSync(f, "utf8");
      for (const m of text.matchAll(/(["'`])((?:\\.|(?!\1).)*)\1/g)) {
        const s = m[2];
        if (HEX.test(` ${s}`) || FN.test(s)) bad.push(`${rel(f)}: ${s}`);
      }
      for (const m of text.matchAll(/\b(color|background|fill|stroke|stopColor)\s*[:=]\s*["'`]([a-z]+)["'`]/g))
        if (NAMED.has(m[2])) bad.push(`${rel(f)}: ${m[0]}`);
    }
    expect(bad).toEqual([]);
  });

  it("spacing, radii, shadows and type sizes come from tokens", () => {
    const bad: string[] = [];
    for (const f of css) {
      for (const { prop, value } of declarations(readFileSync(f, "utf8"))) {
        if (prop.startsWith("--")) continue;
        if ((SCALE_PROPS.test(prop) && !scaled(value)) || (prop === "font" && !/var\(|^(inherit|initial|unset)$/.test(value)))
          bad.push(`${rel(f)}: ${prop}: ${value}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("every token in use is defined", () => {
    const defined = new Set<string>();
    for (const f of [join(SRC, TOKENS), ...css])
      for (const m of readFileSync(f, "utf8").matchAll(/(--[a-z0-9-]+)\s*:/g)) defined.add(m[1]);
    const missing: string[] = [];
    for (const f of [...css, ...code])
      for (const m of readFileSync(f, "utf8").matchAll(/var\((--[a-z0-9-]+)/g))
        if (!defined.has(m[1])) missing.push(`${rel(f)}: ${m[1]}`);
    for (const f of code)  // chart colours are token names too
      for (const m of readFileSync(f, "utf8").matchAll(/["'`](--(?:chart|st|sig|syn)[a-z0-9-]*)["'`]/g))
        if (!defined.has(m[1])) missing.push(`${rel(f)}: ${m[1]}`);
    expect(missing).toEqual([]);
  });

  it("the check itself catches raw values", () => {
    const sample = "a { color: #fff; } b { padding: 4px; } c { color: var(--text); gap: var(--space-2); }";
    const d = declarations(sample);
    expect(d.filter(({ value }) => HEX.test(` ${withoutVars(value)}`)).map((x) => x.prop))
      .toEqual(["color"]);
    expect(d.filter(({ prop, value }) => SCALE_PROPS.test(prop) && !scaled(value))
      .map((x) => x.prop)).toEqual(["padding"]);
    expect(HEX.test(" /assumptions#A5")).toBe(false);
    expect(HEX.test(" #2060c0")).toBe(true);
  });
});
