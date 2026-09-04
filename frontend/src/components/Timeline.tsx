import { useState } from "react";
import type { TimelineEvent } from "../api/types";
import { actorTone, POLICY_EVENT_TYPES, TERMINAL_EVENT_HINTS } from "../lib/labels";
import { formatDateTime, formatRelative, humanize } from "../lib/format";
import { Badge, JsonBlock } from "./ui";

// The lifecycle stage each event belongs to, so the timeline reads as
// payment failure → case creation → diagnosis → policy → execution →
// provider result → webhook → terminal, not just a flat event list.
const STAGE: Record<string, string> = {
  payment_ingested: "Payment",
  recovery_case_created: "Case",
  diagnosis_requested: "Diagnosis",
  ai_diagnosis_created: "Diagnosis",
  recovery_recommendation_created: "Diagnosis",
  policy_evaluated: "Policy",
  policy_rechecked: "Policy",
  execution_requested: "Execution",
  execution_started: "Execution",
  payment_link_created: "Execution",
  provider_ambiguous_result: "Execution",
  provider_state_reconciliation: "Execution",
  payment_confirmed: "Verification",
  payment_not_recovered: "Verification",
  webhook_unmatched_payment_link: "Verification",
  recovery_case_status_changed: "Transition",
};

function itemClass(eventType: string, payload: Record<string, unknown>): string {
  if ((POLICY_EVENT_TYPES as readonly string[]).includes(eventType))
    return "timeline__item timeline__item--policy";
  const to = String(payload["to_status"] ?? "");
  if (TERMINAL_EVENT_HINTS.includes(to)) return "timeline__item timeline__item--terminal";
  return "timeline__item";
}

export function Timeline({ events }: { events: TimelineEvent[] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});

  if (events.length === 0)
    return <p className="subtle">No events yet — run a diagnosis to start the trail.</p>;

  return (
    <ol className="timeline">
      {events.map((event) => {
        const hasPayload = Object.keys(event.payload).length > 0;
        const isOpen = open[event.id];
        return (
          <li key={event.id} className={itemClass(event.event_type, event.payload)}>
            <div className="timeline__head">
              {STAGE[event.event_type] && (
                <span className="timeline__stage">{STAGE[event.event_type]}</span>
              )}
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
