import { readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { ROUTES, ROUTE_DATA } from "../src/routes";

/** Every concrete page of the static snapshot: each route of the router, and for a route with
 * parameters one page per value the snapshot has data for (e.g. /cases/1 for
 * snapshot/api/cases/1.json). Used by the build (vite.config.ts) and its test. */
export function staticRoutes(snapshotDir: string): string[] {
  const files = walk(snapshotDir).map((f) => relative(snapshotDir, f).split(sep).join("/"));
  const out: string[] = [];
  for (const [name, path] of Object.entries(ROUTES)) {
    const data = ROUTE_DATA[name as keyof typeof ROUTES];
    if (!path.includes(":")) { out.push(path); continue; }
    if (!data) throw new Error(`route ${path} has parameters but no ROUTE_DATA`);
    const params = [...data.matchAll(/:(\w+)/g)].map((m) => m[1]);
    const re = new RegExp("^" + data.replace(/[.]/g, "\\.").replace(/:(\w+)/g, "([^/]+)") + "$");
    for (const f of files) {
      const m = f.match(re);
      if (!m) continue;
      out.push(params.reduce((p, k, i) => p.replace(`:${k}`, m[i + 1]), path as string));
    }
  }
  return [...new Set(out)].sort();
}

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}
