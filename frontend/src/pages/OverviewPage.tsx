import { useState } from "react";
import { api } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import type { AuditEventRead, RecoveryCaseStatus } from "../api/types";
import { formatMoney, formatNumber, formatPercent, formatRelative, humanize } from "../lib/format";
import { statusTone, type Tone } from "../lib/labels";
import { IngestPaymentModal } from "../components/IngestPaymentModal";
import { ReasonCodes } from "../components/PolicyBadge";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, StatTile } from "../components/ui";

const TONE_COLOR: Record<Tone, string> = {
  ok: "var(--ok)",
  danger: "var(--danger)",
  warn: "var(--warn)",
  info: "var(--accent)",
  neutral: "var(--text-subtle)",
};

export function OverviewPage() {
  const recovery = useApiResource(() => api.getRecoverySummary(), []);
  const evalRun = useApiResource(() => api.getEvaluationSummary(), []);
  const policyEvents = useApiResource(
    () => api.listAuditEvents({ entity_type: "recovery_case", event_type: "policy_evaluated", page_size: 50 }),
    [],
  );
  const [modal, setModal] = useState(false);

  function refreshAll() {
    recovery.refetch();
    policyEvents.refetch();
  }

  if (recovery.state.status === "loading") return <LoadingState label="Loading recovery posture…" />;
  if (recovery.state.status === "error") return <ErrorState message={recovery.state.error} />;

  const live = recovery.state.data;
  const statuses = Object.entries(live.cases_by_status) as [RecoveryCaseStatus, number][];
  const total = statuses.reduce((sum, [, n]) => sum + n, 0);

  const recentBlocks =
    policyEvents.state.status === "success"
      ? policyEvents.state.data.items.filter((e) => (e.payload as { decision?: string }).decision === "block").slice(0, 6)
      : [];

  return (
    <>
      <div className="toolbar">
        <div>
          <p className="page-intro" style={{ marginBottom: 0 }}>
            Computed live from this database’s <span className="cell-mono">recovery_cases</span>. Revenue counts
            as recovered only after a signature-verified Razorpay webhook — never on link creation.
          </p>
        </div>
        <div className="toolbar__spacer" />
        <Button onClick={() => setModal(true)}>Ingest payment</Button>
        <Button
          variant="primary"
          onClick={async () => {
            await api.runDiscovery();
            refreshAll();
          }}
        >
          Run discovery sweep
        </Button>
      </div>

      <div className="grid grid--stats">
        <StatTile label="Cases tracked" value={formatNumber(live.cases_total)} hint={`${formatNumber(live.cases_eligible)} eligible`} />
        <StatTile label="Eligible revenue" value={formatMoney(live.eligible_revenue)} />
        <StatTile
          label="Confirmed recovered"
          value={formatMoney(live.confirmed_recovered_revenue)}
          tone={live.confirmed_recovered_revenue > 0 ? "ok" : undefined}
        />
        <StatTile label="Recovery rate" value={formatPercent(live.recovery_rate)} hint="confirmed ÷ eligible" />
        <StatTile label="Outstanding" value={formatMoney(live.outstanding_revenue)} tone={live.outstanding_revenue > 0 ? "danger" : undefined} />
      </div>

      <div className="section-gap">
        <Card title="Case distribution">
          {total === 0 ? (
            <EmptyState
              title="No recovery cases yet."
              hint="Ingest a failed payment or run a discovery sweep to populate this deployment."
              action={<Button variant="primary" onClick={() => setModal(true)}>Ingest payment</Button>}
            />
          ) : (
            <>
              <div className="dist-bar">
                {statuses.map(([status, n]) => (
                  <div
                    key={status}
                    className="dist-bar__seg"
                    style={{ width: `${(n / total) * 100}%`, background: TONE_COLOR[statusTone(status)] }}
                    title={`${humanize(status)}: ${n}`}
                  />
                ))}
              </div>
              <div className="dist-legend">
                {statuses.map(([status, n]) => (
                  <span key={status} className="dist-legend__item">
                    <span className="dist-legend__swatch" style={{ background: TONE_COLOR[statusTone(status)] }} />
                    {humanize(status)} · <span className="strong">{n}</span>
                  </span>
                ))}
              </div>
            </>
          )}
        </Card>
      </div>

      <div className="grid grid--2 section-gap">
        <Card title="Assurance snapshot">
          <dl className="kv">
            <dt>Escalation rate</dt>
            <dd>{formatPercent(live.escalation_rate)}</dd>
            <dt>Stop rate</dt>
            <dd>{formatPercent(live.stop_rate)}</dd>
            <dt>Provider failure rate</dt>
            <dd>{formatPercent(live.provider_failure_rate)}</dd>
            <dt>Recovery attempts</dt>
            <dd>{formatNumber(live.recovery_attempts)}</dd>
            <dt>Payment links created</dt>
            <dd>{formatNumber(live.successful_payment_links_created)}</dd>
          </dl>
        </Card>

        <Card
          title="Recent policy blocks"
          actions={<span className="subtle">deterministic gate</span>}
        >
          {policyEvents.state.status === "loading" && <LoadingState label="Loading…" />}
          {policyEvents.state.status === "error" && <ErrorState message={policyEvents.state.error} />}
          {policyEvents.state.status === "success" && recentBlocks.length === 0 && (
            <p className="subtle">No BLOCK decisions recorded.</p>
          )}
          {recentBlocks.length > 0 && (
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 10 }}>
              {recentBlocks.map((e: AuditEventRead) => (
                <li key={e.id} className="spread" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
                  <span>
                    <Badge tone="warn">{humanize(String((e.payload as { proposed_action?: string }).proposed_action ?? "—"))}</Badge>{" "}
                    <ReasonCodes codes={(e.payload as { reason_codes?: string[] }).reason_codes ?? []} />
                  </span>
                  <span className="subtle nowrap">{formatRelative(e.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="section-gap">
        <Card title="Synthetic evaluation (separate methodology)">
          {evalRun.state.status === "success" && evalRun.state.data.status === "no_evaluation_run" && (
            <p className="subtle">
              No evaluation report on this machine. Run{" "}
              <span className="cell-mono">python -m evaluation.run_evaluation --count 500 --seed 42</span>.
            </p>
          )}
          {evalRun.state.status === "success" && evalRun.state.data.status === "ok" && (
            <div className="grid grid--stats">
              <StatTile label="Strategy" value={humanize(evalRun.state.data.strategy)} />
              <StatTile label="Dataset" value={`${formatNumber(evalRun.state.data.dataset_count)} cases`} hint={`seed ${evalRun.state.data.dataset_seed}`} />
              <StatTile label="Recovery rate (sim)" value={formatPercent(evalRun.state.data.financial?.recovery_rate)} />
              <StatTile label="Policy violations" value={formatNumber(evalRun.state.data.safety?.policy_violations)} />
            </div>
          )}
          {evalRun.state.status === "loading" && <LoadingState label="Loading…" />}
          {evalRun.state.status === "error" && <ErrorState message={evalRun.state.error} />}
        </Card>
      </div>

      {modal && <IngestPaymentModal onClose={() => setModal(false)} onDone={refreshAll} />}
    </>
  );
}
