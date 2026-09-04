import type { PolicyLimits, RecoveryCaseDetail, TimelineEvent } from "../api/types";
import { formatMoney, humanize } from "../lib/format";

type Check = { label: string; detail: string; pass: boolean };

const ACTIVE_ACTIONS = new Set(["retry_payment", "send_payment_link", "send_reminder"]);
const CONTACT_ACTIONS = new Set(["send_payment_link", "send_reminder"]);

function daysSince(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000));
}

/**
 * The AI proposes; the deterministic policy engine decides. This renders that
 * boundary for one case: the recommendation, each policy check against the
 * limits the backend actually enforces (from /system/info), and the final
 * verdict — which is taken from the recorded `policy_evaluated` audit event,
 * never recomputed here as if it were authoritative.
 */
export function PolicyLadder({
  detail,
  policy,
  decisionEvent,
}: {
  detail: RecoveryCaseDetail;
  policy: PolicyLimits | null;
  decisionEvent: TimelineEvent | null;
}) {
  const action = detail.recommended_action;
  if (!action) return <p className="subtle">No recommendation yet — run AI diagnosis first.</p>;

  const confidence = detail.recovery_confidence ?? 0;
  const amount = detail.payment.amount;

  const recorded = decisionEvent?.payload as
    | { decision?: string; reason_codes?: string[] }
    | undefined;
  const verdict = recorded?.decision ?? null; // "allow" | "block" | null
  const reasonCodes = recorded?.reason_codes ?? [];

  const checks: Check[] = [];
  if (policy) {
    if (action === "retry_payment") {
      checks.push({
        label: "Retry limit",
        detail: `${detail.current_attempt_number} / ${policy.max_retry_count}`,
        pass: detail.current_attempt_number < policy.max_retry_count,
      });
    }
    checks.push({
      label: "Amount ceiling",
      detail: `${formatMoney(amount)} / ${formatMoney(policy.max_recovery_amount)}`,
      pass: amount > 0 && amount <= policy.max_recovery_amount,
    });
    if (ACTIVE_ACTIONS.has(action)) {
      const highValue = amount >= policy.high_value_amount_threshold;
      const threshold = highValue
        ? policy.high_value_min_confidence_threshold
        : policy.min_confidence_threshold;
      checks.push({
        label: highValue ? "Confidence (high-value)" : "Confidence",
        detail: `${Math.round(confidence * 100)}% / ${Math.round(threshold * 100)}%`,
        pass: confidence >= threshold,
      });
      checks.push({
        label: "Recovery window",
        detail: `${daysSince(detail.created_at)}d / ${policy.max_recovery_window_days}d`,
        pass: daysSince(detail.created_at) <= policy.max_recovery_window_days,
      });
    }
    if (CONTACT_ACTIONS.has(action)) {
      checks.push({
        label: "Customer contacts",
        detail: `${detail.customer_contact_count} / ${policy.max_customer_contacts}`,
        pass: detail.customer_contact_count < policy.max_customer_contacts,
      });
    }
  }

  const isFallback = action === "escalate" || action === "stop";

  return (
    <div className="ladder">
      <div className="ladder__node ladder__node--ai">
        <span className="ladder__cap">AI recommendation</span>
        <span className="ladder__action">{humanize(action)}</span>
        <span className="ladder__sub">{Math.round(confidence * 100)}% confidence · from Groq</span>
      </div>

      <div className="ladder__arrow" aria-hidden="true">↓</div>

      <div className="ladder__node ladder__node--policy">
        <span className="ladder__cap">Policy engine · deterministic</span>
        {isFallback ? (
          <p className="ladder__note">
            {humanize(action)} is a safe fallback — always permitted, never gated on confidence.
          </p>
        ) : checks.length === 0 ? (
          <p className="ladder__note">Policy limits unavailable.</p>
        ) : (
          <ul className="ladder__checks">
            {checks.map((c) => (
              <li key={c.label} className={c.pass ? "ok" : "bad"}>
                <span className="ladder__check-label">{c.label}</span>
                <span className="ladder__check-detail">{c.detail}</span>
                <span className="ladder__check-mark">{c.pass ? "✓" : "✕"}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="ladder__arrow" aria-hidden="true">↓</div>

      <div
        className={`ladder__node ladder__node--verdict ladder__node--${
          verdict === "allow" ? "allow" : verdict === "block" ? "block" : "unknown"
        }`}
      >
        <span className="ladder__cap">Final decision</span>
        {verdict === "allow" && <span className="ladder__verdict">✓ ALLOWED</span>}
        {verdict === "block" && (
          <>
            <span className="ladder__verdict">✕ BLOCKED</span>
            <span className="ladder__sub">
              {reasonCodes.map((c) => humanize(c)).join(", ") || "policy rule violated"} → case{" "}
              {humanize(detail.status)}
            </span>
          </>
        )}
        {!verdict && <span className="ladder__sub">Not evaluated yet.</span>}
      </div>
    </div>
  );
}
