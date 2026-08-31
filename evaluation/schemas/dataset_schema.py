"""Schema for synthetic recovery-case data + ground truth.

This package is intentionally decoupled from `backend/app` — it does not
import backend enums or the backend policy engine. That keeps the
evaluation dataset an independent oracle rather than a mirror of whatever
the implementation currently does. See docs/evaluation-methodology.md for
why that separation matters and where it is (and isn't) airtight.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class PaymentMethodType(str, Enum):
    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMI = "emi"


class FailureReason(str, Enum):
    NETWORK_ERROR = "network_error"
    GATEWAY_TIMEOUT = "gateway_timeout"
    PROVIDER_ERROR = "provider_error"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXPIRED_INSTRUMENT = "expired_instrument"
    AUTHENTICATION_FAILED = "authentication_failed"
    UNKNOWN = "unknown"


class ScenarioType(str, Enum):
    TEMPORARY_FAILURE = "temporary_failure"
    REPEATED_FAILURE = "repeated_failure"
    CUSTOMER_SIDE_FAILURE = "customer_side_failure"
    HIGH_VALUE = "high_value"
    PREVIOUSLY_CONTACTED = "previously_contacted"
    AMBIGUOUS = "ambiguous"


class RecoveryAction(str, Enum):
    RETRY_PAYMENT = "retry_payment"
    SEND_PAYMENT_LINK = "send_payment_link"
    SEND_REMINDER = "send_reminder"
    ESCALATE = "escalate"
    STOP = "stop"


class SyntheticCaseInput(BaseModel):
    """Fields a recovery-decision strategy is allowed to see."""

    case_id: str
    scenario_type: ScenarioType
    amount: int = Field(gt=0)
    currency: str = "INR"
    payment_method_type: PaymentMethodType
    failure_reason: FailureReason
    attempt_number: int = Field(ge=1)
    days_since_first_attempt: int = Field(ge=0)
    previous_contact_count: int = Field(ge=0)
    customer_payment_history_success_rate: float = Field(ge=0.0, le=1.0)
    is_high_value: bool


class GroundTruth(BaseModel):
    """The held-out label a strategy is scored against. Never fed as input."""

    expected_action: RecoveryAction
    recoverable: bool
    rationale: str


class SyntheticCase(BaseModel):
    input: SyntheticCaseInput
    ground_truth: GroundTruth


class Dataset(BaseModel):
    seed: int
    count: int
    generated_at: str
    cases: list[SyntheticCase]
