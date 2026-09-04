import { useEffect } from "react";
import { api } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import type { AuditEventRead } from "../api/types";
import { formatMoney, humanize } from "../lib/format";
import { ErrorState, LoadingState } from "./ui";

type Lane = "ai" | "policy" | "exec" | "webhook" | "system";

const EXEC_EVENTS = new Set([
  "execution_requested",
  "execution_started",
  "payment_link_created",
  "provider_ambiguous_result",
  "provider_state_reconciliation",
]);
const WEBHOOK_EVENTS = new Set([
  "payment_confirmed",
  "payment_not_recovered",
  "webhook_unmatched_payment_link",
]);

function laneOf(e: AuditEventRead): Lane {
  if (e.actor_type === "ai") return "ai";
  if (e.actor_type === "policy_engine") return "policy";
  if (EXEC_EVENTS.has(e.event_type)) return "exec";
  if (WEBHOOK_EVENTS.has(e.event_type)) return "webhook";
  return "system";
}

const LANE_TAG: Record<Lane, string> = {
  ai: "AI",
  policy: "POLICY",
  exec: "EXEC",
  webhook: "WEBHOOK",
  system: "SYSTEM",
};

function describe(e: AuditEventRead): string {
  const p = e.payload as Record<string, unknown>;
  switch (e.event_type) {
    case "ai_diagnosis_created":
      return [
        p.diagnosis_category && humanize(String(p.diagnosis_category)),
        p.model ?? "safe fallback",
        p.latency_ms != null && `${p.latency_ms} ms`,
      ]
        .filter(Boolean)
        .join(" · ");
    case "recovery_recommendation_created":
      return `${humanize(String(p.recommended_action ?? "—"))} @ ${Math.round(
        Number(p.recovery_confidence ?? 0) * 100,
      )}% confidence`;
    case "policy_evaluated":
    case "policy_rechecked": {
      const codes = (p.reason_codes as string[] | undefined) ?? [];
      return `${String(p.decision ?? "—").toUpperCase()}${
        codes.length ? ` — ${codes.map((c) => humanize(c)).join(", ")}` : ""
      }`;
    }
    case "recovery_case_status_changed":
      return `${humanize(String(p.from_status ?? "—"))} → ${humanize(String(p.to_status ?? "—"))}`;
    case "payment_link_created":
      return `${formatMoney(Number(p.amount ?? 0), String(p.currency ?? "INR"))} link created`;
    case "payment_confirmed":
      return `${formatMoney(Number(p.amount_paid ?? 0))} confirmed by webhook`;
    case "payment_not_recovered":
      return `link ended ${String(p.status ?? "unpaid")} — not recovered`;
    case "recovery_cycle_completed":
      return `discovered ${p.cases_discovered}, diagnosed ${p.cases_diagnosed}, executed ${p.cases_executed}, escalated ${p.escalated}`;
    case "recovery_case_created":
      return `revenue at risk ${formatMoney(Number(p.revenue_at_risk ?? 0))}`;
    default:
      return "";
  }
}

export function ActivityFeed({ limit = 24 }: { limit?: number }) {
  const { state, refetch } = useApiResource(() => api.listAuditEvents({ page_size: limit }), [limit]);

  useEffect(() => {
    const t = setInterval(refetch, 5000);
    return () => clearInterval(t);
  }, [refetch]);

  if (state.status === "loading") return <LoadingState label="Loading agent activity…" />;
  if (state.status === "error") return <ErrorState message={state.error} />;
  if (state.data.items.length === 0)
    return <p className="subtle">No activity yet — start a recovery cycle or run the batch.</p>;

  return (
    <ul className="feed">
      {state.data.items.map((e) => {
        const lane = laneOf(e);
        const detail = describe(e);
        return (
          <li key={e.id} className={`feed__row feed__row--${lane}`}>
            <span className="feed__time">
              {new Date(e.created_at).toLocaleTimeString(undefined, { hour12: false })}
            </span>
            <span className={`feed__tag feed__tag--${lane}`}>{LANE_TAG[lane]}</span>
            <span className="feed__event">{humanize(e.event_type)}</span>
            {detail && <span className="feed__detail">{detail}</span>}
          </li>
        );
      })}
    </ul>
  );
}
