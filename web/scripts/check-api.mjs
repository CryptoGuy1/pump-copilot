// Fails when src/api/schema.d.ts is not what openapi-typescript makes of ../api/openapi.json.
// Regenerate with `npm run gen:api` (or `make gen-api`) after `pumpcopilot api --export-openapi`.
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "api-check-")), "schema.d.ts");
execFileSync("npx", ["openapi-typescript", "../api/openapi.json", "-o", out], { stdio: "pipe" });
const fresh = readFileSync(out, "utf8");
const committed = readFileSync("src/api/schema.d.ts", "utf8");
if (fresh !== committed) {
  console.error("src/api/schema.d.ts is out of date with api/openapi.json: run `npm run gen:api`.");
  process.exit(1);
}
console.log("typed API client is up to date with api/openapi.json");
