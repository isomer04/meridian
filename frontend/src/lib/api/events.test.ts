import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectRunEvents } from "./events";

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly listeners = new Map<string, (event: MessageEvent<string>) => void>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor(readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, listener as (event: MessageEvent<string>) => void);
  }

  emit(type: string, data: string, lastEventId = "") {
    this.listeners.get(type)?.({ data, lastEventId } as MessageEvent<string>);
  }
}

describe("connectRunEvents", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.NEXT_PUBLIC_MERIDIAN_API_ORIGIN;
  });

  it("encodes the stream URL, reports connection state, and closes cleanly", () => {
    process.env.NEXT_PUBLIC_MERIDIAN_API_ORIGIN = "https://api.example.test";
    const onStatus = vi.fn();
    const disconnect = connectRunEvents("run/one", 42, vi.fn(), onStatus);
    const source = MockEventSource.instances[0];

    expect(source.url).toBe(
      "https://api.example.test/api/v1/runs/run%2Fone/events?last_event_id=42",
    );
    source.onopen?.();
    source.onerror?.();
    expect(onStatus.mock.calls).toEqual([["open"], ["reconnecting"]]);

    disconnect();
    expect(source.close).toHaveBeenCalledOnce();
  });

  it("normalizes valid events and prefers the SSE event id", () => {
    const onEvent = vi.fn();
    connectRunEvents("run-1", 0, onEvent, vi.fn());

    MockEventSource.instances[0].emit(
      "run.event",
      JSON.stringify({ id: 7, type: "run.completed", payload: {} }),
      "12",
    );

    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({ id: 12, kind: "run.completed", type: "run.completed" }),
    );
  });

  it("reports malformed stream messages without forwarding them", () => {
    const onEvent = vi.fn();
    const onStatus = vi.fn();
    connectRunEvents("run-1", 0, onEvent, onStatus);

    MockEventSource.instances[0].emit("run.event", "not-json");

    expect(onEvent).not.toHaveBeenCalled();
    expect(onStatus).toHaveBeenCalledWith("error");
  });
});
