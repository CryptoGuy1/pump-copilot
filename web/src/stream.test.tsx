import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {  // uPlot (charts) reads matchMedia when it is imported; jsdom has none
  window.matchMedia ??= ((q: string) => ({
    matches: false, media: q, onchange: null, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});

import { App } from "./App";
import { FakeEventSource } from "./test/FakeEventSource";

/** Stream events are cues to refetch, never data to show: after a rewind the stream replays
 * old events, and anything they carried could flash on screen before the refetch clips it. */

const MARK = "SENTINEL";
// every field a marker: the ones the API sends, and data an old or odd server might add
const payload = (extra: object) => ({
  session_id: 7, asset_id: `${MARK}-asset`, source_day: `${MARK}-day`, synthetic: true,
  created_at: `${MARK}-created`, count: 99, window_end_last: `${MARK} 09:59:00`,
  states: { review_suggested: 99 }, model_version: [`${MARK}-model`], note: `${MARK} note`,
  status: `${MARK}-status`, cursor_at: `${MARK}-cursor`, ...extra,
});

describe("the page and the stream", () => {
  let qc: QueryClient;
  let invalidate: { mock: { calls: unknown[][] } };

  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));  // queries stay loading
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    invalidate = vi.spyOn(qc, "invalidateQueries");
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("never renders event payloads, only refetches", () => {
    const { container } = render(
      <QueryClientProvider client={qc}><MemoryRouter><App /></MemoryRouter>
      </QueryClientProvider>);
    const es = FakeEventSource.instances.at(-1)!;
    act(() => es.open());
    act(() => {
      es.emit("replay.progress", 11, payload({}));
      es.emit("score.batch", 12, payload({}));
      es.emit("case.event", 13, payload({ case_id: 42, event_type: `${MARK}-type` }));
    });
    expect(container.textContent).not.toContain(MARK);
    expect(document.documentElement.outerHTML).not.toContain(MARK);  // title, attributes too
    const keys = invalidate.mock.calls.map((args) => JSON.stringify(
      (args[0] as { queryKey: unknown[] }).queryKey));
    expect(keys).toEqual(expect.arrayContaining([
      '["fleet"]', '["sessions"]', '["baseline",7]', '["scores"]', '["cases"]', '["case",42]']));
    // only the event id reaches the page (the live indicator), never its payload
    expect(container.querySelector('[data-testid="live-status"]')?.textContent).toContain("#13");
  });
});

describe("who may use the stream", () => {
  const src = join(process.cwd(), "src") + "/";  // vitest runs in web/
  const files = (dir: string): string[] => readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(f) ? [p] : [];
  });
  const code = files(src).filter((f) => !/\.test\.tsx?$|\/test\/|schema\.d\.ts$/.test(f));

  it("only the app shell subscribes, and it reads only identifiers from events", () => {
    const users = code.filter((f) => /\buseStream\(|new StreamClient\(/.test(
      readFileSync(f, "utf8"))).map((f) => f.slice(src.length));
    expect(users.sort()).toEqual(["App.tsx", "hooks/useStream.ts"]);
    const app = readFileSync(join(src, "App.tsx"), "utf8");
    const read = [...app.matchAll(/\be\.data\.(\w+)/g)].map((m) => m[1]);
    expect(read.length).toBeGreaterThan(0);
    expect(new Set(read)).toEqual(new Set(["session_id", "case_id"]));
  });
});
