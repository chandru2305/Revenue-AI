import { useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import type {
  DiagnosisResponse,
  ExecutionResponse,
  SystemInfo,
  TimelineEvent,
} from "../api/types";
import { formatDateTime, formatMoney, formatPercent, humanize } from "../lib/format";
import { statusTone } from "../lib/labels";
import { PolicyLadder } from "./PolicyLadder";
import { ReasonCodes } from "./PolicyBadge";
import { Timeline } from "./Timeline";
import { Badge, Button, Copyable, Drawer, ErrorState, LoadingState } from "./ui";

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

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="case-sec">
      <h3 className="case-sec__title">{title}</h3>
      {children}
    </section>
  );
}

export function CaseDrawer({
  caseId,
  system,
  onClose,
  onMutated,
}: {
  caseId: string;
  system?: SystemInfo | null;
  onClose: () => void;
  onMutated: () => void;
}) {
  const caseRes = useApiResource(() => api.getRecoveryCase(caseId), [caseId]);
  const [reloadKey, setReloadKey] = useState(0);
  const timelineRes = useApiResource(
    () => api.getRecoveryCaseTimeline(caseId),
    [caseId, reloadKey],
  );
  const [diagnosis, setDiagnosis] = useState<ActionState<DiagnosisResponse>>({ status: "idle" });
  const [execution, setExecution] = useState<ActionState<ExecutionResponse>>({ status: "idle" });

  function afterMutation() {
    caseRes.refetch();
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

  const detail = caseRes.state.status === "success" ? caseRes.state.data : null;
  const events: TimelineEvent[] =
    timelineRes.state.status === "success" ? timelineRes.state.data.events : [];

  const policyEvent =
    [...events].reverse().find((e) => e.event_type === "policy_evaluated") ??
    [...events].reverse().find((e) => e.event_type === "policy_rechecked") ??
    null;
  const verifyEvent =
    [...events].reverse().find(
      (e) => e.event_type === "payment_confirmed" || e.event_type === "payment_not_recovered",
    ) ?? null;

  const latestAttempt = detail && detail.attempts.length > 0 ? detail.attempts[detail.attempts.length - 1] : null;
  const latestRequest =
    detail && detail.payment_requests.length > 0
      ? detail.payment_requests[detail.payment_requests.length - 1]
      : null;

  return (
    <Drawer
      title={
        <span className="row-flex">
          Case investigation
          {detail && <Badge tone={statusTone(detail.status)}>{humanize(detail.status)}</Badge>}
        </span>
      }
      onClose={onClose}
    >
      {caseRes.state.status === "loading" && <LoadingState label="Loading case…" />}
      {caseRes.state.status === "error" && <ErrorState message={caseRes.state.error} />}

      {detail && (
        <>
          <Section title="Payment">
            <div className="spread" style={{ marginBottom: 8 }}>
              <span className="subtle">Recovery state</span>
              <Copyable text={caseId} display={`case ${caseId.slice(0, 8)}`} />
            </div>
            <dl className="kv">
              <dt>Payment ID</dt>
              <dd className="cell-mono">{detail.payment.id.slice(0, 18)}…</dd>
              <dt>Amount</dt>
              <dd className="strong">{formatMoney(detail.payment.amount, detail.payment.currency)}</dd>
              <dt>Revenue at risk</dt>
              <dd>{formatMoney(detail.revenue_at_risk, detail.payment.currency)}</dd>
              <dt>Failure reason</dt>
              <dd>{humanize(detail.payment.failure_reason)}</dd>
              <dt>Method</dt>
              <dd>{humanize(detail.payment.payment_method_type)}</dd>
              <dt>Payment attempts</dt>
              <dd>{detail.payment.attempt_number}</dd>
              <dt>Failed at</dt>
              <dd>{formatDateTime(detail.payment.updated_at)}</dd>
              <dt>Recovery state</dt>
              <dd>
                <Badge tone={statusTone(detail.status)}>{humanize(detail.status)}</Badge>
              </dd>
            </dl>
          </Section>

          <Section title="AI diagnosis">
            {detail.recommended_action === null ? (
              <p className="subtle">Not diagnosed yet. Groq has not been asked about this case.</p>
            ) : (
              <>
                <dl className="kv">
                  <dt>Diagnosis</dt>
                  <dd>{humanize(detail.diagnosis_category)}</dd>
                  <dt>Confidence</dt>
                  <dd>{formatPercent(detail.recovery_confidence, 0)}</dd>
                  <dt>Recommended action</dt>
                  <dd>
                    <Badge tone="info">{humanize(detail.recommended_action)}</Badge>
                  </dd>
                  {diagnosis.status === "success" && diagnosis.result.decision_source && (
                    <>
                      <dt>Decision source</dt>
                      <dd>
                        <Badge tone={diagnosis.result.decision_source === "ai" ? "info" : "neutral"}>
                          {diagnosis.result.decision_source === "ai"
                            ? `Groq${diagnosis.result.ai_model ? ` · ${diagnosis.result.ai_model}` : ""}`
                            : "safe fallback (AI unavailable)"}
                        </Badge>
                      </dd>
                      {diagnosis.result.ai_latency_ms != null && (
                        <>
                          <dt>Latency</dt>
                          <dd>{diagnosis.result.ai_latency_ms} ms</dd>
                        </>
                      )}
                    </>
                  )}
                </dl>
                {detail.diagnosis_notes && (
                  <p className="inline-note" style={{ marginTop: 10 }}>
                    {detail.diagnosis_notes}
                  </p>
                )}
              </>
            )}
          </Section>

          <Section title="Policy decision">
            <p className="subtle" style={{ marginBottom: 10 }}>
              An AI recommendation is not an authorization. This is the deterministic gate.
            </p>
            <PolicyLadder detail={detail} policy={system?.policy ?? null} decisionEvent={policyEvent} />
            <dl className="kv" style={{ marginTop: 12 }}>
              <dt>Policy version</dt>
              <dd className="cell-mono">{detail.policy_version ?? "—"}</dd>
              {policyEvent && (
                <>
                  <dt>Reason codes</dt>
                  <dd>
                    <ReasonCodes
                      codes={(policyEvent.payload.reason_codes as string[] | undefined) ?? []}
                    />
                  </dd>
                </>
              )}
            </dl>
          </Section>

          <Section title="Execution">
            {!latestAttempt ? (
              <p className="subtle">
                No recovery action executed. Execution only happens after policy ALLOW and an
                executable action.
              </p>
            ) : (
              <dl className="kv">
                <dt>Action attempted</dt>
                <dd>
                  <Badge tone="info">{humanize(latestAttempt.action)}</Badge>
                </dd>
                <dt>Attempt number</dt>
                <dd>{detail.current_attempt_number}</dd>
                <dt>Provider</dt>
                <dd>{latestAttempt.provider ?? "—"}</dd>
                <dt>Execution state</dt>
                <dd>
                  <Badge
                    tone={
                      latestAttempt.status === "succeeded"
                        ? "ok"
                        : latestAttempt.status === "failed"
                          ? "danger"
                          : "neutral"
                    }
                  >
                    {humanize(latestAttempt.status)}
                  </Badge>
                </dd>
                {latestAttempt.provider_reference && (
                  <>
                    <dt>Provider ref</dt>
                    <dd className="cell-mono">{latestAttempt.provider_reference}</dd>
                  </>
                )}
                {latestAttempt.failure_message && (
                  <>
                    <dt>Provider result</dt>
                    <dd>{latestAttempt.failure_message}</dd>
                  </>
                )}
                {latestRequest && (
                  <>
                    <dt>Payment link</dt>
                    <dd>
                      {latestRequest.short_url ? (
                        <a href={latestRequest.short_url} target="_blank" rel="noreferrer">
                          {latestRequest.short_url}
                        </a>
                      ) : (
                        <span className="cell-mono">{latestRequest.provider_reference}</span>
                      )}
                    </dd>
                    <dt>Link status</dt>
                    <dd>
                      <Badge
                        tone={
                          latestRequest.status === "paid"
                            ? "ok"
                            : latestRequest.status === "expired" || latestRequest.status === "cancelled"
                              ? "danger"
                              : "neutral"
                        }
                      >
                        {latestRequest.status}
                      </Badge>{" "}
                      <span className="subtle">
                        {formatMoney(latestRequest.amount_paid, latestRequest.currency)} /{" "}
                        {formatMoney(latestRequest.amount, latestRequest.currency)}
                      </span>
                    </dd>
                  </>
                )}
              </dl>
            )}
          </Section>

          <Section title="Verification">
            {!verifyEvent ? (
              <p className="subtle">
                No payment webhook yet. Revenue is only ever counted after a signature-verified
                Razorpay webhook — never on link creation.
              </p>
            ) : (
              <dl className="kv">
                <dt>Webhook event</dt>
                <dd>{humanize(verifyEvent.event_type)}</dd>
                <dt>Signature</dt>
                <dd>
                  <Badge tone="ok">verified before processing</Badge>
                </dd>
                <dt>Payment status</dt>
                <dd>{humanize(String(verifyEvent.payload.status ?? "—"))}</dd>
                <dt>Amount confirmed</dt>
                <dd className="strong">
                  {formatMoney(Number(verifyEvent.payload.amount_paid ?? 0))}
                </dd>
              </dl>
            )}
            {detail.status === "recovered" && (
              <p className="inline-note" style={{ borderLeftColor: "var(--ok)", marginTop: 10 }}>
                ✓ Recovered {formatMoney(detail.recovered_amount, detail.payment.currency)} — confirmed
                by a signature-verified webhook, not by link creation alone.
              </p>
            )}
            {system?.demo_mode && (
              <p className="subtle" style={{ marginTop: 8 }}>
                Demo mode: the confirming webhook is simulated (no Razorpay Test Mode key). The
                verification code path, state machine, and audit entries are the real ones.
              </p>
            )}
          </Section>

          <Section title="Actions">
            <div className="row-flex">
              <Button
                variant="primary"
                onClick={runDiagnose}
                disabled={diagnosis.status === "loading" || !DIAGNOSABLE.has(detail.status)}
              >
                {diagnosis.status === "loading" ? "Diagnosing…" : "Run AI diagnosis"}
              </Button>
              <Button
                onClick={runExecute}
                disabled={execution.status === "loading" || detail.status !== "approved"}
              >
                {execution.status === "loading" ? "Executing…" : "Execute recovery"}
              </Button>
            </div>
            {!DIAGNOSABLE.has(detail.status) && detail.status !== "approved" && (
              <p className="subtle" style={{ marginTop: 8 }}>
                Status “{humanize(detail.status)}” — no operator action available.
              </p>
            )}
            <p className="subtle" style={{ marginTop: 8 }}>
              The dashboard only <em>requests</em> these. The backend independently re-checks
              approval, policy, and amount before calling the provider.
            </p>
            {diagnosis.status === "error" && <ErrorState message={diagnosis.message} />}
            {execution.status === "error" && <ErrorState message={execution.message} />}
          </Section>

          <Section title="Audit timeline">
            {timelineRes.state.status === "loading" && <LoadingState label="Loading timeline…" />}
            {timelineRes.state.status === "error" && (
              <ErrorState message={timelineRes.state.error} />
            )}
            {timelineRes.state.status === "success" && <Timeline events={events} />}
          </Section>
        </>
      )}
    </Drawer>
  );
}
