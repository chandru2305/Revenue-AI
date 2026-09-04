import { useState } from "react";
import { api } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import type { AuditEventRead, RecoveryCaseStatus, SystemInfo } from "../api/types";
import { formatMoney, formatNumber, formatPercent, formatRelative, humanize } from "../lib/format";
import { statusTone, type Tone } from "../lib/labels";
import { AgentConsole } from "../components/AgentConsole";
import { ActivityFeed } from "../components/ActivityFeed";
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

const TERMINALish = new Set(["recovered", "stopped", "escalated", "ineligible", "failed"]);

export function OverviewPage({ system }: { system?: SystemInfo | null }) {
  const recovery = useApiResource(() => api.getRecoverySummary(), []);
  const evalRun = useApiResource(() => api.getEvaluationSummary(), []);
  const policyEvents = useApiResource(
    () => api.listAuditEvents({ entity_type: "recovery_case", event_type: "policy_evaluated", page_size: 100 }),
    [],
  );
  const [modal, setModal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<{ tone: Tone; text: string } | null>(null);

  function refreshAll() {
    recovery.refetch();
    policyEvents.refetch();
  }

  async function seedBatch() {
    setBusy(true);
    setBanner(null);
    try {
      const r = await api.seedDemoBatch();
      setBanner({
        tone: "warn",
        text:
          `Recovery batch: ${r.cases_processed} cases, diagnosed by ${r.ai_model} → ` +
          `${formatMoney(r.summary.confirmed_recovered_revenue)} recovered of ` +
          `${formatMoney(r.summary.eligible_revenue)} eligible ` +
          `(${formatPercent(r.summary.recovery_rate)}). ${r.provenance}`,
      });
      refreshAll();
    } catch (e) {
      setBanner({ tone: "danger", text: e instanceof Error ? e.message : "Demo batch failed." });
    } finally {
      setBusy(false);
    }
  }

  if (recovery.state.status === "loading") return <LoadingState label="Loading recovery posture…" />;
  if (recovery.state.status === "error") return <ErrorState message={recovery.state.error} />;

  const live = recovery.state.data;
  const statuses = Object.entries(live.cases_by_status) as [RecoveryCaseStatus, number][];
  const total = statuses.reduce((sum, [, n]) => sum + n, 0);
  const byStatus = live.cases_by_status;
  const activeCases = total - statuses.reduce((s, [k, n]) => s + (TERMINALish.has(k) ? n : 0), 0);

  const policyRows: AuditEventRead[] =
    policyEvents.state.status === "success" ? policyEvents.state.data.items : [];
  const allowed = policyRows.filter((e) => (e.payload as { decision?: string }).decision === "allow").length;
  const blocked = policyRows.filter((e) => (e.payload as { decision?: string }).decision === "block").length;
  const recentBlocks = policyRows
    .filter((e) => (e.payload as { decision?: string }).decision === "block")
    .slice(0, 6);

  return (
    <>
      <div className="toolbar">
        <p className="page-intro" style={{ marginBottom: 0 }}>
          A bounded autonomous agent: it discovers failed payments, asks Groq to diagnose them,
          lets the deterministic policy engine decide, executes only permitted actions, and confirms
          recovery through a signature-verified webhook. Revenue counts as recovered only on that
          webhook — never on link creation.
        </p>
        <div className="toolbar__spacer" />
        <Button variant="primary" onClick={() => setModal(true)}>Ingest payment event</Button>
        <Button onClick={seedBatch} disabled={busy} title="Developer tool — seeds 30 curated input scenarios and runs each through the real diagnosis + policy pipeline. Disabled in production.">
          {busy ? "Seeding…" : "Seed demo batch (dev)"}
        </Button>
      </div>

      {banner && (
        <div
          className="inline-note"
          style={{
            marginBottom: 18,
            borderLeftColor:
              banner.tone === "danger"
                ? "var(--danger)"
                : banner.tone === "warn"
                  ? "var(--warn)"
                  : "var(--accent)",
          }}
        >
          {banner.text}
        </div>
      )}

      <AgentConsole summary={live} onActivity={refreshAll} />

      <div className="grid grid--stats section-gap">
        <StatTile
          label="Recovered revenue"
          value={formatMoney(live.confirmed_recovered_revenue)}
          tone={live.confirmed_recovered_revenue > 0 ? "ok" : undefined}
          hint="webhook-confirmed only"
        />
        <StatTile label="Recovery rate" value={formatPercent(live.recovery_rate)} hint="confirmed ÷ eligible" />
        <StatTile label="Recovered cases" value={formatNumber(byStatus.recovered ?? 0)} />
        <StatTile label="Policy allowed" value={formatNumber(allowed)} tone={allowed > 0 ? "ok" : undefined} />
        <StatTile label="Policy blocked" value={formatNumber(blocked)} tone={blocked > 0 ? "danger" : undefined} />
        <StatTile label="Escalated" value={formatNumber(byStatus.escalated ?? 0)} />
        <StatTile label="Stopped" value={formatNumber(byStatus.stopped ?? 0)} />
        <StatTile label="Active recovery cases" value={formatNumber(activeCases)} />
      </div>

      <div className="grid grid--2 section-gap">
        <Card title="Case distribution">
          {total === 0 ? (
            <EmptyState
              title="No recovery cases yet."
              hint="Ingest a failed payment or run a recovery cycle to populate this deployment."
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

        <Card title="Live agent activity" actions={<span className="subtle">audit trail · auto-refresh</span>}>
          <ActivityFeed limit={22} />
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

        <Card title="Recent policy blocks" actions={<span className="subtle">deterministic gate</span>}>
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

      {system?.demo_mode && (
        <p className="demo-disclosure section-gap">
          <b>Demo mode.</b> Payment confirmation is simulated because Razorpay Test Mode credentials
          are not configured here. Recovery orchestration, policy enforcement, webhook processing, and
          audit logic all use the same application pipeline. The recovered-revenue figure is a real
          measurement over real rows — not a Razorpay result.
        </p>
      )}

      {modal && <IngestPaymentModal onClose={() => setModal(false)} onDone={refreshAll} />}
    </>
  );
}
