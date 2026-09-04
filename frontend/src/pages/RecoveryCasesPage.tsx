import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import type { RecoveryCaseStatus, SystemInfo } from "../api/types";
import { formatMoney, formatPercent, formatRelative, humanize } from "../lib/format";
import { statusTone } from "../lib/labels";
import { CaseDrawer } from "../components/CaseDrawer";
import { IngestPaymentModal } from "../components/IngestPaymentModal";
import { Badge, Button, EmptyState, ErrorState, LoadingState } from "../components/ui";

const FILTERS: (RecoveryCaseStatus | "all")[] = [
  "all",
  "discovered",
  "policy_review",
  "approved",
  "executing",
  "recovered",
  "escalated",
  "stopped",
  "failed",
];

export function RecoveryCasesPage({ system }: { system?: SystemInfo | null }) {
  const { state, refetch } = useApiResource(() => api.listRecoveryCases({ page: 1, page_size: 100 }), []);
  const [filter, setFilter] = useState<RecoveryCaseStatus | "all">("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [modal, setModal] = useState(false);

  const rows = useMemo(() => {
    if (state.status !== "success") return [];
    return filter === "all" ? state.data.items : state.data.items.filter((c) => c.status === filter);
  }, [state, filter]);

  return (
    <>
      <div className="toolbar">
        <p className="page-intro" style={{ marginBottom: 0 }}>
          One row per failed-payment recovery opportunity, tracked through the recovery state machine.
          Select a row to investigate, diagnose, and execute.
        </p>
        <div className="toolbar__spacer" />
        <Button onClick={() => setModal(true)}>Ingest payment</Button>
        <Button
          variant="primary"
          onClick={async () => {
            await api.runDiscovery();
            refetch();
          }}
        >
          Run discovery sweep
        </Button>
      </div>

      <div className="filter-chips">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            className={f === filter ? "filter-chip filter-chip--active" : "filter-chip"}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "All" : humanize(f)}
          </button>
        ))}
      </div>

      {state.status === "loading" && <LoadingState label="Loading recovery cases…" />}
      {state.status === "error" && <ErrorState message={state.error} />}

      {state.status === "success" && state.data.items.length === 0 && (
        <EmptyState
          title="No recovery cases yet."
          hint="Ingest a failed payment, or run a discovery sweep over existing failed payments."
          action={<Button variant="primary" onClick={() => setModal(true)}>Ingest payment</Button>}
        />
      )}

      {state.status === "success" && state.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Status</th>
                <th className="num">Revenue at risk</th>
                <th className="num">Recovered</th>
                <th>Diagnosis</th>
                <th>Recommended</th>
                <th className="num">Confidence</th>
                <th className="num">Attempts</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="row--click" onClick={() => setSelected(c.id)}>
                  <td>
                    <Badge tone={statusTone(c.status)}>{humanize(c.status)}</Badge>
                  </td>
                  <td className="num">{formatMoney(c.revenue_at_risk)}</td>
                  <td className="num">
                    {c.recovered_amount > 0 ? <span className="strong">{formatMoney(c.recovered_amount)}</span> : "—"}
                  </td>
                  <td>{humanize(c.diagnosis_category)}</td>
                  <td>{c.recommended_action ? humanize(c.recommended_action) : "—"}</td>
                  <td className="num">{formatPercent(c.recovery_confidence, 0)}</td>
                  <td className="num">{c.current_attempt_number}</td>
                  <td className="subtle nowrap">{formatRelative(c.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="table-foot">
            <span>
              {rows.length} of {state.data.total} case{state.data.total === 1 ? "" : "s"}
              {filter !== "all" ? ` · filtered by ${humanize(filter)}` : ""}
            </span>
            <Button size="sm" variant="ghost" onClick={refetch}>
              Refresh
            </Button>
          </div>
        </div>
      )}

      {selected && (
        <CaseDrawer
          caseId={selected}
          system={system}
          onClose={() => setSelected(null)}
          onMutated={refetch}
        />
      )}
      {modal && <IngestPaymentModal onClose={() => setModal(false)} onDone={refetch} />}
    </>
  );
}
