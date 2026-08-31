import { api } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import { formatDateTime, formatNumber, formatPercent, humanize, shortId } from "../lib/format";
import { Card, EmptyState, ErrorState, LoadingState } from "../components/ui";

type Row = [string, string | number | null | undefined];

function Metric({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <Card title={title} flush>
      <dl style={{ margin: 0 }}>
        {rows.map(([label, value], i) => (
          <div
            key={label}
            className="spread"
            style={{ padding: "10px 16px", borderTop: i === 0 ? "none" : "1px solid var(--border)" }}
          >
            <dt className="subtle" style={{ fontSize: 12.5 }}>
              {label}
            </dt>
            <dd className="strong" style={{ margin: 0, fontVariantNumeric: "tabular-nums" }}>
              {value ?? "—"}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

export function EvaluationPage() {
  const { state } = useApiResource(() => api.getEvaluationSummary(), []);

  if (state.status === "loading") return <LoadingState label="Loading evaluation summary…" />;
  if (state.status === "error") return <ErrorState message={state.error} />;

  const data = state.data;

  if (data.status === "no_evaluation_run") {
    return (
      <>
        <p className="page-intro">
          Decision-quality and safety metrics from the synthetic-dataset harness. Entirely separate from
          the live recovery numbers on Overview — different methodology, hundreds of simulated cases,
          never real money.
        </p>
        <EmptyState
          title="No evaluation report found."
          hint="Run python -m evaluation.run_evaluation --count 500 --seed 42 from the repo root, then reload."
        />
      </>
    );
  }

  return (
    <>
      <p className="page-intro">
        Synthetic-dataset run — decision quality and safety only. Ground truth and the baseline strategy
        share authorship by construction; this is a regression harness, not proof of real-world accuracy.
      </p>
      <p className="inline-note" style={{ marginBottom: 18 }}>
        Strategy <span className="strong">{humanize(data.strategy)}</span> · {formatNumber(data.dataset_count)} cases
        (seed {data.dataset_seed}) · run {shortId(data.run_id ?? "—")} · {formatDateTime(data.generated_at)}
      </p>

      <div className="grid grid--2">
        <Metric
          title="Financial (simulated)"
          rows={[
            ["Total revenue at risk", formatNumber(data.financial?.total_revenue_at_risk)],
            ["Eligible revenue", formatNumber(data.financial?.eligible_revenue)],
            ["Recovered revenue", formatNumber(data.financial?.recovered_revenue)],
            ["Recovery rate", formatPercent(data.financial?.recovery_rate)],
          ]}
        />
        <Metric
          title="Decision quality"
          rows={[
            ["Intervention accuracy", formatPercent(data.decision?.intervention_accuracy)],
            ["Appropriate escalation rate", formatPercent(data.decision?.appropriate_escalation_rate)],
            ["Inappropriate intervention rate", formatPercent(data.decision?.inappropriate_intervention_rate)],
          ]}
        />
        <Metric
          title="Safety"
          rows={[
            ["Policy violations", data.safety?.policy_violations],
            ["Retry-limit violations", data.safety?.retry_limit_violations],
            ["Stopping-rule violations", data.safety?.stopping_rule_violations],
            ["Unauthorized actions", data.safety?.unauthorized_actions],
          ]}
        />
        <Metric
          title="Operational"
          rows={[
            ["Cases processed", formatNumber(data.operational?.cases_processed)],
            ["Avg. processing time (ms)", data.operational?.average_processing_time_ms?.toFixed(1)],
            ["Throughput (cases/sec)", data.operational?.throughput_per_second?.toFixed(1)],
          ]}
        />
      </div>
    </>
  );
}
