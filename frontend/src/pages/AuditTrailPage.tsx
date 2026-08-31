import { Fragment, useMemo, useState } from "react";
import { api } from "../api/client";
import { useApiResource } from "../api/useApiResource";
import type { ActorType, AuditEventRead } from "../api/types";
import { formatDateTime, formatRelative, humanize } from "../lib/format";
import { ACTOR_TYPES, actorTone, ENTITY_TYPES } from "../lib/labels";
import { CaseDrawer } from "../components/CaseDrawer";
import { Badge, Button, Copyable, EmptyState, ErrorState, JsonBlock, LoadingState } from "../components/ui";

const PAGE_SIZE = 50;

export function AuditTrailPage() {
  const [entityType, setEntityType] = useState("");
  const [eventType, setEventType] = useState("");
  const [correlationId, setCorrelationId] = useState("");
  const [actorType, setActorType] = useState<ActorType | "">("");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [openCase, setOpenCase] = useState<string | null>(null);

  const { state, refetch } = useApiResource(
    () =>
      api.listAuditEvents({
        page,
        page_size: PAGE_SIZE,
        entity_type: entityType || undefined,
        event_type: eventType.trim() || undefined,
        correlation_id: correlationId.trim() || undefined,
      }),
    [page, entityType, eventType, correlationId],
  );

  const rows = useMemo(() => {
    if (state.status !== "success") return [];
    return actorType ? state.data.items.filter((e) => e.actor_type === actorType) : state.data.items;
  }, [state, actorType]);

  function resetPageAnd(fn: () => void) {
    setPage(1);
    fn();
  }

  const total = state.status === "success" ? state.data.total : 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <>
      <p className="page-intro">
        Append-only record of every decision and action, newest first. Nothing here is ever edited or
        deleted. Filter by entity, event type, or a correlation ID to trace one request end to end.
      </p>

      <div className="toolbar">
        <label className="field">
          <span className="field__label">Entity type</span>
          <select
            className="select"
            value={entityType}
            onChange={(e) => resetPageAnd(() => setEntityType(e.target.value))}
          >
            <option value="">All</option>
            {ENTITY_TYPES.map((t) => (
              <option key={t} value={t}>
                {humanize(t)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">Actor</span>
          <select className="select" value={actorType} onChange={(e) => setActorType(e.target.value as ActorType | "")}>
            <option value="">All</option>
            {ACTOR_TYPES.map((t) => (
              <option key={t} value={t}>
                {humanize(t)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">Event type</span>
          <input
            className="input"
            placeholder="e.g. payment_confirmed"
            value={eventType}
            onChange={(e) => resetPageAnd(() => setEventType(e.target.value))}
          />
        </label>
        <label className="field">
          <span className="field__label">Correlation ID</span>
          <input
            className="input"
            placeholder="exact match"
            value={correlationId}
            onChange={(e) => resetPageAnd(() => setCorrelationId(e.target.value))}
          />
        </label>
        <div className="toolbar__spacer" />
        <Button size="sm" variant="ghost" onClick={refetch}>
          Refresh
        </Button>
      </div>

      {state.status === "loading" && <LoadingState label="Loading audit events…" />}
      {state.status === "error" && <ErrorState message={state.error} />}

      {state.status === "success" && rows.length === 0 && (
        <EmptyState title="No audit events match these filters." hint="Widen the filters or clear the correlation ID." />
      )}

      {state.status === "success" && rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                <th>Event</th>
                <th>Actor</th>
                <th>Entity</th>
                <th>Correlation</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((e: AuditEventRead) => {
                const isOpen = expanded[e.id];
                const isCase = e.entity_type === "recovery_case";
                return (
                  <Fragment key={e.id}>
                    <tr>
                      <td className="subtle nowrap" title={formatDateTime(e.created_at)}>
                        {formatRelative(e.created_at)}
                      </td>
                      <td className="strong">{humanize(e.event_type)}</td>
                      <td>
                        <Badge tone={actorTone(e.actor_type)}>{e.actor_type}</Badge>
                      </td>
                      <td>
                        <span className="subtle">{humanize(e.entity_type)}</span>{" "}
                        {isCase ? (
                          <button type="button" className="copy-btn" onClick={() => setOpenCase(e.entity_id)}>
                            {e.entity_id.slice(0, 8)} ↗
                          </button>
                        ) : (
                          <span className="cell-mono">{e.entity_id.slice(0, 8)}</span>
                        )}
                      </td>
                      <td>
                        {e.correlation_id ? (
                          <Copyable text={e.correlation_id} display={e.correlation_id.slice(0, 10)} />
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="num">
                        {Object.keys(e.payload).length > 0 && (
                          <button
                            type="button"
                            className="copy-btn"
                            onClick={() => setExpanded((o) => ({ ...o, [e.id]: !o[e.id] }))}
                          >
                            {isOpen ? "hide" : "payload"}
                          </button>
                        )}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={6} style={{ background: "var(--surface-2)" }}>
                          <JsonBlock value={e.payload} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          <div className="table-foot">
            <span>
              {actorType ? `${rows.length} shown · ` : ""}
              {total} event{total === 1 ? "" : "s"} matched
            </span>
            <span className="row-flex">
              <Button size="sm" variant="ghost" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
                Prev
              </Button>
              <span className="subtle">
                Page {page} / {pageCount}
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                disabled={page >= pageCount}
              >
                Next
              </Button>
            </span>
          </div>
        </div>
      )}

      {openCase && <CaseDrawer caseId={openCase} onClose={() => setOpenCase(null)} onMutated={refetch} />}
    </>
  );
}
