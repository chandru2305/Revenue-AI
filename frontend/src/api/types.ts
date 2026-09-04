// Mirrors backend/app/schemas/*.py. Keep in sync by hand — the frontend has
// no code-generation step in Phase 1 (see docs/architecture.md).

export type PaymentStatus = "created" | "authorized" | "captured" | "failed" | "refunded";

export type PaymentMethodType = "card" | "upi" | "netbanking" | "wallet" | "emi";

export type FailureReason =
  | "network_error"
  | "gateway_timeout"
  | "provider_error"
  | "insufficient_funds"
  | "expired_instrument"
  | "authentication_failed"
  | "unknown";

export type DiagnosisCategory =
  | "temporary_failure"
  | "customer_side_failure"
  | "authentication_failure"
  | "repeated_failure"
  | "unknown_failure"
  | "other";

export type DecisionSource = "ai" | "fallback";

export type PolicyDecisionType = "allow" | "block";

export type PolicyReasonCode =
  | "max_retries_reached"
  | "recovery_window_expired"
  | "max_contacts_reached"
  | "confidence_below_threshold"
  | "action_not_eligible_for_status"
  | "amount_out_of_bounds"
  | "terminal_state_protected";

export type RecoveryCaseStatus =
  | "discovered"
  | "eligible"
  | "ineligible"
  | "diagnosing"
  | "recommended"
  | "policy_review"
  | "approved"
  | "executing"
  | "recovered"
  | "stopped"
  | "escalated"
  | "failed";

export type RecoveryAction =
  | "retry_payment"
  | "send_payment_link"
  | "send_reminder"
  | "escalate"
  | "stop";

export type RecoveryAttemptStatus = "pending" | "in_progress" | "succeeded" | "failed" | "cancelled";

export type RecoveryPaymentRequestStatus = "created" | "partially_paid" | "paid" | "expired" | "cancelled";

export type ActorType = "system" | "ai" | "policy_engine" | "human";

export interface PaginatedResponse<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface PaymentRead {
  id: string;
  customer_id: string;
  amount: number;
  currency: string;
  status: PaymentStatus;
  payment_method_type: PaymentMethodType;
  failure_reason: FailureReason | null;
  attempt_number: number;
  provider_payment_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface RecoveryAttemptRead {
  id: string;
  recovery_case_id: string;
  action: RecoveryAction;
  status: RecoveryAttemptStatus;
  provider: string | null;
  amount: number | null;
  currency: string | null;
  reason: string | null;
  provider_reference: string | null;
  idempotency_key: string | null;
  correlation_id: string | null;
  failure_code: string | null;
  failure_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface RecoveryPaymentRequestRead {
  id: string;
  recovery_attempt_id: string;
  provider: string;
  provider_reference: string;
  short_url: string | null;
  amount: number;
  amount_paid: number;
  currency: string;
  status: RecoveryPaymentRequestStatus;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RecoveryCaseRead {
  id: string;
  payment_id: string;
  status: RecoveryCaseStatus;
  revenue_at_risk: number;
  recovered_amount: number;
  eligible: boolean;
  diagnosis_category: DiagnosisCategory | null;
  recovery_confidence: number | null;
  recommended_action: RecoveryAction | null;
  current_attempt_number: number;
  customer_contact_count: number;
  policy_version: string | null;
  created_at: string;
  updated_at: string;
}

export interface RecoveryCaseDetail extends RecoveryCaseRead {
  diagnosis_notes: string | null;
  attempts: RecoveryAttemptRead[];
  payment_requests: RecoveryPaymentRequestRead[];
  payment: PaymentRead;
}

export interface ExecutionResponse {
  recovery_case_id: string;
  case_status: RecoveryCaseStatus;
  correlation_id: string;

  executed: boolean;
  reason: string | null;

  policy_decision: PolicyDecisionType | null;
  policy_reason_codes: PolicyReasonCode[];

  provider_reference: string | null;
  short_url: string | null;
  payment_link_status: RecoveryPaymentRequestStatus | null;
  amount: number | null;
  currency: string | null;
  expires_at: string | null;
}

export interface TimelineEvent {
  id: string;
  event_type: string;
  actor_type: ActorType;
  payload: Record<string, unknown>;
  correlation_id: string | null;
  created_at: string;
}

export interface TimelineResponse {
  recovery_case_id: string;
  events: TimelineEvent[];
}

export interface DiagnosisResponse {
  recovery_case_id: string;
  case_status: RecoveryCaseStatus;
  correlation_id: string;

  decision_source: DecisionSource | null;
  diagnosis_category: DiagnosisCategory | null;
  recovery_confidence: number | null;
  recommended_action: RecoveryAction | null;
  decision_explanation: string | null;

  policy_decision: PolicyDecisionType | null;
  policy_reason_codes: PolicyReasonCode[];
  policy_version: string | null;

  ai_model: string | null;
  ai_prompt_version: string | null;
  ai_latency_ms: number | null;
}

export interface AuditEventRead {
  id: string;
  entity_type: string;
  entity_id: string;
  event_type: string;
  actor_type: ActorType;
  payload: Record<string, unknown>;
  correlation_id: string | null;
  created_at: string;
}

export interface FinancialMetrics {
  total_revenue_at_risk: number;
  eligible_revenue: number;
  recovered_revenue: number;
  recovery_rate: number;
}

export interface DecisionMetrics {
  intervention_accuracy: number;
  appropriate_escalation_rate: number;
  inappropriate_intervention_rate: number;
}

export interface SafetyMetrics {
  policy_violations: number;
  retry_limit_violations: number;
  stopping_rule_violations: number;
  unauthorized_actions: number;
}

export interface OperationalMetrics {
  cases_processed: number;
  average_processing_time_ms: number;
  throughput_per_second: number;
}

export interface EvaluationSummaryRead {
  status: "ok" | "no_evaluation_run";
  run_id: string | null;
  generated_at: string | null;
  strategy: string | null;
  dataset_count: number | null;
  dataset_seed: number | null;
  financial: FinancialMetrics | null;
  decision: DecisionMetrics | null;
  safety: SafetyMetrics | null;
  operational: OperationalMetrics | null;
}

export interface RecoverySummaryRead {
  source: string;
  generated_at: string;

  cases_total: number;
  cases_eligible: number;
  cases_by_status: Record<string, number>;

  total_revenue_at_risk: number;
  eligible_revenue: number;
  confirmed_recovered_revenue: number;
  outstanding_revenue: number;
  recovery_rate: number;

  recovery_attempts: number;
  successful_payment_links_created: number;
  average_recovery_amount: number;

  escalation_rate: number;
  stop_rate: number;
  provider_failure_rate: number;
}

export interface HealthCheckResponse {
  status: "ok" | "degraded";
  checks: Record<string, string>;
}

// ---- Ingestion (workflow entry point) ----

export interface PaymentIngestRequest {
  customer_reference?: string | null;
  amount: number;
  currency?: string;
  status?: PaymentStatus;
  payment_method_type?: PaymentMethodType;
  failure_reason?: FailureReason | null;
  attempt_number?: number;
  provider_payment_id?: string | null;
  auto_create_case?: boolean;
}

export interface PaymentIngestResponse {
  payment_id: string;
  customer_id: string;
  recovery_case_id: string | null;
  recovery_case_status: RecoveryCaseStatus | null;
  correlation_id: string;
  /** True when this matched an existing payment by provider_payment_id and no new record was created. */
  deduplicated: boolean;
}

export interface RecoveryCaseCreatedResponse {
  recovery_case_id: string;
  payment_id: string;
  status: RecoveryCaseStatus;
  revenue_at_risk: number;
  created: boolean;
  correlation_id: string;
}

export interface CycleCaseOutcome {
  recovery_case_id: string;
  final_status: RecoveryCaseStatus;
  diagnosed: boolean;
  executed: boolean;
  withheld_reason: string | null;
  error: string | null;
}

export interface RecoveryCycleReport {
  cycle_id: string;
  correlation_id: string;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  auto_execute: boolean;
  cases_discovered: number;
  cases_diagnosed: number;
  cases_executed: number;
  cases_failed: number;
  approved: number;
  stopped: number;
  escalated: number;
  recovered: number;
  outcomes: CycleCaseOutcome[];
}

export interface DemoBatchResponse {
  correlation_id: string;
  cases_processed: number;
  final_status_counts: Record<string, number>;
  /** Model that produced the diagnoses (live provider's model, or a fallback label). */
  ai_model: string;
  summary: RecoverySummaryRead;
  provenance: string;
}

export interface DiscoveryReport {
  scanned: number;
  created: number;
  skipped_existing: number;
  case_ids: string[];
  generated_at: string;
  correlation_id: string;
}

// ---- Autonomous agent status (GET /orchestrator/status) ----

export interface LastCycleSummary {
  completed_at: string;
  correlation_id: string | null;
  auto_execute: boolean;
  cases_discovered: number;
  cases_diagnosed: number;
  cases_executed: number;
  cases_failed: number;
  approved: number;
  stopped: number;
  escalated: number;
  duration_seconds: number;
}

export interface OrchestratorStatus {
  enabled: boolean;
  running: boolean;
  errored: boolean;
  auto_execute: boolean;
  interval_seconds: number;
  cycles_completed: number;
  last_cycle: LastCycleSummary | null;
  recent_ai_diagnoses: number;
  average_ai_latency_ms: number | null;
  /** "running" | "idle" | "error" */
  agent_state: string;
}

// ---- Deployment wiring (GET /system/info) ----

export interface PolicyLimits {
  max_retry_count: number;
  max_recovery_window_days: number;
  max_customer_contacts: number;
  min_confidence_threshold: number;
  high_value_amount_threshold: number;
  high_value_min_confidence_threshold: number;
  max_recovery_amount: number;
}

export interface SystemInfo {
  app_env: string;
  demo_mode: boolean;
  payment_provider: string;
  payment_provider_mode: string;
  ai_provider: string;
  ai_model: string;
  orchestrator_enabled: boolean;
  orchestrator_auto_execute: boolean;
  auth_enforced: boolean;
  policy: PolicyLimits;
}
