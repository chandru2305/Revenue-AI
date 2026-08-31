import { useState } from "react";
import { api, ApiError } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import type { DiagnosisResponse, ExecutionResponse } from "../api/types";
import { formatDateTime, formatMoney, formatPercent, humanize } from "../lib/format";
import { statusTone } from "../lib/labels";
import { PolicyDecisionBadge, ReasonCodes } from "./PolicyBadge";
import { Timeline } from "./Timeline";
import { Badge, Button, Copyable, Drawer, ErrorState, JsonBlock, LoadingState } from "./ui";

const DIAGNOSABLE = new Set(["discovered", "eligible", "failed"]);

type ActionState<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; result: T }
  | { status: "error"; message: string };

function errText(err: unknown, suffix409: string): string {
  if (err instanceof ApiError) {
    return `${err.message}${err.status === 409 ? ` (${suffix409})` : ""}`;
  }
  return "Could not reach the RecoverAI API.";
}

export function CaseDrawer({ caseId, onClose, onMutated }: { caseId: string; onClose: () => void; onMutated: () => void }) {
  const { state, refetch } = useApiResource(() => api.getRecoveryCase(caseId), [caseId]);
  const [reloadKey, setReloadKey] = useState(0);
  const [diagnosis, setDiagnosis] = useState<ActionState<DiagnosisResponse>>({ status: "idle" });
  const [execution, setExecution] = useState<ActionState<ExecutionResponse>>({ status: "idle" });

  function afterMutation() {
    refetch();
    setReloadKey((k) => k + 1);
    onMutated();
  }

  async function runDiagnose() {
    setDiagnosis({ status: "loading" });
    try {
      const result = await api.diagnoseRecoveryCase(caseId);
      setDiagnosis({ status: "success", result });
      afterMutation();
    } catch (err) {
      setDiagnosis({ status: "error", message: errText(err, "already diagnosed") });
    }
  }

  async function runExecute() {
    setExecution({ status: "loading" });
    try {
      const result = await api.executeRecoveryCase(caseId);
      setExecution({ status: "success", result });
      afterMutation();
    } catch (err) {
      setExecution({ status: "error", message: errText(err, "already executed") });
    }
  }

  return (
    <Drawer
      title={
        <span className="row-flex">
          Case investigation
          {state.status === "success" && (
            <Badge tone={statusTone(state.data.status)}>{humanize(state.data.status)}</Badge>
          )}
        </span>
      }
      onClose={onClose}
    >
      {state.status === "loading" && <LoadingState label="Loading case…" />}
      {state.status === "error" && <ErrorState message={state.error} />}

      {state.status === "success" && (
        <>
          <section>
            <div className="spread" style={{ marginBottom: 8 }}>
              <h3 style={{ fontSize: 13 }}>Payment context</h3>
              <Copyable text={caseId} display={`case ${caseId.slice(0, 8)}`} />
            </div>
            <dl className="kv">
              <dt>Amount</dt>
              <dd className="strong">
                {formatMoney(state.data.payment.amount, state.data.payment.currency)}
              </dd>
              <dt>Revenue at risk</dt>
              <dd>{formatMoney(state.data.revenue_at_risk, state.data.payment.currency)}</dd>
              <dt>Failure reason</dt>
              <dd>{humanize(state.data.payment.failure_reason)}</dd>
              <dt>Method</dt>
              <dd>{humanize(state.data.payment.payment_method_type)}</dd>
              <dt>Payment attempts</dt>
              <dd>{state.data.payment.attempt_number}</dd>
              <dt>Failed at</dt>
              <dd>{formatDateTime(state.data.payment.updated_at)}</dd>
            </dl>
          </section>

          <section>
            <h3 style={{ fontSize: 13, marginBottom: 8 }}>Diagnosis &amp; recommendation</h3>
            {state.data.recommended_action === null ? (
              <p className="subtle">No diagnosis run yet.</p>
            ) : (
              <dl className="kv">
                <dt>Category</dt>
                <dd>{humanize(state.data.diagnosis_category)}</dd>
                <dt>Confidence</dt>
                <dd>{formatPercent(state.data.recovery_confidence, 0)}</dd>
                <dt>Recommended action</dt>
                <dd>
                  <Badge tone="info">{humanize(state.data.recommended_action)}</Badge>
                </dd>
                {diagnosis.status === "success" && diagnosis.result.decision_source && (
                  <>
                    <dt>Decision source</dt>
                    <dd>
                      <Badge tone={diagnosis.result.decision_source === "ai" ? "info" : "neutral"}>
                        {diagnosis.result.decision_source}
                      </Badge>
                    </dd>
                  </>
                )}
              </dl>
            )}
            {state.data.diagnosis_notes && (
              <p className="inline-note" style={{ marginTop: 10 }}>
                {state.data.diagnosis_notes}
              </p>
            )}
          </section>

          <section>
            <h3 style={{ fontSize: 13, marginBottom: 6 }}>Deterministic policy gate</h3>
            <p className="subtle" style={{ marginBottom: 10 }}>
              An AI recommendation is never an authorization. This is what the policy engine decided.
            </p>
            {state.data.policy_version || diagnosis.status === "success" ? (
              <dl className="kv">
                <dt>Decision</dt>
                <dd>
                  <PolicyDecisionBadge
                    decision={
                      diagnosis.status === "success"
                        ? diagnosis.result.policy_decision
                        : state.data.status === "approved" || state.data.status === "executing" || state.data.status === "recovered"
                          ? "allow"
                          : null
                    }
                  />
                </dd>
                {diagnosis.status === "success" && (
                  <>
                    <dt>Reason codes</dt>
                    <dd>
                      <ReasonCodes codes={diagnosis.result.policy_reason_codes} />
                    </dd>
                  </>
                )}
                <dt>Policy version</dt>
                <dd className="cell-mono">{state.data.policy_version ?? "—"}</dd>
              </dl>
            ) : (
              <p className="subtle">Not evaluated yet.</p>
            )}
          </section>

          <section>
            <h3 style={{ fontSize: 13, marginBottom: 8 }}>Actions</h3>
            <div className="row-flex">
              <Button
                variant="primary"
                onClick={runDiagnose}
                disabled={diagnosis.status === "loading" || !DIAGNOSABLE.has(state.data.status)}
              >
                {diagnosis.status === "loading" ? "Diagnosing…" : "Run AI diagnosis"}
              </Button>
              <Button
                onClick={runExecute}
                disabled={execution.status === "loading" || state.data.status !== "approved"}
              >
                {execution.status === "loading" ? "Executing…" : "Execute recovery"}
              </Button>
            </div>
            {!DIAGNOSABLE.has(state.data.status) && state.data.status !== "approved" && (
              <p className="subtle" style={{ marginTop: 8 }}>
                Status “{humanize(state.data.status)}” — no operator action available.
              </p>
            )}
            <p className="subtle" style={{ marginTop: 8 }}>
              The dashboard only <em>requests</em> execution. The backend independently re-checks
              approval, policy, and amount before calling Razorpay.
            </p>
            {diagnosis.status === "error" && <ErrorState message={diagnosis.message} />}
            {execution.status === "error" && <ErrorState message={execution.message} />}
          </section>

          {state.data.status === "recovered" && (
            <p className="inline-note" style={{ borderLeftColor: "var(--ok)" }}>
              ✓ Recovered {formatMoney(state.data.recovered_amount, state.data.payment.currency)} —
              confirmed by a signature-verified webhook, not by link creation alone.
            </p>
          )}

          {state.data.payment_requests.length > 0 && (
            <section>
              <h3 style={{ fontSize: 13, marginBottom: 8 }}>Payment links</h3>
              {state.data.payment_requests.map((pr) => (
                <div key={pr.id} className="spread" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                  <span className="cell-mono">
                    {pr.short_url ? (
                      <a href={pr.short_url} target="_blank" rel="noreferrer">
                        {pr.short_url}
                      </a>
                    ) : (
                      pr.provider_reference
                    )}
                  </span>
                  <span className="row-flex">
                    <Badge tone={pr.status === "paid" ? "ok" : pr.status === "expired" || pr.status === "cancelled" ? "danger" : "neutral"}>
                      {pr.status}
                    </Badge>
                    <span className="subtle">
                      {formatMoney(pr.amount_paid, pr.currency)} / {formatMoney(pr.amount, pr.currency)}
                    </span>
                  </span>
                </div>
              ))}
            </section>
          )}

          {execution.status === "success" && (
            <section>
              <h3 style={{ fontSize: 13, marginBottom: 8 }}>Execution result</h3>
              <JsonBlock value={execution.result} />
            </section>
          )}

          <section>
            <h3 style={{ fontSize: 13, marginBottom: 8 }}>Timeline</h3>
            <Timeline caseId={caseId} reloadKey={reloadKey} />
          </section>
        </>
      )}
    </Drawer>
  );
}
