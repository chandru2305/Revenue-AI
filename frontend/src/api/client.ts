import type {
  AuditEventRead,
  DiagnosisResponse,
  DiscoveryReport,
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

  const response = await fetch(url.toString(), { headers: { Accept: "application/json" } });
  if (!response.ok) throw await parseError(response, path);
  return (await response.json()) as T;
}

async function requestPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
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

  diagnoseRecoveryCase: (caseId: string) =>
    requestPost<DiagnosisResponse>(`${API_V1_PREFIX}/recovery-cases/${caseId}/diagnose`),

  executeRecoveryCase: (caseId: string) =>
    requestPost<ExecutionResponse>(`${API_V1_PREFIX}/recovery-cases/${caseId}/execute`),

  getRecoveryCaseTimeline: (caseId: string) =>
    request<TimelineResponse>(`${API_V1_PREFIX}/recovery-cases/${caseId}/timeline`),

  listAuditEvents: (params?: AuditQuery) =>
    request<PaginatedResponse<AuditEventRead>>(`${API_V1_PREFIX}/audit-events`, params),

  getEvaluationSummary: () => request<EvaluationSummaryRead>(`${API_V1_PREFIX}/evaluation/summary`),

  getRecoverySummary: () => request<RecoverySummaryRead>(`${API_V1_PREFIX}/evaluation/recovery-summary`),
};
