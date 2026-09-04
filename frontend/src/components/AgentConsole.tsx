import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import type { AuditEventRead, RecoverySummaryRead } from "../api/types";
import { formatMoney, formatNumber, formatRelative, humanize } from "../lib/format";
import { Button } from "./ui";

type StageState = "pending" | "running" | "completed";

const STAGES: { key: string; label: string; events: string[]; blurb: string }[] = [
  {
    key: "discover",
    label: "Discover",
    events: ["recovery_case_created", "payment_ingested"],
    blurb: "Failed payments with no case yet",
  },
  {
    key: "diagnose",
    label: "Diagnose",
    events: ["diagnosis_requested", "ai_diagnosis_created", "recovery_recommendation_created"],
    blurb: "Groq proposes a cause + action",
  },
  {
    key: "decide",
    label: "Decide",
    events: ["policy_evaluated", "policy_rechecked"],
    blurb: "Deterministic policy allows / blocks",
  },
  {
    key: "act",
    label: "Act",
    events: ["execution_requested", "execution_started", "payment_link_created"],
    blurb: "Execute only permitted actions",
  },
  {
    key: "observe",
    label: "Observe",
    events: ["payment_confirmed", "payment_not_recovered", "webhook_unmatched_payment_link"],
    blurb: "Verify the outcome via webhook",
  },
];

const RECENT_MS = 8000;

function stageState(events: AuditEventRead[], stageEvents: string[], now: number): StageState {
  const matches = events.filter((e) => stageEvents.includes(e.event_type));
  if (matches.length === 0) return "pending";
  const newest = Math.max(...matches.map((e) => new Date(e.created_at).getTime()));
  return now - newest < RECENT_MS ? "running" : "completed";
}

export function AgentConsole({
  summary,
  onActivity,
}: {
  summary: RecoverySummaryRead | null;
  onActivity: () => void;
}) {
  const statusRes = useApiResource(() => api.getOrchestratorStatus(), []);
  const eventsRes = useApiResource(() => api.listAuditEvents({ page_size: 40 }), []);
  const [running, setRunning] = useState(false);
  const [banner, setBanner] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const refreshStatus = statusRes.refetch;
  const refreshEvents = eventsRes.refetch;

  useEffect(() => {
    const ms = running ? 1500 : 5000;
    const t = setInterval(() => {
      setNow(Date.now());
      refreshStatus();
      refreshEvents();
    }, ms);
    return () => clearInterval(t);
  }, [running, refreshStatus, refreshEvents]);

  async function runCycle() {
    setRunning(true);
    setBanner(null);
    try {
      const r = await api.runRecoveryCycle();
      setBanner({
        tone: "info",
        text:
          `Cycle finished in ${r.duration_seconds}s — discovered ${r.cases_discovered}, ` +
          `diagnosed ${r.cases_diagnosed}, approved ${r.approved}, stopped ${r.stopped}, ` +
          `escalated ${r.escalated}, executed ${r.cases_executed}` +
          (r.auto_execute ? "." : ". Auto-execute is off, so approved cases wait for a human."),
      });
      refreshStatus();
      refreshEvents();
      onActivity();
    } catch (e) {
      setBanner({ tone: "danger", text: e instanceof Error ? e.message : "Cycle failed." });
    } finally {
      setRunning(false);
    }
  }

  const status = statusRes.state.status === "success" ? statusRes.state.data : null;
  const events: AuditEventRead[] =
    eventsRes.state.status === "success" ? eventsRes.state.data.items : [];

  const agentState = running ? "running" : (status?.agent_state ?? "idle");
  const stateLabel =
    agentState === "running" ? "Running" : agentState === "error" ? "Error" : "Idle";

  const newest = events[0];
  const newestRecent = newest && now - new Date(newest.created_at).getTime() < RECENT_MS;
  const currentOp = newestRecent
    ? `${humanize(newest.event_type)} · ${newest.entity_type} ${newest.entity_id.slice(0, 8)}`
    : running
      ? "Starting cycle…"
      : "No cycle in progress";

  const lc = status?.last_cycle ?? null;
  const recoveredCount = summary?.cases_by_status?.recovered ?? 0;

  const counts: { label: string; value: number; tone?: string }[] = [
    { label: "Discovered", value: lc?.cases_discovered ?? 0 },
    { label: "Diagnosed", value: lc?.cases_diagnosed ?? 0 },
    { label: "Approved", value: lc?.approved ?? 0, tone: "info" },
    { label: "Stopped", value: lc?.stopped ?? 0, tone: "warn" },
    { label: "Escalated", value: lc?.escalated ?? 0, tone: "warn" },
    { label: "Executed", value: lc?.cases_executed ?? 0 },
    {
      label: "Recovered (lifetime)",
      value: recoveredCount,
      tone: recoveredCount > 0 ? "ok" : undefined,
    },
  ];

  return (
    <section className={`agent agent--${agentState}`}>
      <div className="agent__bar">
        <div className="agent__id">
          <span className="agent__eyebrow">Autonomous recovery agent</span>
          <span className="agent__loop">discover → diagnose → decide → act → observe</span>
        </div>
        <div className="agent__statewrap">
          <span className={`agent__state agent__state--${agentState}`}>
            <span className="agent__state-dot" />
            {stateLabel}
          </span>
          <Button variant="primary" onClick={runCycle} disabled={running}>
            {running ? "Running cycle…" : "Start recovery cycle"}
          </Button>
        </div>
      </div>

      <div className="agent__op">
        <span className="agent__op-label">Current operation</span>
        <span className="agent__op-value">{currentOp}</span>
      </div>

      <ol className="pipeline">
        {STAGES.map((stage, i) => {
          const st = stageState(events, stage.events, now);
          return (
            <li key={stage.key} className={`pipeline__stage pipeline__stage--${st}`}>
              <span className="pipeline__index">{i + 1}</span>
              <span className="pipeline__label">{stage.label}</span>
              <span className="pipeline__blurb">{stage.blurb}</span>
              <span className="pipeline__status">{st}</span>
            </li>
          );
        })}
      </ol>

      <div className="agent__counts">
        {counts.map((c) => (
          <div key={c.label} className={`agent__count${c.tone ? ` agent__count--${c.tone}` : ""}`}>
            <span className="agent__count-value">{formatNumber(c.value)}</span>
            <span className="agent__count-label">{c.label}</span>
          </div>
        ))}
      </div>

      <div className="agent__meta">
        <span>
          Cycles completed: <b>{formatNumber(status?.cycles_completed ?? 0)}</b>
        </span>
        <span>
          Background loop:{" "}
          <b>
            {status?.enabled
              ? status.running
                ? "running"
                : "enabled, idle"
              : "operator-triggered"}
          </b>
        </span>
        <span>
          Auto-execute:{" "}
          <b>{status?.auto_execute ? "on" : "off (approved cases held for review)"}</b>
        </span>
        {status?.average_ai_latency_ms != null && (
          <span>
            Mean AI latency: <b>{status.average_ai_latency_ms} ms</b> ({status.recent_ai_diagnoses}{" "}
            recent)
          </span>
        )}
        {lc && (
          <span>
            Last cycle: <b>{formatRelative(lc.completed_at)}</b> · {lc.duration_seconds}s
          </span>
        )}
        {summary && (
          <span>
            Confirmed recovered: <b>{formatMoney(summary.confirmed_recovered_revenue)}</b>
          </span>
        )}
      </div>

      {banner && <p className={`agent__banner agent__banner--${banner.tone}`}>{banner.text}</p>}

      <p className="agent__note">
        The agent is the loop above — it discovers, asks Groq for a diagnosis, then defers to the
        deterministic policy engine before any action. The LLM never authorizes a financial action.
        Execution follows <code>ORCHESTRATOR_AUTO_EXECUTE</code>; a single operator-triggered cycle
        runs to completion and cannot be interrupted mid-request.
      </p>
    </section>
  );
}
