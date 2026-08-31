from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.domain.enums import (
    DecisionSource,
    DiagnosisCategory,
    PolicyDecisionType,
    PolicyReasonCode,
    RecoveryAction,
    RecoveryCaseStatus,
)


class DiagnosisResponse(BaseModel):
    """Response for POST /api/v1/recovery-cases/{id}/diagnose.

    `policy_decision` ALLOW/BLOCK is a description of what the deterministic
    policy engine decided, never authorization performed by the AI — see
    docs/ai-safety.md. `case_status` is the recovery case's resulting
    status after this call; the AI recommendation and the policy decision
    are visibly separate fields on purpose (recommendation != authorization).
    """

    recovery_case_id: uuid.UUID
    case_status: RecoveryCaseStatus
    correlation_id: str

    decision_source: DecisionSource | None = None
    diagnosis_category: DiagnosisCategory | None = None
    recovery_confidence: float | None = None
    recommended_action: RecoveryAction | None = None
    decision_explanation: str | None = None

    policy_decision: PolicyDecisionType | None = None
    policy_reason_codes: list[PolicyReasonCode] = []
    policy_version: str | None = None

    ai_model: str | None = None
    ai_prompt_version: str | None = None
    ai_latency_ms: float | None = None
