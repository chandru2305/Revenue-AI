# Razorpay Integration

Source of truth: [`backend/app/payments/`](../backend/app/payments/),
[`backend/app/services/execution_service.py`](../backend/app/services/execution_service.py),
[`backend/app/services/webhook_service.py`](../backend/app/services/webhook_service.py).
This document explains what's actually implemented, what was verified
against current Razorpay documentation, and — most importantly — exactly
what "recovered revenue" does and doesn't mean once a real provider is
involved.

## Reality check: what Razorpay's API actually offers

Razorpay's Payments API is for fetching payment records and moving an
`authorized` payment to `captured`. There is no "retry a failed payment"
endpoint — a failed payment is dead; you cannot resubmit it. The correct
way to give a customer another chance to pay is to create a new payment
request and send it to them: a **Payment Link**
(`POST /v1/payment_links`), which Razorpay hosts, can be sent by
SMS/email, and produces its own new payment on completion.

Because of this, Phase 3 implements exactly one recovery action end to
end: `SEND_PAYMENT_LINK`. `RETRY_PAYMENT` remains a valid AI/policy
recommendation (see `docs/recovery-state-machine.md`), but the executor
does not implement it against a real provider — there is no Razorpay
endpoint that does what its name implies. A case recommended for
`RETRY_PAYMENT` is escalated by the executor
(`_IMPLEMENTED_ACTIONS` in `execution_service.py`) rather than having a
fabricated retry endpoint invented for it.

## What was verified against current Razorpay documentation

During Phase 3 development, before writing `app/payments/providers/razorpay.py`,
the following were checked against Razorpay's current official API
reference (not assumed from memory or invented):

- Payment Links API: `POST /v1/payment_links`, `GET /v1/payment_links/:id`,
  `GET /v1/payment_links/?reference_id=...`,
  `POST /v1/payment_links/:id/notify_by/:medium` — request/response
  shapes, required fields (`amount`, `currency`, `reference_id`,
  `description`), and status vocabulary (`created`, `partially_paid`,
  `paid`, `expired`, `cancelled`).
- `GET /v1/payments/:id` — payment status vocabulary
  (`created`, `authorized`, `captured`, `refunded`, `failed`).
- Authentication: HTTP Basic Auth with the Key ID as username and Key
  Secret as password.
- Webhook signature verification: HMAC-SHA256 over the **raw** request
  body, keyed with the dashboard-configured webhook secret, delivered in
  the `X-Razorpay-Signature` header, hex-encoded.
- Test Mode Payment Link limit: 30 per business account. This is why the
  simulated evaluation (500+ cases) never creates real Payment Links —
  see "Simulated vs. real evaluation" below.

No endpoint, field, or status value in this codebase was invented; where
the mapping had to fail closed (e.g. an unrecognized future status
string), that's called out explicitly in code and below.

## Provider abstraction

`backend/app/domain/providers/base.py` defines `PaymentProvider`, an
abstract interface the domain layer depends on instead of Razorpay
directly:

```
fetch_payment(provider_payment_id) -> ProviderPaymentSnapshot
create_payment_link(request) -> PaymentLinkSnapshot
fetch_payment_link(provider_reference) -> PaymentLinkSnapshot
find_payment_link_by_reference(reference_id) -> PaymentLinkSnapshot | None
notify_payment_link(provider_reference, medium) -> bool
```

Two implementations exist:

- `app/payments/providers/razorpay.py::RazorpayPaymentProvider` — the
  real thing, over `httpx.AsyncClient`, Test Mode only (see below).
- `app/payments/providers/fake.py::FakePaymentProvider` — configurable
  success/failure/timeout/ambiguous/reconciliation behavior, used by
  every automated test and by large-scale simulated evaluation. CI never
  requires real Razorpay credentials — nothing in `backend/tests/`
  constructs a `RazorpayPaymentProvider` against the real API.

`app/payments/dependencies.py::get_payment_provider()` mirrors the
Phase 2 `app.ai.dependencies` pattern: with no `RAZORPAY_KEY_ID`/
`RAZORPAY_KEY_SECRET` configured, it returns an
`_UnconfiguredPaymentProvider` whose every method raises
`PaymentProviderAuthError` — the API stays up and every execution
attempt fails safely into `ESCALATED`, exactly like an AI outage falls
back to `ESCALATE` in Phase 2.

## Test Mode guard

`RazorpayPaymentProvider.__init__` calls `_require_test_mode(mode)`,
which raises `RazorpayModeError` unless `settings.razorpay_mode == "test"`
— the default, and the only value ever used in this codebase. This is
not a constructor argument that can be overridden per call; it is checked
once, at construction, from config, so there is no code path that can
accidentally point the executor at Razorpay Live Mode.

## Payment Link recovery flow

```
POST /api/v1/recovery-cases/{id}/execute
  -> execute_recovery_case (guarded against concurrent modification)
  -> validate_transition(status, EXECUTING)     -- auth + idempotency in one check
  -> audit: execution_requested
  -> re-check policy with FRESH case data       -- see "Re-check before execution"
  -> audit: policy_rechecked
  -> transition APPROVED -> EXECUTING
  -> if policy now BLOCKs, or action isn't SEND_PAYMENT_LINK: escalate, stop
  -> commit (ends the DB transaction before any external call)
  -> create a RecoveryAttempt (IN_PROGRESS), audit: execution_started, commit
  -> provider.create_payment_link(...)          -- the one real Razorpay write
  -> success: create a RecoveryPaymentRequest, audit: payment_link_created,
     case stays EXECUTING (NOT recovered)
  -> ambiguous (timeout/5xx): reconcile via find_payment_link_by_reference,
     adopt if found, else escalate as AMBIGUOUS_RESULT
  -> definite failure (4xx auth/validation/rate-limit): escalate
```

A case that successfully creates a Payment Link does **not** move to
`RECOVERED`. It stays `EXECUTING`, holding a live `RecoveryPaymentRequest`
whose status starts `created`. The only way forward from there is a
Razorpay webhook.

## Amount safety

The executor never accepts an amount from the API request — there isn't
one; `POST .../execute` takes no body — and never accepts one from AI
output. `_create_payment_link` reads `amount = case.payment.amount`
directly from the canonical `Payment` row and asserts, immediately before
sending it to Razorpay, that it still matches `case.payment.amount`
(defends against any future code path that might mutate a local copy).
Amounts are always integer minor units (paise) end to end — Razorpay's
API, the DB columns, and `PaymentLinkSnapshot` all use the same integer
unit; no floating-point currency math exists anywhere in this path.

## Re-check before execution

A case can sit in `APPROVED` for an arbitrary amount of time before a
human or scheduler triggers execution. During that gap, nothing about
the case's eligibility is guaranteed to still hold — the recovery window
may have expired, or (in a system with concurrent diagnosis) contact
counts could have changed. `execute_recovery_case` therefore re-runs the
**same** deterministic `evaluate_policy` function diagnosis_service uses
— not a re-run of the AI — against freshly loaded case/payment data,
before doing anything irreversible. If the re-check now returns `BLOCK`,
the case is escalated instead of executed; the original `APPROVED`
decision is never treated as a standing authorization that outlives its
own inputs.

## Execution state machine

No new states or transitions were added for Phase 3. `EXECUTING`, added
in Phase 1, is reused as-is: `APPROVED -> EXECUTING -> {RECOVERED, FAILED,
ESCALATED}`. `EXECUTING` is overloaded to mean both "provider call in
flight" and "Payment Link created, awaiting customer payment" — this was
a deliberate choice over adding a new `AWAITING_PAYMENT` state, because
the state machine's job is to describe *what the system is allowed to do
next* (nothing, until the webhook or a human acts), and that's identical
in both sub-phases. The distinction between "call in flight" and
"awaiting payment" is visible instead at the `RecoveryAttempt`/
`RecoveryPaymentRequest` level (`attempt.status`,
`payment_request.status`), which is where it actually differs mechanically.
See `docs/recovery-state-machine.md` for the full transition table —
unchanged from Phase 1/2.

## Idempotency strategy

Two independent mechanisms, for two independent concerns:

1. **Re-execution of the same case.** `validate_transition(status,
   EXECUTING)` at the top of `_execute_recovery_case` is the entire
   mechanism — only a case in `APPROVED` can transition to `EXECUTING`,
   so a case that's already `EXECUTING`, `RECOVERED`, `ESCALATED`, or
   `FAILED` structurally cannot execute again. No separate idempotency
   table was needed for this, matching the pattern already established
   for diagnosis in Phase 2.
2. **Webhook delivery.** Razorpay's payload has no single top-level
   delivery ID exposed consistently across event types, so
   `ParsedWebhookEvent.dedup_key` is derived as
   `f"{event_type}:{payment_link_id}:{payment_id}"` and stored, unique,
   in `ProcessedWebhookEvent`. A redelivered webhook (Razorpay retries on
   non-2xx, and operators may replay from the dashboard) is detected via
   that unique constraint and short-circuited to a `"duplicate"` result
   before any state change is attempted.

`RecoveryAttempt.idempotency_key` (set to the same UUID used as the
Payment Link's `reference_id`) exists for a third purpose: reconciliation
after an ambiguous provider response (below), not for rejecting repeat
`/execute` calls.

## Concurrency

Fixed before execution was implemented (Phase 3 requirement, not
optional polish): `RecoveryCase` now carries a SQLAlchemy
`version_id_col`. Two simultaneous writers against the same case (e.g. a
double-clicked "Execute" button, or a diagnose and an execute racing)
cause the loser's `COMMIT` to raise `StaleDataError`, which
`app/services/concurrency.py::guard_against_concurrent_modification`
catches, rolls back, and converts into a `ConcurrentModificationError`
(HTTP 409, `error_code: "concurrent_modification"`). Both
`diagnose_recovery_case` and `execute_recovery_case` are wrapped in this
guard. `backend/tests/test_concurrency.py` proves it empirically with two
real, separate DB sessions racing on the same case — not merely by
inspecting the mapper configuration.

## Handling an ambiguous provider result

If `create_payment_link` times out, or Razorpay returns a 5xx, the
request may or may not have actually succeeded server-side — Razorpay
never guarantees idempotent Payment Link creation on retry, so blindly
calling `create_payment_link` again risks creating a duplicate, real
Payment Link for the same case.

`RazorpayPaymentProvider.create_payment_link` converts a timeout or
"unavailable" outcome into `PaymentProviderAmbiguousError` rather than a
plain failure. `execution_service._handle_ambiguous_creation` reacts by
calling `find_payment_link_by_reference(reference_id)` — the same
`reference_id` the failed create attempt used — to ask Razorpay directly
whether the link actually exists:

- **Found:** the link was created despite the ambiguous response; it is
  adopted (`_record_payment_link_created`) rather than duplicated.
- **Not found (or reconciliation itself fails):** the attempt is marked
  `FAILED` with `AMBIGUOUS_RESULT`, and the case is escalated for manual
  review rather than silently retried.

## Webhook handling

`POST /api/v1/webhooks/razorpay` (`app/api/v1/webhooks.py`):

1. Reads the **raw** request body (`await request.body()`) — signature
   verification is byte-exact over what Razorpay actually signed; FastAPI's
   parsed-then-re-serialized JSON is not guaranteed to match byte-for-byte
   (key order, whitespace) and would silently break verification.
2. Verifies `X-Razorpay-Signature` via HMAC-SHA256 with
   `hmac.compare_digest` (constant-time). Invalid or missing signature →
   `401`, event is not processed, nothing is trusted from the body.
3. Parses the body only after verification succeeds.
4. Deduplicates via `ProcessedWebhookEvent.dedup_key` (see Idempotency
   above) — a duplicate delivery is acknowledged but not reprocessed.
5. Only `payment_link.paid` / `.expired` / `.cancelled` events (via their
   embedded `payment_link.entity.status`) affect case state; anything
   else is acknowledged and ignored.
6. Looks up the affected case by `payment_request.provider_reference ==
   payment_link_id` — never by any ID the client could supply directly,
   since the whole point of verification is that the payload is
   authentic Razorpay data, not client input.
7. If the case has already moved on from `EXECUTING` (a second, later
   event for an already-resolved case), the event is recorded but no
   transition is attempted — the state machine would reject it anyway,
   and this keeps that path an explicit "ignored" rather than a 500.

The webhook handler never trusts a client-supplied "this payment
succeeded" claim from anywhere else in the system — `RECOVERED` is
reachable from exactly one code path: a signature-verified webhook event.

## What "recovered" means

**A Payment Link being created is not counted as recovered revenue.
Revenue is counted only after provider-confirmed successful payment** —
a signature-verified `payment_link.paid` webhook where
`amount_paid >= amount` (see `_apply_payment_link_event`'s `fully_paid`
check). Only at that point does `case.recovered_amount` increase and the
case transition to `RECOVERED`. A partially-paid link
(`amount_paid < amount`) never marks a case recovered — Razorpay Payment
Links are created with `accept_partial: False` specifically so partial
payment isn't a state this system has to treat as success; a payment_link
event for such a link still routes through `_apply_payment_link_event`,
but `fully_paid` will be false and the case is marked `FAILED`.
`recovery_rate` in `RecoverySummaryRead` (`GET
/api/v1/evaluation/recovery-summary`) is computed as
`confirmed_recovered_revenue / eligible_revenue`, never
"payment links created / cases."

## Financial invariants

Enforced in code and asserted by `backend/tests/test_financial_invariants.py`:

- `case.recovered_amount` starts at `0` and is only ever incremented, by
  the exact `amount_paid` from a confirmed webhook.
- Transitioning to `EXECUTING` never sets `recovered_amount`.
- The amount sent to the provider always equals the canonical
  `Payment.amount` at the moment of the call.
- `total_revenue_at_risk` in the live summary is computed from
  `Payment.amount`, never from anything AI- or request-supplied.
- Multiple recovery cases never share or cross-contaminate financial
  state — each is scoped by its own `recovery_case_id`/
  `recovery_attempt_id` foreign keys.

## Failure handling and stopping rules

Every provider failure is classified into a `ProviderFailureCategory`
(`provider_timeout`, `provider_auth_error`, `provider_rate_limit`,
`provider_validation_error`, `duplicate_request`,
`webhook_verification_failure`, `payment_failed`, `payment_expired`,
`ambiguous_result`, `unknown_provider_error`) and recorded on the
`RecoveryAttempt`. Phase 3 does not add automatic re-execution on
failure: every execution failure — provider error, ambiguous result, or
a policy re-check that now blocks — routes to `ESCALATED`, a terminal
state, for human review, rather than attempting a second Payment Link
automatically. This is intentionally more conservative than the
diagnosis-side `FAILED -> DIAGNOSING` retry loop (`docs/recovery-state-machine.md`):
an execution failure has real external-world side effects (a
possibly-created, possibly-not Payment Link) in a way a diagnosis
failure never does, so it stops for a human rather than looping.

Customer contact limits (`max_contacts_reached`, checked by the same
`evaluate_policy` used at diagnosis time) apply equally at the
re-check-before-execution step — a case that accumulated enough contacts
between `APPROVED` and execution time is blocked and escalated, not
executed anyway.

## Notification

`RazorpayPaymentProvider.notify_payment_link` wraps
`POST /payment_links/:id/notify_by/:medium` (SMS or email, per
`NotificationMedium`). Nothing in the execution flow calls it
automatically in Phase 3 — Razorpay itself can be configured to send the
Payment Link notification on creation, and the explicit notify endpoint
exists in the provider interface for a future phase (e.g. a manual
"resend" action) rather than being wired into the automated flow now.
This keeps Phase 3's automated customer contact surface to exactly what
Razorpay's own Payment Link creation already does, per the "no real
customer communication beyond controlled testing" constraint for this
phase.

## Correlation IDs

Every audit event and provider call in the execution/webhook paths
carries the same `correlation_id` propagated from the Phase 1
`x-correlation-id` middleware (`execute_recovery_case`'s
`correlation_id` parameter, threaded through `_create_payment_link`,
`_handle_ambiguous_creation`, etc.). For a webhook delivery, which has no
inbound `x-correlation-id` from Razorpay, `correlation_id` falls back to
the event's own `dedup_key` so every audit row for that delivery is still
traceable as one unit even without a client-supplied header.

## Credential protection

- `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` are
  read only from `app/core/config.py` (`pydantic-settings`), never
  hardcoded, never logged. `app/core/logging.py`'s redaction (a
  substring match on field names, covering `secret`/`token`/`api_key`/
  `authorization`/`password`-shaped names — including `webhook_secret`,
  not just an exact `secret`) would catch any of these even if a caller
  accidentally passed one through `extra_fields`; nothing in the Phase 3
  code paths does — `RazorpayPaymentProvider._log_call` only logs
  method/path/status/latency, never headers or bodies. See
  docs/security.md "Logging."
- `httpx.BasicAuth` handles the Authorization header construction — the
  secret is never manually string-formatted into a log-adjacent variable.
- With no credentials configured, the app does not crash — see "Provider
  abstraction" above.

## Simulated vs. real evaluation — never mixed

Three genuinely different things exist in this codebase. They must never
be reported as if they were one number, and the second and third — while
they compute through the *same* aggregation endpoint — must always be
labeled by which one actually produced the rows behind it:

1. **Simulated evaluation** (`evaluation/`, `GET
   /api/v1/evaluation/summary`): 500+ synthetic cases scored against a
   held-out ground truth, using `FakePaymentProvider` semantics (no
   network calls). This is what `docs/evaluation-methodology.md` covers
   in depth, including its own honestly-stated limitations. It exists to
   measure decision quality and safety at scale, not real payment
   outcomes. A completely different code path from #2/#3 below — it
   never touches `RecoveryCase` rows at all.
2. **Real Razorpay Test Mode integration** (`GET
   /api/v1/evaluation/recovery-summary`, backed by
   `recovery_summary_service.py`): real database metrics from whatever
   recovery cases actually exist in this deployment's own database,
   computed by aggregating actual `RecoveryCase`/`RecoveryPaymentRequest`
   rows — no synthetic data, no ground truth, no simulation. Given
   Razorpay Test Mode's 30-Payment-Links-per-business limit, this is
   necessarily a small, controlled sample, not a batch of hundreds — see
   `backend/tests/integration/razorpay_live_check.py`, a manual-only
   script (not collected by pytest, not run in CI) that creates exactly
   one real Test Mode Payment Link to demonstrate the integration
   actually works end to end against the live API, without consuming the
   Test Mode Payment Link quota at scale.
3. **Demo batch** (`backend/scripts/seed_demo_batch.py`, same `GET
   /api/v1/evaluation/recovery-summary` endpoint as #2 — it's a pure
   aggregation over whatever rows exist, with no way to tag their
   provenance): runs 30 cases through the exact same production pipeline
   as #2 — `diagnose_recovery_case` → `execute_recovery_case` → webhook
   processing → `compute_recovery_summary` — substituting only
   `FakeAIProvider`/`FakePaymentProvider` for Gemini/Razorpay. Exists
   because #2 requires live credentials that were not available in any
   build environment; without it, "measured money recovered across a
   batch" would be undemonstrable in this repository as cloned — every
   figure on a fresh, credential-less database is honestly zero. The
   batch is deliberately mixed (recovered, expired-unpaid, policy-stopped,
   AI/policy-escalated) rather than 100% success, specifically so it also
   demonstrates compliant escalation and stopping rules, not just revenue.
   **Never** present a demo-batch-populated `recovery-summary` response
   as a real Razorpay result — state plainly which one produced it.

The frontend's Overview page renders the real/demo `recovery-summary`
metrics alongside the simulated evaluation summary, explicitly labeled,
side by side — never averaged or summed together. It cannot itself tell
#2 and #3 apart (see above) — say which one you ran when presenting it.

## Real Razorpay Test Mode results (this build)

**Real Razorpay Test Mode execution: NOT RUN — credentials unavailable**
in the environment this phase was built in. `backend/tests/integration/razorpay_live_check.py`
exists and is ready to run given `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`
Test Mode credentials, but no such credentials were available, so no real
Payment Link was created and no real recovered-revenue figure exists from
this build. Every "recovered revenue" number produced during this phase
came from `FakePaymentProvider`-backed automated tests
(`backend/tests/test_execution_workflow.py`,
`test_webhook_workflow.py`) and, at batch scale, from the demo batch
(`backend/scripts/seed_demo_batch.py`, `backend/tests/test_demo_batch.py`)
— every one reported as such, never presented as a Razorpay result.

## Limitations

- `RETRY_PAYMENT` has no real-provider implementation, by design (see
  "Reality check" above) — a case recommending it is escalated, not
  executed.
- No automatic retry of a failed execution — every execution failure
  escalates to a human, on purpose (see "Failure handling").
- Notification is not triggered automatically by this codebase in
  Phase 3 (see "Notification").
- The real integration check
  (`backend/tests/integration/razorpay_live_check.py`) has not actually
  been run against Razorpay in this environment — its correctness rests
  on documentation review and the same HTTP client code path the
  automated tests exercise against `FakePaymentProvider`, not on an
  observed live response.
- `RecoverySummaryRead` reflects whatever is in this deployment's own
  database — on a fresh database with zero executed cases, every figure
  in it is legitimately zero, which is the correct, honest state, not a
  bug.
