import { useState } from "react";
import { api } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import { actorTone, POLICY_EVENT_TYPES, TERMINAL_EVENT_HINTS } from "../lib/labels";
import { formatDateTime, formatRelative, humanize } from "../lib/format";
import { Badge, ErrorState, JsonBlock, LoadingState } from "./ui";

function itemClass(eventType: string, payload: Record<string, unknown>): string {
  if ((POLICY_EVENT_TYPES as readonly string[]).includes(eventType)) return "timeline__item timeline__item--policy";
  const to = String(payload["to_status"] ?? "");
  if (TERMINAL_EVENT_HINTS.includes(to)) return "timeline__item timeline__item--terminal";
  return "timeline__item";
}

export function Timeline({ caseId, reloadKey }: { caseId: string; reloadKey: number }) {
  const { state } = useApiResource(() => api.getRecoveryCaseTimeline(caseId), [caseId, reloadKey]);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  if (state.status === "loading") return <LoadingState label="Loading timeline…" />;
  if (state.status === "error") return <ErrorState message={state.error} />;
  if (state.data.events.length === 0)
    return <p className="subtle">No events yet — run a diagnosis to start the trail.</p>;

  return (
    <ol className="timeline">
      {state.data.events.map((event) => {
        const hasPayload = Object.keys(event.payload).length > 0;
        const isOpen = open[event.id];
        return (
          <li key={event.id} className={itemClass(event.event_type, event.payload)}>
            <div className="timeline__head">
              <span className="timeline__event">{humanize(event.event_type)}</span>
              <Badge tone={actorTone(event.actor_type)}>{event.actor_type}</Badge>
              <span className="timeline__time" title={formatDateTime(event.created_at)}>
                {formatRelative(event.created_at)}
              </span>
              {hasPayload && (
                <button
                  type="button"
                  className="copy-btn"
                  onClick={() => setOpen((o) => ({ ...o, [event.id]: !o[event.id] }))}
                >
                  {isOpen ? "hide" : "details"}
                </button>
              )}
            </div>
            {hasPayload && isOpen && (
              <div className="timeline__payload">
                <JsonBlock value={event.payload} />
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
