/** Stands in for the browser's EventSource: records instances, lets a test push events. */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  readyState = FakeEventSource.CONNECTING;
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  private listeners = new Map<string, ((e: MessageEvent) => void)[]>();
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }
  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.(new Event("open"));
  }
  emit(type: string, id: number, data: object) {
    const e = new MessageEvent(type, { data: JSON.stringify(data), lastEventId: String(id) });
    for (const fn of this.listeners.get(type) ?? []) fn(e);
  }
  fail() {
    this.readyState = FakeEventSource.CLOSED;
    this.onerror?.(new Event("error"));
  }
}

