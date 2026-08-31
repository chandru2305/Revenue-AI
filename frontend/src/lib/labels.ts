// Human-readable labels + semantic tone for the enums the API returns.
// `tone` maps to badge modifier classes (badge--ok / --danger / --warn /
// --info / neutral).

import type {
  ActorType,
  PolicyReasonCode,
  RecoveryCaseStatus,
} from "../api/types";

export type Tone = "ok" | "danger" | "warn" | "info" | "neutral";

export function statusTone(status: RecoveryCaseStatus): Tone {
  switch (status) {
    case "recovered":
      return "ok";
    case "failed":
    case "stopped":
    case "escalated":
      return "danger";
    case "ineligible":
      return "neutral";
    case "approved":
    case "executing":
    case "diagnosing":
    case "recommended":
    case "policy_review":
      return "info";
    default:
      return "neutral";
  }
}

export function actorTone(actor: ActorType): Tone {
  switch (actor) {
    case "policy_engine":
      return "warn";
    case "ai":
      return "info";
    case "human":
      return "ok";
    default:
      return "neutral";
  }
}

export const REASON_CODE_LABEL: Record<PolicyReasonCode, string> = {
  max_retries_reached: "Max retries reached",
  recovery_window_expired: "Recovery window expired",
  max_contacts_reached: "Contact cap reached",
  confidence_below_threshold: "Confidence below threshold",
  action_not_eligible_for_status: "Action not eligible for status",
  amount_out_of_bounds: "Amount out of bounds",
  terminal_state_protected: "Terminal state protected",
};

// Audit event types that represent a deterministic policy decision.
export const POLICY_EVENT_TYPES = ["policy_evaluated", "policy_rechecked"] as const;

// Audit event types that land a case in a terminal state.
export const TERMINAL_EVENT_HINTS = ["recovered", "stopped", "escalated", "failed"];

export function policyPhase(eventType: string): string {
  if (eventType === "policy_rechecked") return "Pre-execution re-check";
  if (eventType === "policy_evaluated") return "Diagnosis gate";
  return "—";
}

export const ENTITY_TYPES = [
  "recovery_case",
  "recovery_attempt",
  "payment",
  "webhook",
] as const;

export const ACTOR_TYPES: ActorType[] = ["system", "ai", "policy_engine", "human"];
