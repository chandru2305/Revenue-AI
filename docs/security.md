# Security Model

## Threat model

The API is gated by a shared API key (see "API authentication" below);
there is still no *per-user* authentication surface, since the frontend is
an internal-facing operator dashboard with no notion of user identity.
There are two outbound
external calls: the Groq API (Phase 2) and, new in Phase 3, the
Razorpay Test Mode API — plus one new *inbound* surface, the Razorpay
webhook endpoint, which accepts unauthenticated-until-verified requests
from the public internet. The main risks at this stage are: leaking
secrets (`GROQ_API_KEY`, and now `RAZORPAY_KEY_SECRET`/
`RAZORPAY_WEBHOOK_SECRET`), prompt injection through payment/customer
data reaching the AI, an AI layer that could bypass deterministic
controls, a forged or replayed webhook forcing a case to `RECOVERED`
without a real payment, and two concurrent writers corrupting one
case's state. This document covers what's in place now and what's
explicitly deferred; the AI-specific threat model has its own document,
**docs/ai-safety.md**, and the Razorpay-specific threat model (webhook
verification, amount safety, ambiguous-result handling, credential
protection) has its own document, **docs/razorpay-integration.md** —
both required reading before touching `backend/app/ai/` or
`backend/app/payments/` respectively.

## AI reasons, deterministic systems enforce

This is the core design principle of the whole project (see the root
[README](../README.md)). Concretely, in the code that exists today:

- `backend/app/domain/policy.py` (`evaluate_policy`) is a pure function with
  no I/O, no LLM calls, and no dependency on anything non-deterministic. It
  is the *only* place that can produce an `ALLOW`/`BLOCK` decision.
- `backend/app/domain/state_machine.py` is the only place that defines legal
  recovery-case status transitions. Services must call `validate_transition`
  before persisting a status change.
- **An AI/LLM integration exists** (`backend/app/ai/`, Groq via the
  `groq` SDK). It is permitted to populate only *recommendation*
  fields (`recommended_action`, `recovery_confidence`,
  `diagnosis_category`/`diagnosis_notes`) via a schema-validated
  `RecoveryRecommendation` — never a policy decision, a state transition, or
  an execution. Every recommendation, AI-sourced or fallback-sourced, still
  passes through the same `evaluate_policy` call before it can change a
  case's state. Full detail: docs/ai-safety.md.
- **As of Phase 3, execution against a real payment provider exists**
  (`backend/app/payments/`, Razorpay Test Mode). The AI never calls
  Razorpay directly, never receives Razorpay credentials, and never
  authorizes its own recommendation — `execution_service.py` re-checks
  the same deterministic policy engine with fresh data immediately before
  calling the provider, and the amount sent to Razorpay is always read
  from the canonical `Payment` row, never from AI output or a request
  body. `RECOVERED` is reachable only via a signature-verified webhook,
  never from the execute call itself. Full detail:
  docs/razorpay-integration.md.

## Secrets and configuration

- All configuration is typed and centralized in `backend/app/core/config.py`
  (`pydantic-settings`). No module reads `os.environ` directly.
- `.env.example` (repo root) and `frontend/.env.example` document every
  variable with no real values. `.gitignore` excludes `.env` and `.env.*`
  (except the `.example` files) everywhere in the repo.
- **As of Phase 3, Razorpay credentials are live-read** by
  `RazorpayPaymentProvider` (`backend/app/payments/providers/razorpay.py`)
  — Test Mode only. `RazorpayPaymentProvider.__init__` refuses to
  construct unless `RAZORPAY_MODE == "test"`
  (`RazorpayModeError`, not bypassable via any constructor argument);
  Live Mode credentials must never be placed in this repo or its `.env`
  files. Like `GROQ_API_KEY`, reading these is fully optional at
  runtime: with no key_id/secret configured,
  `app/payments/dependencies.py::get_payment_provider()` returns a
  stand-in whose every method raises an auth error, which
  `execution_service` turns into a safe `ESCALATED` outcome — an
  unconfigured provider degrades gracefully, it doesn't crash requests.
  `RAZORPAY_WEBHOOK_SECRET` is read only by the webhook route to verify
  `X-Razorpay-Signature`; with no secret configured, verification always
  fails closed (`verify_signature` returns `False` for an empty secret),
  so an unconfigured webhook endpoint rejects everything rather than
  accepting unverified events. See docs/razorpay-integration.md for full
  detail, including exactly what was checked against Razorpay's current
  API documentation before any of this was implemented.
- `GROQ_API_KEY` is used starting Phase 2, but reading it is fully
  optional at runtime: `GroqProvider` is only constructed if the key is
  non-empty (`app/ai/dependencies.py`). An empty key does not crash the
  app or fail requests — it resolves to a stand-in provider that always
  raises an auth error, which `AIRecommendationService` already turns into
  a safe fallback (ESCALATE), exactly like any other AI outage. The key
  itself is never logged (see Logging below) and never included in a
  prompt sent to the model.

## Logging

- `backend/app/core/logging.py` emits structured JSON logs with a
  correlation ID (propagated via an `x-correlation-id` request header/
  response header, set in `backend/app/main.py`'s middleware).
- `_is_redacted_key`/`_REDACTED_KEY_FRAGMENTS` in `logging.py` drop any
  field whose name *contains* `password`, `secret`, `token`, `api_key`,
  or `authorization` (case-insensitive substring match, not exact) from
  structured log fields before they're serialized, even if a caller
  accidentally passes one through `extra_fields`. This is deliberately a
  substring match rather than an enumerated list of exact field names —
  found and fixed during the Phase 4 security review, when an
  exact-match version of this list would not have caught a field
  literally named `webhook_secret` (only `secret`/`key_secret` were
  listed). See `backend/tests/test_logging_redaction.py`.
- No request/response bodies are logged wholesale — only explicit,
  named fields via `log_event(...)`.

## API error handling

- `backend/app/core/errors.py` registers handlers for domain errors
  (`NotFoundError`, `InvalidStateTransitionError`, ...), FastAPI validation
  errors, and a catch-all handler for anything unexpected.
- The catch-all handler logs the full exception server-side
  (`logger.exception(...)`) but returns only `{"error_code": "internal_error",
  "message": "An unexpected error occurred.", "correlation_id": "..."}` to
  the client — no stack traces, no internal paths, no exception messages
  leak into HTTP responses.
- Every error response includes the request's correlation ID so an incident
  can be traced from a user-visible error back to server logs.

## Webhook security

`POST /api/v1/webhooks/razorpay` is, by necessity, an unauthenticated
public endpoint (Razorpay calls it, not a logged-in user) — its entire
security model rests on signature verification, not a bearer token:

- The raw request body is verified via HMAC-SHA256 against
  `RAZORPAY_WEBHOOK_SECRET`, constant-time compared
  (`hmac.compare_digest`) to avoid a timing side-channel. An invalid or
  missing signature returns `401` and the event is never parsed further.
- Nothing in the parsed payload is trusted for identification — the
  affected case is always looked up by `provider_reference` matching a
  `RecoveryPaymentRequest` this system itself created, never by any ID
  the payload could otherwise imply.
- Redelivered/replayed events are deduplicated (`ProcessedWebhookEvent`,
  unique `dedup_key`) before any state change, so a replay cannot double-
  count recovered revenue or re-trigger side effects. The dedup key
  prefers Razorpay's `X-Razorpay-Event-Id` header — stable across
  redeliveries — over a key derived from payload contents, and the claim
  is written *first* so a lost race can never roll back a partially
  applied transition.

Full detail: docs/razorpay-integration.md "Webhook handling."

## API authentication

Every `/api/v1` endpoint requires a shared key in an `X-API-Key` header,
compared in constant time (`secrets.compare_digest`). A missing key and a
wrong key produce byte-identical `401` responses, so a caller learns
nothing about which it was.

Three configuration postures, deliberately mirroring the rest of the
codebase's fail-safe/fail-loud split:

| `API_KEY` | `APP_ENV` | Behaviour |
|---|---|---|
| set | any | Enforced on every `/api/v1` route. |
| empty | `development` / `test` | **Not enforced**, with a startup warning. Keeps local dev and `make up` zero-config, the same way an unset `GROQ_API_KEY` degrades to a safe fallback. |
| empty | `production` | **Refuses to start** (`InsecureConfigurationError`). Silently serving an unauthenticated API in production is the one outcome worth failing loudly over — the same posture as the `RAZORPAY_MODE` guard. |

Two endpoints are deliberately exempt, and neither is an oversight:

- `GET /health` — an infrastructure probe (load balancers, uptime checks).
  Requiring credentials for a liveness check would be counterproductive,
  and it exposes nothing but `{"status", "checks"}`.
- `POST /api/v1/webhooks/razorpay` — Razorpay cannot send our key. It
  authenticates every request by HMAC-SHA256 over the raw body instead,
  which is a *stronger* guarantee than a shared bearer key: it proves the
  payload is untampered, not merely that the caller knows a secret. See
  "Webhook handling" above.

**The frontend's copy of the key is not a secret.** `VITE_API_KEY` is
compiled into the JavaScript bundle at build time and is readable by
anyone who opens browser devtools. It gates access to a *deployment*
("can this browser reach this API at all"); it does not identify or
authorize a person. Treat it as you would a public client ID, rotate it
freely, and do not reuse it anywhere it would function as a real
credential. Anything requiring genuine per-user authorization needs the
identity layer listed under "Explicitly deferred" below.

Implementation: `app/core/auth.py`, wired in `app/api/v1/router.py`.
Tests: `backend/tests/test_api_auth.py`.

## Request correlation IDs

An inbound `X-Correlation-Id` is caller-supplied, is written into every
structured log line for that request, and is echoed back in the response.
It is therefore constrained rather than trusted: at most 128 characters,
and `[A-Za-z0-9._:-]` only. Anything else is replaced with a generated
UUID rather than rejected — a malformed trace header shouldn't fail an
otherwise valid request. This closes a log-forging vector (a newline in
the header would otherwise let a caller inject a fabricated log line).
See `app.main._safe_correlation_id` and `tests/test_app_lifecycle.py`.

## Concurrency

`RecoveryCase.version` (optimistic locking) prevents two simultaneous
writers — a double-clicked action, or a diagnose and an execute racing —
from both partially applying to the same case. The losing write raises
`ConcurrentModificationError` (HTTP 409) instead of silently corrupting
state. See docs/architecture.md "Concurrency" and
docs/razorpay-integration.md "Concurrency."

## Data handling

- `Customer` (`backend/app/models/customer.py`) intentionally stores no PII
  — no name, email, or phone number. Only an opaque
  `external_reference` and aggregate behavioral counters. Contact details
  belong to the payment provider's own customer record, not this database.
- `AuditEvent` (`backend/app/models/audit_event.py`) is append-only by
  convention: `AuditEventRepository` (and every service built on it) exposes
  only `add`/`list`, never `update`/`delete`. Nothing in the codebase
  mutates or deletes an audit row.
- All database access goes through SQLAlchemy's parameterized query
  builder (`select(...)`, `mapped_column(...)`) — no raw string-interpolated
  SQL anywhere in the codebase.

## AI-specific security

Full detail in docs/ai-safety.md; summary here for completeness:

- **Prompt injection**: payment/customer metadata reaching the model is
  typed, structured data (`PaymentRecoveryContext`) with no free-text
  field — there's no attacker-controlled string field for an injected
  instruction to live in. The fixed system prompt is passed via the SDK's
  `system_instruction` parameter, a channel separate from the data
  payload, not string-concatenated with it.
- **Output validation**: every AI response is re-validated against
  `RecoveryRecommendation` (enum-constrained fields, confidence clamped to
  `[0.0, 1.0]` by the schema) before it can influence anything — malformed
  output is rejected, never coerced.
- **No secret ever enters a prompt.**

## CORS

- `CORS_ALLOW_ORIGINS` is an environment-driven, comma-separated allowlist
  (`Settings.cors_origins_list` in `config.py`), defaulting to the Vite dev
  server origin (`http://localhost:5173`) for local development. Production
  deployment must set this explicitly to the real frontend origin(s) — the
  default is not safe to use in production as-is.

## Explicitly deferred to a later phase

- **Per-user authentication and authorization.** A shared API key now
  gates the API (see "API authentication" above), but there are still no
  users, roles, or per-actor permissions — every valid key holder can do
  everything. Adding real identity is a larger design decision than this
  codebase currently needs; it is called out here so the shared key isn't
  mistaken for more than it is.
- **Rate limiting / abuse protection.** Including on `POST
  .../diagnose` and, new in Phase 3, `POST .../execute` and
  `POST /webhooks/razorpay` — nothing currently stops a caller from
  triggering many AI or Razorpay calls in a row (cost, not safety,
  exposure; every individual call is still bounded and gracefully-failing
  — see docs/ai-safety.md and docs/razorpay-integration.md). The webhook
  endpoint's only protection against a flood of forged requests is that
  each one fails signature verification cheaply (`401`) before any DB
  work happens.
- **Secrets management beyond `.env`** (e.g. a real secrets manager for
  deployed environments).
- **Encryption at rest** for the database — deferred until real payment/
  customer data is involved (Phase 1 uses synthetic data only).
- **Dependency/vulnerability scanning in CI** — no CI pipeline exists yet in
  Phase 1 (see `docs/architecture.md` roadmap).

## Reporting a concern

This is a buildathon submission, not a production service with a disclosure
program. If you find an issue while reviewing this repository, open an
issue in the project's tracker rather than filing a formal security report.
