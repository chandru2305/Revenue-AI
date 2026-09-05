import type {
  AuditEventRead,
  DemoBatchResponse,
  DiagnosisResponse,
  DiscoveryReport,
  OrchestratorStatus,
  RecoveryCycleReport,
  SystemInfo,
  EvaluationSummaryRead,
  ExecutionResponse,
  HealthCheckResponse,
  PaginatedResponse,
  PaymentIngestRequest,
  PaymentIngestResponse,
  PaymentRead,
  RecoveryCaseCreatedResponse,
  RecoveryCaseDetail,
  RecoveryCaseRead,
  RecoverySummaryRead,
  TimelineResponse,
} from "./types";

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const API_V1_PREFIX = "/api/v1";

// Sent as X-API-Key when the backend has auth enabled. This is baked into
// the JS bundle at build time and is therefore readable by anyone who
// opens devtools — it is a deployment gate ("can this browser reach this
// deployment"), not a user credential, and must not be treated as a
// secret. See docs/security.md "API authentication".
const API_KEY: string = import.meta.env.VITE_API_KEY ?? "";

function authHeaders(): Record<string, string> {
  return API_KEY ? { "X-API-Key": API_KEY } : {};
}

export class ApiError extends Error {
  status: number;
  errorCode: string | undefined;

  constructor(message: string, status: number, errorCode?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errorCode = errorCode;
  }
}

async function parseError(response: Response, path: string): Promise<ApiError> {
  const body = await response.json().catch(() => ({}) as Record<string, unknown>);
  return new ApiError(
    (body.message as string) ?? `Request to ${path} failed with status ${response.status}`,
    response.status,
    body.error_code as string | undefined,
  );
}

async function request<T>(path: string, params?: object): Promise<T> {
  const url = new URL(`${API_BASE_URL}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params) as [string, unknown][]) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const response = await fetch(url.toString(), {
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (!response.ok) throw await parseError(response, path);
  return (await response.json()) as T;
}

async function requestPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      ...authHeaders(),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw await parseError(response, path);
  return (await response.json()) as T;
}

export interface ListParams {
  page?: number;
  page_size?: number;
}

export interface AuditQuery extends ListParams {
  entity_type?: string;
  entity_id?: string;
  event_type?: string;
  correlation_id?: string;
}

export const api = {
  getHealth: () => request<HealthCheckResponse>("/health"),

  listPayments: (params?: ListParams & { status?: string }) =>
    request<PaginatedResponse<PaymentRead>>(`${API_V1_PREFIX}/payments`, params),

  ingestPayment: (body: PaymentIngestRequest) =>
    requestPost<PaymentIngestResponse>(`${API_V1_PREFIX}/payments`, body),

  listRecoveryCases: (params?: ListParams & { status?: string }) =>
    request<PaginatedResponse<RecoveryCaseRead>>(`${API_V1_PREFIX}/recovery-cases`, params),

  getRecoveryCase: (caseId: string) =>
    request<RecoveryCaseDetail>(`${API_V1_PREFIX}/recovery-cases/${caseId}`),

  createRecoveryCase: (paymentId: string) =>
    requestPost<RecoveryCaseCreatedResponse>(`${API_V1_PREFIX}/recovery-cases`, {
      payment_id: paymentId,
    }),

  runDiscovery: () => requestPost<DiscoveryReport>(`${API_V1_PREFIX}/recovery-cases/discover`),

  // One pass of the autonomous loop: discover -> diagnose -> (optionally) execute.
  runRecoveryCycle: (autoExecute?: boolean) =>
    requestPost<RecoveryCycleReport>(
      `${API_V1_PREFIX}/orchestrator/cycle${autoExecute === undefined ? "" : `?auto_execute=${autoExecute}`}`,
    ),

  // Read-only snapshot of the autonomous recovery agent, from the audit trail.
  getOrchestratorStatus: () =>
    request<OrchestratorStatus>(`${API_V1_PREFIX}/orchestrator/status`),

  // How this deployment is wired (provider modes, policy limits).
  getSystemInfo: () => request<SystemInfo>(`${API_V1_PREFIX}/system/info`),

  seedDemoBatch: () => requestPost<DemoBatchResponse>(`${API_V1_PREFIX}/demo/seed-batch`),

  diagnoseRecoveryCase: (caseId: string) =>
    requestPost<DiagnosisResponse>(`${API_V1_PREFIX}/recovery-cases/${caseId}/diagnose`),

  executeRecoveryCase: (caseId: string) =>
    requestPost<ExecutionResponse>(`${API_V1_PREFIX}/recovery-cases/${caseId}/execute`),

  getRecoveryCaseTimeline: (caseId: string) =>
    request<TimelineResponse>(`${API_V1_PREFIX}/recovery-cases/${caseId}/timeline`),

  listAuditEvents: (params?: AuditQuery) =>
    request<PaginatedResponse<AuditEventRead>>(`${API_V1_PREFIX}/audit-events`, params),

  // Downloads the same append-only rows, unpaginated, oldest first. A
  // plain <a href> can't carry the X-API-Key header, so this fetches the
  // file as a Blob for the caller to save (see lib/download.ts).
  exportAuditEvents: async (
    format: "csv" | "json",
    params?: Omit<AuditQuery, "page" | "page_size">,
  ): Promise<Blob> => {
    const url = new URL(`${API_BASE_URL}${API_V1_PREFIX}/audit-events/export`);
    url.searchParams.set("format", format);
    if (params) {
      for (const [key, value] of Object.entries(params) as [string, unknown][]) {
        if (value !== undefined && value !== null && value !== "") {
          url.searchParams.set(key, String(value));
        }
      }
    }
    const response = await fetch(url.toString(), { headers: { ...authHeaders() } });
    if (!response.ok) throw await parseError(response, "/audit-events/export");
    return response.blob();
  },

  getEvaluationSummary: () => request<EvaluationSummaryRead>(`${API_V1_PREFIX}/evaluation/summary`),

  getRecoverySummary: () => request<RecoverySummaryRead>(`${API_V1_PREFIX}/evaluation/recovery-summary`),
};
