/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { type Plugin, defineConfig } from "vite";
import { staticRoutes } from "./scripts/static-routes";

// The browser talks to /api on the dev server, which proxies to the API on 127.0.0.1.
// PUMPCOPILOT_API picks another API (the end-to-end test runs its own on port 8001).
const api = process.env.PUMPCOPILOT_API ?? "http://127.0.0.1:8000";
const port = Number(process.env.PUMPCOPILOT_WEB_PORT ?? 5173);
const proxy = { "/api": { target: api, changeOrigin: false } };

// Step 7b: VITE_SNAPSHOT=1 builds the static snapshot for GitHub Pages (npm run build:snapshot):
// served under /pump-copilot/, reading web/public/snapshot/ instead of the API.
const SNAPSHOT = process.env.VITE_SNAPSHOT === "1";
export const PAGES_URL = "https://cryptoguy1.github.io/pump-copilot/";

function snapshotSite(): Plugin {
  let outDir = "dist";
  let root = ".";
  return {
    name: "pumpcopilot-snapshot-site",
    apply: "build",
    configResolved(c) { outDir = resolve(c.root, c.build.outDir); root = c.root; },
    // sharing previews need absolute URLs
    transformIndexHtml(html) {
      if (!SNAPSHOT) return html;
      return html
        .replace(/(<meta property="og:image" content=")[^"]*(")/, `$1${PAGES_URL}og.png$2`)
        .replace('<meta property="og:type"',
                 `<meta property="og:url" content="${PAGES_URL}" />\n    <meta property="og:type"`)
        .replace('<meta name="twitter:card" content="summary_large_image" />',
                 '<meta name="twitter:card" content="summary_large_image" />\n'
                 + `    <meta name="twitter:image" content="${PAGES_URL}og.png" />`);
    },
    closeBundle() {
      if (SNAPSHOT) {
        // GitHub Pages serves 404.html for any path it has no file for: the app then reads the
        // address itself, so deep links work
        copyFileSync(resolve(outDir, "index.html"), resolve(outDir, "404.html"));
        // and a page at every route the snapshot has data for, so a link to one returns 200
        // with its own og:url (Pages serves /evaluation from evaluation.html)
        const html = readFileSync(resolve(outDir, "index.html"), "utf8");
        for (const route of staticRoutes(resolve(root, "public/snapshot"))) {
          if (route === "/") continue;
          const file = resolve(outDir, `${route.slice(1)}.html`);
          mkdirSync(dirname(file), { recursive: true });
          writeFileSync(file, html.replace(/(<meta property="og:url" content=")[^"]*(")/,
                                           `$1${PAGES_URL}${route.slice(1)}$2`));
        }
      } else {
        rmSync(resolve(outDir, "snapshot"), { recursive: true, force: true });
      }
    },
  };
}

export default defineConfig({
  base: SNAPSHOT ? "/pump-copilot/" : "/",
  build: SNAPSHOT ? { outDir: "dist-snapshot" } : {},
  plugins: [react(), snapshotSite()],
  server: { host: "127.0.0.1", port, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port, strictPort: true, proxy },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
