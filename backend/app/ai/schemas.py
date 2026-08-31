"""Structured AI output.

This is the ONLY shape a provider is allowed to return. There is no path
from raw provider text to a domain decision that skips this schema's
validation — malformed or out-of-range output is rejected here, not
downstream. Confidence validation alone does not authorize anything; see
`app.domain.policy` for the deterministic gate that actually decides.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import DiagnosisCategory, RecoveryAction


class RecoveryRecommendation(BaseModel):
    """A single AI (or fallback) recommendation for one recovery case.

    No hidden reasoning is ever stored here — `decision_explanation` is a
    short, human-reviewable summary, not a chain-of-thought transcript.
    """

    diagnosis_category: DiagnosisCategory
    recovery_confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: RecoveryAction
    decision_explanation: str = Field(min_length=1, max_length=600)
