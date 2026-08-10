import type { EventRecord } from "./generated";

export type StreamState = "open" | "reconnecting" | "error";

export function connectRunEvents(
  runId: string,
  lastEventId: number,
  onEvent: (event: EventRecord) => void,
  onStatus: (state: StreamState) => void,
) {
  const origin = process.env.NEXT_PUBLIC_MERIDIAN_API_ORIGIN ?? "";
  const replay = lastEventId
    ? `?last_event_id=${encodeURIComponent(lastEventId)}`
    : "";
  const source = new EventSource(
    `${origin}/api/v1/runs/${encodeURIComponent(runId)}/events${replay}`,
  );

  source.onopen = () => onStatus("open");
  source.onerror = () => onStatus("reconnecting");
  source.addEventListener("run.event", (message) => {
    try {
      const event = JSON.parse(
        (message as MessageEvent<string>).data,
      ) as EventRecord;
      onEvent({
        ...event,
        kind: event.type,
        id: Number((message as MessageEvent).lastEventId) || event.id,
      });
    } catch {
      onStatus("error");
    }
  });

  return () => source.close();
}
