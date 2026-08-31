"""Domain enums shared across models, schemas, and services.

Centralizing these prevents status strings from being scattered (and
silently drifting) across the codebase.
"""
from __future__ import annotations

from enum import Enum


class PaymentStatus(str, Enum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"


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


class DiagnosisCategory(str, Enum):
    TEMPORARY_FAILURE = "temporary_failure"
    CUSTOMER_SIDE_FAILURE = "customer_side_failure"
    AUTHENTICATION_FAILURE = "authentication_failure"
    REPEATED_FAILURE = "repeated_failure"
    UNKNOWN_FAILURE = "unknown_failure"
    OTHER = "other"


class RecoveryCaseStatus(str, Enum):
    DISCOVERED = "discovered"
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    DIAGNOSING = "diagnosing"
    RECOMMENDED = "recommended"
    POLICY_REVIEW = "policy_review"
    APPROVED = "approved"
    EXECUTING = "executing"
    RECOVERED = "recovered"
    STOPPED = "stopped"
    ESCALATED = "escalated"
    FAILED = "failed"


TERMINAL_RECOVERY_CASE_STATUSES: frozenset[RecoveryCaseStatus] = frozenset(
    {
        RecoveryCaseStatus.INELIGIBLE,
        RecoveryCaseStatus.RECOVERED,
        RecoveryCaseStatus.STOPPED,
        RecoveryCaseStatus.ESCALATED,
    }
)


class RecoveryAction(str, Enum):
    RETRY_PAYMENT = "retry_payment"
    SEND_PAYMENT_LINK = "send_payment_link"
    SEND_REMINDER = "send_reminder"
    ESCALATE = "escalate"
    STOP = "stop"


class RecoveryAttemptStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActorType(str, Enum):
    SYSTEM = "system"
    AI = "ai"
    POLICY_ENGINE = "policy_engine"
    HUMAN = "human"


class PolicyDecisionType(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


class PolicyReasonCode(str, Enum):
    MAX_RETRIES_REACHED = "max_retries_reached"
    RECOVERY_WINDOW_EXPIRED = "recovery_window_expired"
    MAX_CONTACTS_REACHED = "max_contacts_reached"
    CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
    ACTION_NOT_ELIGIBLE_FOR_STATUS = "action_not_eligible_for_status"
    AMOUNT_OUT_OF_BOUNDS = "amount_out_of_bounds"
    TERMINAL_STATE_PROTECTED = "terminal_state_protected"


class DecisionSource(str, Enum):
    """Whether a recovery recommendation came from the AI provider or the
    deterministic safe fallback that runs when the AI is unavailable."""

    AI = "ai"
    FALLBACK = "fallback"


class RecoveryPaymentRequestStatus(str, Enum):
    """Mirrors Razorpay's own Payment Link status vocabulary exactly, so no
    translation table is needed between provider state and ours."""

    CREATED = "created"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ProviderFailureCategory(str, Enum):
    """Internal classification a concrete PaymentProvider maps its own
    errors into — never a raw provider exception or response leaks past
    this boundary. See docs/razorpay-integration.md."""

    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_AUTH_ERROR = "provider_auth_error"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_VALIDATION_ERROR = "provider_validation_error"
    DUPLICATE_REQUEST = "duplicate_request"
    WEBHOOK_VERIFICATION_FAILURE = "webhook_verification_failure"
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_EXPIRED = "payment_expired"
    AMBIGUOUS_RESULT = "ambiguous_result"
    UNKNOWN_PROVIDER_ERROR = "unknown_provider_error"


class NotificationMedium(str, Enum):
    SMS = "sms"
    EMAIL = "email"
