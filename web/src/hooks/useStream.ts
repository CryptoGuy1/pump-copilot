import { useEffect, useRef, useState } from "react";
import type { components } from "../api/schema";

export const EVENT_TYPES = ["replay.progress", "score.batch", "case.event"] as const;
export type EventType = (typeof EVENT_TYPES)[number];

type S = components["schemas"];
/** The payload of each event type, from the API's StreamEvent models. */
export interface EventData {
  "replay.progress": S["ReplayProgressEvent"];
  "score.batch": S["ScoreBatchEvent"];
  "case.event": S["CaseEvent"];
}

export type StreamEvent = { [K in EventType]: { id: number; type: K; data: EventData[K] } }[
  EventType];

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed";

export interface StreamOptions {
  url?: string;
  types?: EventType[];
  sessionId?: number;
  onEvent?: (e: StreamEvent) => void;
  onStatus?: (s: StreamStatus) => void;
  EventSourceImpl?: typeof EventSource;
  retryMs?: number;
  maxRetryMs?: number;
}

/** /api/stream with filters, resuming after `lastEventId` (EventSource cannot set headers on a
 * connection it did not open itself, so the resume point goes in the query). */
export function streamUrl(base: string, o: Pick<StreamOptions, "types" | "sessionId">,
                          lastEventId?: number | null): string {
  const q = new URLSearchParams();
  if (o.types?.length) q.set("types", o.types.join(","));
  if (o.sessionId != null) q.set("session_id", String(o.sessionId));
  if (lastEventId != null) q.set("last_event_id", String(lastEventId));
  const s = q.toString();
  return s ? `${base}?${s}` : base;
}

/** One live connection to /api/stream. On any error it closes the EventSource and opens a
 * new one after a backoff, asking for events after the last id it delivered, so nothing is
 * missed or repeated across reconnects. */
export class StreamClient {
  status: StreamStatus = "closed";
  lastEventId: number | null = null;
  private source: EventSource | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private delay: number;
  private readonly o: Required<Pick<StreamOptions, "url" | "retryMs" | "maxRetryMs">> &
    StreamOptions;

  constructor(options: StreamOptions = {}) {
    // explicit defaults: a caller passing `url: undefined` must still get the default
    this.o = { ...options, url: options.url ?? "/api/stream",
               retryMs: options.retryMs ?? 1000, maxRetryMs: options.maxRetryMs ?? 30000 };
    this.delay = this.o.retryMs;
  }

  start(): void {
    this.connect("connecting");
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.source?.close();
    this.source = null;
    this.setStatus("closed");
  }

  private setStatus(s: StreamStatus) {
    this.status = s;
    this.o.onStatus?.(s);
  }

  private connect(status: StreamStatus) {
    this.timer = null;
    this.setStatus(status);
    const Impl = this.o.EventSourceImpl ?? EventSource;
    const es = new Impl(streamUrl(this.o.url, this.o, this.lastEventId));
    this.source = es;
    es.onopen = () => {
      this.delay = this.o.retryMs;
      this.setStatus("open");
    };
    es.onerror = () => {
      if (this.source !== es) return;
      es.close();
      this.source = null;
      this.setStatus("reconnecting");
      this.timer = setTimeout(() => this.connect("reconnecting"), this.delay);
      this.delay = Math.min(this.delay * 2, this.o.maxRetryMs);
    };
    for (const type of EVENT_TYPES) {
      es.addEventListener(type, (e: MessageEvent) => {
        const id = Number(e.lastEventId);
        if (this.lastEventId != null && id <= this.lastEventId) return;
        this.lastEventId = id;
        this.o.onEvent?.({ id, type, data: JSON.parse(e.data) } as StreamEvent);
      });
    }
  }
}

/** React wrapper: connection status, the last event id and the most recent events. */
export function useStream(options: StreamOptions = {}, keep = 50) {
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [lastEventId, setLast] = useState<number | null>(null);
  const onEvent = useRef(options.onEvent);
  onEvent.current = options.onEvent;
  const { url, types, sessionId, EventSourceImpl, retryMs, maxRetryMs } = options;
  const typesKey = types?.join(",");

  useEffect(() => {
    const client = new StreamClient({
      url, types, sessionId, EventSourceImpl, retryMs, maxRetryMs, onStatus: setStatus,
      onEvent: (e) => {
        setLast(e.id);
        setEvents((prev) => [...prev.slice(-(keep - 1)), e]);
        onEvent.current?.(e);
      },
    });
    client.start();
    return () => client.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, typesKey, sessionId, EventSourceImpl, retryMs, maxRetryMs, keep]);

  return { status, events, lastEventId };
}
