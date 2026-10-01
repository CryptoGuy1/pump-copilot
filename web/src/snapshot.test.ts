import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "./snapshot-keys.json";
import { recordedSummary } from "./components/Assistant";
import { ACTIONS_DISABLED, NOT_IN_SNAPSHOT, snapshotFetch, snapshotKey } from "./snapshot";

describe("snapshot file names", () => {
  it("agree with pumpcopilot.snapshot.key", () => {
    for (const c of fixture.cases) expect(snapshotKey(c.path, c.query)).toBe(c.key);
  });
});

describe("snapshotFetch", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads a GET from its snapshot file", async () => {
    const seen: string[] = [];
    vi.stubGlobal("fetch", async (u: string) => {
      seen.push(u);
      return new Response('{"pumps":[]}', { headers: { "Content-Type": "application/json" } });
    });
    const r = await snapshotFetch(new Request("http://x/api/cases?status=open&limit=8"));
    expect(r.status).toBe(200);
    expect(seen).toEqual(["/snapshot/api/cases@limit=8~status=open.json"]);
  });

  it("answers a request with no file as not included in this snapshot", async () => {
    vi.stubGlobal("fetch", async () => new Response("<html>", {
      status: 404, headers: { "Content-Type": "text/html" } }));
    const r = await snapshotFetch(new Request("http://x/api/health"));
    expect(r.status).toBe(404);
    expect((await r.json()).error.code).toBe(NOT_IN_SNAPSHOT);
  });

  it("refuses every action without making a request", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    for (const method of ["POST", "PUT", "DELETE"]) {
      const r = await snapshotFetch(new Request("http://x/api/cases/1/acknowledge", { method }));
      expect(r.status).toBe(403);
      expect((await r.json()).error.message).toBe(ACTIONS_DISABLED);
    }
    expect(f).not.toHaveBeenCalled();
  });
});

describe("the recorded answers' summary line", () => {
  const r = (served: string) => ({ response: { served } });
  it("counts from the recorded data", () => {
    expect(recordedSummary([r("assistant"), r("assistant"), r("template"), r("template"),
                            r("template"), r("template")])).toBe(
      "2 of 6 recorded answers passed the checker; the other 4 were replaced by the evidence "
      + "summary. Reasons shown.");
    expect(recordedSummary([r("assistant"), r("template")])).toBe(
      "1 of 2 recorded answers passed the checker; the other 1 was replaced by the evidence "
      + "summary. Reasons shown.");
    expect(recordedSummary([r("assistant")])).toBe("All 1 recorded answers passed the checker.");
  });
});
