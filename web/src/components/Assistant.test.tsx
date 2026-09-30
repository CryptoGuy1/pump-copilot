import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// the API client keeps the fetch it finds when it is imported: route that one to the test
const net = vi.hoisted(() => {
  const h: { fn: (r: Request) => Promise<Response> } = { fn: async () => new Response("{}") };
  globalThis.fetch = ((r: Request) => h.fn(r)) as typeof fetch;
  // the app calls the API by relative URL (the dev server proxies /api); resolve it here
  const Base = globalThis.Request;
  globalThis.Request = class extends Base {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" && input.startsWith("/") ? `http://localhost${input}`
                                                               : input, init);
    }
  } as typeof Request;
  return h;
});

import { AssistantPanel } from "./Assistant";

/** One assistant request per question: the evidence summary comes from its own read-only
 * endpoint (no model, not logged), fetched ahead; each question is one POST. */

const answer = { claims: [{ text: "The case has 3 evidence windows.", evidence_refs: ["E1"],
                            kind: "observation" }], suggested_checks: [], draft_note: "n" };
const summary = { case_id: 7, label: "Evidence summary", answer,
                  check: { passed: true, reasons: [] }, evidence: [],
                  calibration_status: "not_applicable", context_hash: "h", synthetic: false,
                  model_version: [], assumptions: ["A1"] };
const response = { ...summary, run_id: 1, served: "template", provider: "anthropic",
                   model: "m", rejected: null, fallback_reason: "no ANTHROPIC_API_KEY",
                   latency_ms: 1 };

describe("the assistant panel", () => {
  afterEach(() => cleanup());

  it("shows the summary at once and sends one request per question", async () => {
    const posts: unknown[] = [];
    let release: () => void = () => {};
    net.fn = async (req: Request) => {
      const json = (x: object) => new Response(JSON.stringify(x),
        { status: 200, headers: { "content-type": "application/json" } });
      if (req.url.endsWith("/evidence-summary")) return json(summary);
      if (req.method === "POST" && req.url.endsWith("/assistant")) {
        posts.push(await req.clone().json());
        await new Promise<void>((r) => { release = r; });
        return json(response);
      }
      return json({});
    };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}>
      <AssistantPanel caseId={7} actor="op" canNote onHighlight={() => {}} /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Summarize this case" }));
    // the summary at once, with the answer on its way
    expect((await screen.findByTestId("assistant-badge")).textContent).toContain("Evidence summary");
    expect(screen.getByTestId("assistant-working")).toBeTruthy();
    expect(posts).toEqual([{ question: "Summarize this case", provider: "auto" }]);
    await act(async () => release());
    expect((await screen.findByTestId("assistant-fallback")).textContent).toContain("no API key");
    expect(screen.getByTestId("stored-units").textContent).toContain("vibration in m/s");
    fireEvent.click(screen.getByRole("button", { name: "Which signal drove it?" }));
    await act(async () => release());
    expect(posts).toHaveLength(2);  // one per question, never a second for the summary
  });
});
