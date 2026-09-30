import { describe, expect, it } from "vitest";
import type { Schema } from "./api/client";
import { scoreSeries, signalsToDraw } from "./fleet";
import { applyTheme, readTheme } from "./theme";

type State = Schema<"ScoreRow">["state"];
const row = (signal_name: string, window_end: string, state: State,
             score: number | null = 1): Schema<"ScoreRow"> => ({
  signal_name, window_end, window_start: window_end, state, score, stretch: 0,
  abstention_reason: null, median: null, band_low: null, band_high: null, model_id: "m",
  model_version: "v", synthetic: true,
});

describe("fleet helpers", () => {
  it("draws not-normal signals first and leaves abstentions out", () => {
    expect(signalsToDraw({ a: "normal", b: "review_suggested", c: "insufficient_evidence" }, 2))
      .toEqual(["b", "c"]);
    const s = scoreSeries([row("p", "2024-10-30T10:00:00Z", "normal", 2),
                           row("p", "2024-10-30T10:06:00Z", "insufficient_evidence", null)],
                          ["p"]);
    expect(s[0].points).toHaveLength(1);
  });
});

describe("theme", () => {
  it("reads ?theme=, ignores unknown values and remembers the choice", () => {
    localStorage.clear();
    expect(readTheme("")).toBe("industrial");  // the lead direction
    expect(readTheme("?theme=daylight")).toBe("daylight");
    expect(readTheme("?theme=neon")).toBe("industrial");
    applyTheme("aurora");
    expect(document.documentElement.dataset.theme).toBe("aurora");
    expect(readTheme("")).toBe("aurora");  // remembered without the parameter
    expect(readTheme("?theme=industrial")).toBe("industrial");  // the URL wins
  });
});
