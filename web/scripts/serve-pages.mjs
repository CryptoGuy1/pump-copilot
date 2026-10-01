// Serves the static snapshot build (dist-snapshot/) the way GitHub Pages serves a project site:
// under /pump-copilot/, a directory's index.html for a directory, and 404.html with status 404
// for any path that has no file (which is how the app's deep links work there).
import { createServer } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, join, normalize, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "../dist-snapshot");
const BASE = "/pump-copilot/";
const PORT = Number(process.env.PUMPCOPILOT_PAGES_PORT ?? 5176);
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript",
                ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
                ".png": "image/png", ".woff2": "font/woff2", ".woff": "font/woff" };

function send(res, status, file) {
  res.writeHead(status, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream" });
  res.end(readFileSync(file));
}

createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (path === "/pump-copilot") { res.writeHead(301, { Location: BASE }); return res.end(); }
  if (path.startsWith(BASE)) {
    let file = normalize(join(ROOT, path.slice(BASE.length)));
    if (file.startsWith(ROOT)) {
      if (existsSync(file) && statSync(file).isDirectory()) file = join(file, "index.html");
      if (existsSync(file) && statSync(file).isFile()) return send(res, 200, file);
    }
  }
  send(res, 404, join(ROOT, "404.html"));
}).listen(PORT, "127.0.0.1", () => console.log(`pages: http://127.0.0.1:${PORT}${BASE}`));
