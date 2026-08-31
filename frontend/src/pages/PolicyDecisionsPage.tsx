import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import type { AuditEventRead } from "../api/types";
import { formatDateTime, formatPercent, formatRelative, humanize } from "../lib/format";
import { policyPhase, REASON_CODE_LABEL } from "../lib/labels";
import { CaseDrawer } from "../components/CaseDrawer";
import { PolicyDecisionBadge, ReasonCodes } from "../components/PolicyBadge";
import { Button, Copyable, EmptyState, ErrorState, LoadingState, StatTile } from "../components/ui";

interface PolicyRow {
  event: AuditEventRead;
  decision: string;
  reasonCodes: string[];
  proposedAction: string | null;
  policyVersion: string | null;
}

function toRow(e: AuditEventRead): PolicyRow {
  const p = e.payload as {
    decision?: string;
    reason_codes?: string[];
    proposed_action?: string;
    policy_version?: string;
  };
  return {
    event: e,
    decision: p.decision ?? "—",
    reasonCodes: p.reason_codes ?? [],
    proposedAction: p.proposed_action ?? null,
    policyVersion: p.policy_version ?? null,
  };
}

async function fetchPolicyEvents(): Promise<AuditEventRead[]> {
  const [evaluated, rechecked] = await Promise.all([
    api.listAuditEvents({ entity_type: "recovery_case", event_type: "policy_evaluated", page_size: 100 }),
    api.listAuditEvents({ entity_type: "recovery_case", event_type: "policy_rechecked", page_size: 100 }),
  ]);
  return [...evaluated.items, ...rechecked.items].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function PolicyDecisionsPage() {
  const { state, refetch } = useApiResource(fetchPolicyEvents, []);
  const [decisionFilter, setDecisionFilter] = useState<"all" | "allow" | "block">("all");
  const [selected, setSelected] = useState<string | null>(null);

  const rows = useMemo(() => {
    if (state.status !== "success") return [];
    return state.data
      .map(toRow)
      .filter((r) => decisionFilter === "all" || r.decision === decisionFilter);
  }, [state, decisionFilter]);

  const stats = useMemo(() => {
    if (state.status !== "success") return null;
    const all = state.data.map(toRow);
    const blocks = all.filter((r) => r.decision === "block");
    const reasonTally = new Map<string, number>();
    for (const r of blocks) for (const c of r.reasonCodes) reasonTally.set(c, (reasonTally.get(c) ?? 0) + 1);
    const topReason = [...reasonTally.entries()].sort((a, b) => b[1] - a[1])[0];
    return {
      total: all.length,
      blockRate: all.length ? blocks.length / all.length : 0,
      cases: new Set(all.map((r) => r.event.entity_id)).size,
      topReason: topReason ? REASON_CODE_LABEL[topReason[0] as keyof typeof REASON_CODE_LABEL] ?? topReason[0] : "—",
    };
  }, [state]);

  return (
    <>
      <p className="page-intro">
        Every ALLOW / BLOCK the deterministic policy engine produced — at diagnosis time
        (<span className="cell-mono">policy_evaluated</span>) and again with fresh data immediately
        before execution (<span className="cell-mono">policy_rechecked</span>). No AI can bypass this
        gate; these rows are the proof.
      </p>

      {state.status === "loading" && <LoadingState label="Loading policy decisions…" />}
      {state.status === "error" && <ErrorState message={state.error} />}

      {state.status === "success" && stats && (
        <>
          <div className="grid grid--stats">
            <StatTile label="Decisions recorded" value={stats.total} />
            <StatTile label="Block rate" value={formatPercent(stats.blockRate)} tone={stats.blockRate > 0 ? "danger" : undefined} />
            <StatTile label="Cases gated" value={stats.cases} />
            <StatTile label="Top block reason" value={<span style={{ fontSize: 15 }}>{stats.topReason}</span>} />
          </div>

          <div className="filter-chips" style={{ marginTop: 18 }}>
            {(["all", "allow", "block"] as const).map((f) => (
              <button
                key={f}
                type="button"
                className={f === decisionFilter ? "filter-chip filter-chip--active" : "filter-chip"}
                onClick={() => setDecisionFilter(f)}
              >
                {f === "all" ? "All" : f.toUpperCase()}
              </button>
            ))}
            <div className="toolbar__spacer" />
            <Button size="sm" variant="ghost" onClick={refetch}>
              Refresh
            </Button>
          </div>

          {rows.length === 0 ? (
            <EmptyState title="No policy decisions match this filter." />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Phase</th>
                    <th>Decision</th>
                    <th>Proposed action</th>
                    <th>Reason codes</th>
                    <th>Policy</th>
                    <th>Case</th>
                    <th>Correlation</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.event.id} className="row--click" onClick={() => setSelected(r.event.entity_id)}>
                      <td className="subtle nowrap" title={formatDateTime(r.event.created_at)}>
                        {formatRelative(r.event.created_at)}
                      </td>
                      <td className="nowrap">{policyPhase(r.event.event_type)}</td>
                      <td>
                        <PolicyDecisionBadge decision={r.decision === "allow" ? "allow" : r.decision === "block" ? "block" : null} />
                      </td>
                      <td>{r.proposedAction ? humanize(r.proposedAction) : "—"}</td>
                      <td>
                        <ReasonCodes codes={r.reasonCodes} />
                      </td>
                      <td className="cell-mono">{r.policyVersion ?? "—"}</td>
                      <td className="cell-mono">{r.event.entity_id.slice(0, 8)}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        {r.event.correlation_id ? (
                          <Copyable text={r.event.correlation_id} display={r.event.correlation_id.slice(0, 10)} />
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="table-foot">
                <span>{rows.length} decision{rows.length === 1 ? "" : "s"}</span>
              </div>
            </div>
          )}
        </>
      )}

      {selected && <CaseDrawer caseId={selected} onClose={() => setSelected(null)} onMutated={refetch} />}
    </>
  );
}
