import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeEventSource } from "../test/FakeEventSource";
import { StreamClient, streamUrl, useStream, type StreamEvent } from "./useStream";

const ES = FakeEventSource as unknown as typeof EventSource;
const last = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

describe("streamUrl", () => {
  it("carries filters and the resume point", () => {
    expect(streamUrl("/api/stream", {})).toBe("/api/stream");
    expect(streamUrl("/api/stream", { types: ["case.event", "score.batch"], sessionId: 3 }, 41))
      .toBe("/api/stream?types=case.event%2Cscore.batch&session_id=3&last_event_id=41");
  });
});

describe("StreamClient", () => {
  it("delivers typed events and tracks the last event id", () => {
    const got: StreamEvent[] = [];
    const c = new StreamClient({ EventSourceImpl: ES, onEvent: (e) => got.push(e) });
    c.start();
    last().open();
    last().emit("score.batch", 7, { count: 3, states: { normal: 3 }, synthetic: true });
    last().emit("case.event", 8, { case_id: 1, status: "closed", note: "n" });
    expect(got.map((e) => [e.id, e.type])).toEqual([[7, "score.batch"], [8, "case.event"]]);
    // only identifiers and types get through, whatever the server sends
    expect(got[0].data).toEqual({ synthetic: true });
    expect(got[1].data).toEqual({ case_id: 1 });
    expect(c.lastEventId).toBe(8);
    expect(c.status).toBe("open");
  });

  it("reconnects after an error and resumes after the last event it saw", () => {
    const got: number[] = [];
    const c = new StreamClient({ EventSourceImpl: ES, onEvent: (e) => got.push(e.id),
                                 retryMs: 500 });
    c.start();
    const first = last();
    first.open();
    first.emit("replay.progress", 10, {});
    first.emit("replay.progress", 11, {});
    first.fail();
    expect(first.closed).toBe(true);
    expect(c.status).toBe("reconnecting");
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(499);
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    const second = last();
    expect(second).not.toBe(first);
    expect(second.url).toBe("/api/stream?last_event_id=11");
    second.open();
    second.emit("replay.progress", 11, {}); // a repeat the server should not send: dropped
    second.emit("case.event", 12, {});
    expect(got).toEqual([10, 11, 12]);
  });

  it("backs off while the server stays away, and resets once connected", () => {
    const c = new StreamClient({ EventSourceImpl: ES, retryMs: 100, maxRetryMs: 400 });
    c.start();
    const delays: number[] = [];
    for (let i = 0; i < 4; i++) {
      const before = FakeEventSource.instances.length;
      last().fail();
      let waited = 0;
      while (FakeEventSource.instances.length === before) {
        vi.advanceTimersByTime(50);
        waited += 50;
      }
      delays.push(waited);
    }
    expect(delays).toEqual([100, 200, 400, 400]);
    last().open();
    last().fail();
    const n = FakeEventSource.instances.length;
    vi.advanceTimersByTime(100);
    expect(FakeEventSource.instances.length).toBe(n + 1);
  });

  it("before any event, a reconnect asks for nothing older than now", () => {
    const c = new StreamClient({ EventSourceImpl: ES, retryMs: 10, types: ["case.event"] });
    c.start();
    last().fail();
    vi.advanceTimersByTime(10);
    expect(last().url).toBe("/api/stream?types=case.event");
  });

  it("stop closes the source and cancels a pending reconnect", () => {
    const c = new StreamClient({ EventSourceImpl: ES, retryMs: 100 });
    c.start();
    last().fail();
    c.stop();
    vi.advanceTimersByTime(1000);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(c.status).toBe("closed");
  });
});

describe("useStream", () => {
  it("exposes status and the last event id, and cleans up on unmount", () => {
    const onEvent = vi.fn();
    const { result, unmount } = renderHook(() =>
      useStream({ EventSourceImpl: ES, onEvent, retryMs: 100 }));
    expect(result.current.status).toBe("connecting");
    act(() => last().open());
    expect(result.current.status).toBe("open");
    act(() => last().emit("case.event", 5, { case_id: 2, synthetic: true }));
    expect(result.current.lastEventId).toBe(5);
    expect(Object.keys(result.current).sort()).toEqual(["lastEventId", "status"]);
    expect(onEvent).toHaveBeenCalledOnce();
    act(() => last().fail());
    expect(result.current.status).toBe("reconnecting");
    act(() => vi.advanceTimersByTime(100));
    expect(last().url).toBe("/api/stream?last_event_id=5");
    const source = last();
    unmount();
    expect(source.closed).toBe(true);
  });
});
