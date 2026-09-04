"""Bits shared between the evaluation strategy and its prompt/schema.

Keeps each `*_strategy.py` to just its provider-specific call + exception
handling. The prompt text and the output schema are deliberately the same
across providers so a cross-provider comparison is apples to apples.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from evaluation.baseline.rule_based import BaselineDecision
from evaluation.schemas.dataset_schema import RecoveryAction

_ALLOWED_ACTIONS = ", ".join(action.value for action in RecoveryAction)

SYSTEM_INSTRUCTION = f"""You are evaluating a synthetic payment-recovery case for RecoverAI.

Given structured context about one failed payment, recommend exactly one
recovery action. You have no authority to execute anything — this is a
recommendation only, later checked by a separate deterministic policy
engine which is authoritative.

Rules:
- Base your answer only on the structured context provided.
- recommended_action must be exactly one of: {_ALLOWED_ACTIONS}
- confidence is a number between 0.0 and 1.0.
- If evidence is weak or contradictory, prefer ESCALATE or STOP over
  guessing.
- rationale must be a short (1-2 sentence) justification, not a
  step-by-step reasoning trace.

Respond with a single JSON object matching the schema — no prose outside it."""


class CaseRecommendation(BaseModel):
    """The one shape any provider must return, before it is mapped onto a
    BaselineDecision for scoring."""

    recommended_action: RecoveryAction
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=400)


class AINotConfiguredError(Exception):
    """No API key for the selected provider. Raised by `build_client`; the
    runner catches it and marks the AI side `skipped_no_credentials`
    rather than fabricating a comparison."""


@dataclass(frozen=True)
class AICallOutcome:
    decision: BaselineDecision
    succeeded: bool
    latency_ms: float
    error: str | None = None


def fallback(reason: str) -> BaselineDecision:
    """The safe-fallback decision scored for any failed call — identical to
    the backend's behaviour (`app.ai.service._fallback_recommendation`)."""
    return BaselineDecision(
        action=RecoveryAction.ESCALATE,
        confidence=0.0,
        rationale=f"AI evaluation call failed; escalating per safe fallback. ({reason[:200]})",
    )
