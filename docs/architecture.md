# Architecture

## System shape

RecoverAI is a modular monolith, not a microservices system — three
independently runnable Python/TypeScript packages, one shared Postgres
database, no message queue, no service mesh. Section "Avoid Overbuilding" in
the project brief was explicit about this, and the workflow (failed payment
→ diagnosis → recovery decision → bounded action → measurement) has no
component that benefits from being its own network service yet.

```
                    ┌─────────────────────────┐
                    │        frontend/          │   React + TS + Vite
                    │  (Overview / Cases /      │   read-only dashboard
                    │   Evaluation / Audit)     │
                    └────────────┬──────────────┘
                                 │ HTTP (JSON)
                    ┌────────────▼──────────────┐
                    │         backend/           │   FastAPI + SQLAlchemy 2
                    │  api → services →          │
                    │  repositories → models     │
                    │  domain/ (state machine,   │
                    │  policy engine, provider   │
                    │  interface — pure, no I/O) │
                    │  ai/ (Gemini via a         │
                    │  provider interface —      │
                    │  reasons only, never       │
                    │  authorizes; see           │
                    │  docs/ai-safety.md)        │
                    │  payments/ (Razorpay via a │
                    │  provider interface — only │
                    │  execution_service ever    │
                    │  calls it; see             │
                    │  docs/razorpay-integration │
                    │  .md)                      │
                    └──────┬──────────────┬──────┘
                            │ SQL (asyncpg) │ HTTPS (Test Mode)
                    ┌───────▼──────┐  ┌─────▼─────────┐
                    │  PostgreSQL   │  │ Razorpay API   │
                    │ docker-compose│  │ (Payment Links,│
                    │    .yml       │  │  webhooks)     │
                    └───────────────┘  └────────────────┘

                    ┌─────────────────────────┐
                    │       evaluation/          │   standalone Python package
                    │  generators → baseline →   │   no dependency on backend/
                    │  metrics → reports (JSON)  │
                    └────────────┬──────────────┘
                                 │ reads latest report file
                    (backend/app/services/evaluation_service.py)
```

`evaluation/` is deliberately dependency-free of `backend/` (see
`docs/evaluation-methodology.md`) so it can generate data and run metrics
without a database or API running. The backend only ever *reads* the most
recent JSON report it writes — there's no shared Python import between the
two packages, only a shared JSON shape that's kept in sync by hand
(`evaluation/metrics/report.py` build side, `backend/app/schemas/evaluation.py`
read side).

## Backend layering

```
backend/app/
  api/v1/        FastAPI routers — HTTP concerns only, no business logic
  schemas/       Pydantic request/response DTOs — never expose ORM models directly
  services/      Orchestration: calls repositories, applies domain rules
  repositories/  Query construction (SQLAlchemy) — no business logic
  domain/        Pure logic: enums, state machine, policy engine, provider interface
                 (no SQLAlchemy, no FastAPI, no I/O — this is what's unit-tested hardest)
  ai/            AI provider abstraction (Gemini + fake), prompts, orchestration —
                 reasons only; contains no policy logic (see docs/ai-safety.md)
  payments/      Razorpay provider abstraction (real + fake), webhook signature
                 verification/parsing — only execution_service and the webhook
                 route ever import this (see docs/razorpay-integration.md)
  models/        SQLAlchemy ORM models
  db/            Engine/session setup, declarative base, mixins
  core/          Config, structured logging, error handling — cross-cutting
```

The dependency direction is one-way: `api → services → repositories → models`,
with `domain/` importable from any layer but importing nothing above itself.
`domain/policy.py` and `domain/state_machine.py` have zero I/O — every rule
in them is unit-testable without a database (see
`backend/tests/test_policy_engine.py`, `test_domain_state_machine.py`).

**Why this shape:** the riskiest part of an "agentic" system is exactly the
part between recommendation and action. Isolating that boundary
(`domain/policy.py`) as pure, dependency-free code makes it possible to
exhaustively test every rule (including the recovery-window bug caught and
fixed during Phase 1 — see `docs/recovery-state-machine.md`) without spinning
up infrastructure, and makes it structurally obvious in code review when
something tries to route around it.

## Domain model

See `docs/recovery-state-machine.md` for the state machine in depth. Entity
summary:

- **Customer** — aggregate behavioral signals only (no PII). One customer
  has many payments.
- **Payment** — one payment attempt as reported by the provider: amount,
  status, failure reason, attempt number. One payment has at most one
  recovery case.
- **RecoveryCase** — the revenue-recovery opportunity itself: status
  (state machine), diagnosis, recommended action, confidence, policy
  version, `recovered_amount` (only ever incremented by a confirmed
  webhook — see docs/razorpay-integration.md), and an optimistic-locking
  `version` column (Phase 3 concurrency fix, below). One recovery case
  has many recovery attempts and many payment requests.
- **RecoveryAttempt** — one bounded execution of a recovery action:
  action, status, provider, idempotency/correlation IDs, failure details.
- **RecoveryPaymentRequest** (Phase 3) — mirrors a live Razorpay Payment
  Link: provider reference, short URL, amount vs. amount paid, status.
  Unlike `AuditEvent`, this row is updated in place as the link's status
  changes (via webhook), since it reflects current provider state rather
  than a historical fact.
- **ProcessedWebhookEvent** (Phase 3) — dedup record for inbound Razorpay
  webhook deliveries; see docs/razorpay-integration.md "Idempotency
  strategy."
- **AuditEvent** — append-only; references any entity generically via
  `(entity_type, entity_id)` rather than a foreign key, so the audit trail
  never blocks on or is blocked by domain schema changes.

## Concurrency

`RecoveryCase.version` (SQLAlchemy `version_id_col`) guards against two
concurrent writers acting on the same case — a double-clicked action, or
a diagnose and an execute racing each other. The losing writer's commit
raises `StaleDataError`, converted by
`app/services/concurrency.py::guard_against_concurrent_modification`
into an HTTP 409 `ConcurrentModificationError` rather than silently
letting both operations partially apply. Both `diagnose_recovery_case`
and `execute_recovery_case` are wrapped in this guard. See
`docs/razorpay-integration.md` "Concurrency" and
`backend/tests/test_concurrency.py` (two real, separate DB sessions
racing on one case) for the empirical proof.

## API surface

Versioned under `/api/v1`, except `/health` which is intentionally
unversioned (infrastructure concern, not an API resource). Phase 1's
endpoints were read-only; Phase 2 added the diagnose endpoint (still no
execution); Phase 3 added the execution + webhook endpoints; Phase 5
added the ingestion entry point (`POST /payments`, `POST /recovery-cases`,
`POST /recovery-cases/discover`):

- `GET /health` — liveness + DB connectivity check.
- `GET /api/v1/payments` — paginated, filterable by `status`.
- `POST /api/v1/payments` — ingest one provider-reported payment (the
  workflow entry point). A FAILED payment with `auto_create_case` (the
  default) also opens its `RecoveryCase` in `DISCOVERED`. Executes
  nothing. See `backend/app/services/ingestion_service.py`.
- `GET /api/v1/recovery-cases` — paginated, filterable by `status`.
- `POST /api/v1/recovery-cases` — open a recovery case for one existing
  failed payment; idempotent (returns the existing case with
  `created: false` on a repeat). 404 if the payment is unknown, 422 if it
  is not FAILED.
- `POST /api/v1/recovery-cases/discover` — sweep failed payments that have
  no case yet and open one for each; safe to run repeatedly (e.g. a cron).
- `GET /api/v1/recovery-cases/{id}` — detail, including attempts, payment
  requests, and payment.
- `POST /api/v1/recovery-cases/{id}/diagnose` — runs AI diagnosis +
  recommendation, then the deterministic policy engine, then moves the
  case to `APPROVED`/`STOPPED`/`ESCALATED`. Never executes a recovery
  action. See docs/ai-architecture.md and docs/ai-safety.md.
- `POST /api/v1/recovery-cases/{id}/execute` — re-checks policy, then
  (only for `SEND_PAYMENT_LINK`) creates a real Razorpay Test Mode
  Payment Link, or escalates. Never itself marks a case `RECOVERED`. See
  docs/razorpay-integration.md.
- `GET /api/v1/recovery-cases/{id}/timeline` — chronological audit events
  for a case and its attempts, for the frontend timeline view.
- `POST /api/v1/webhooks/razorpay` — signature-verified inbound Razorpay
  events; the only path to `RECOVERED`. See docs/razorpay-integration.md.
- `GET /api/v1/audit-events` — paginated, filterable by `entity_type`,
  `entity_id`, `event_type`, and `correlation_id` (the last two added for
  the dashboard's Policy Decisions and Audit Trail views).
- `GET /api/v1/evaluation/summary` — the latest **simulated** evaluation
  report, or an explicit `{"status": "no_evaluation_run"}` if none exists.
- `GET /api/v1/evaluation/recovery-summary` — real, live metrics computed
  from this deployment's own database (Phase 3) — never combined with the
  simulated summary above. See docs/razorpay-integration.md "Simulated
  vs. real evaluation."

Pydantic schemas (`backend/app/schemas/`) are the only thing the API ever
serializes — ORM models never cross the API boundary directly.

## Frontend

React + TypeScript + Vite, no UI framework, no Tailwind (not justified for
four static-shaped pages). Client-side "routing" is a `useState<Section>` in
`App.tsx` rather than a router library — there is no deep-linking
requirement in Phase 1 and adding `react-router` for four tabs would be the
kind of unnecessary abstraction the project brief warns against.

```
frontend/src/
  api/        types.ts (hand-mirrors backend Pydantic schemas), client.ts (fetch wrapper),
              useApiResource.ts (small load/error/success hook, no caching layer)
  components/ Layout (shell + nav), StatusStates (loading/error/empty),
              CaseDetailPanel (AI investigation + execution view — see below),
              CaseTimeline (chronological audit events for one case)
  pages/      OverviewPage, RecoveryCasesPage, EvaluationPage, AuditTrailPage
```

Empty states are explicit and real (`EmptyState` component) — the frontend
never fabricates numbers when the API returns no data. `EvaluationPage`
specifically renders the difference between "no evaluation has been run"
and "here are real computed metrics." `OverviewPage` renders the
simulated evaluation summary and the live `RecoverySummaryRead` as two
separate, separately-labeled card groups — never combined into one figure
(see docs/razorpay-integration.md "Simulated vs. real evaluation").

`RecoveryCasesPage` rows are clickable, opening `CaseDetailPanel`: payment
context, the AI diagnosis/recommendation (or "No diagnosis available yet"),
and — rendered as a visually distinct, separately-labeled block — the
policy engine's ALLOW/BLOCK decision. The recommendation and the
authorization are never merged into one "approved" concept in the UI, on
purpose (see docs/ai-safety.md). Below that, a "Recovery execution"
section shows any Payment Link created for the case (short URL, amount
paid vs. amount due) and, only when the case is `APPROVED`, an "Execute
recovery" button — the frontend only requests execution; the backend
independently re-verifies approval, policy, and amount before ever
calling Razorpay (see docs/razorpay-integration.md). A `CaseTimeline`
renders the full audit trail for the case chronologically at the bottom
of the panel.

## Evaluation package

```
evaluation/
  schemas/       Dataset/case/ground-truth Pydantic models (standalone, not backend enums)
  generators/    Six scenario generators, deterministic dataset builder, dev/held-out split
  baseline/      Non-AI rule-based reference strategy
  ai_strategy/   Gemini-backed evaluation strategy (independent of backend/app/ai)
  metrics/       financial.py, decision.py, safety.py, operational.py, report.py
  datasets/generated/   git-ignored generated dataset JSON
  reports/              git-ignored generated report JSON (single-strategy)
  reports/ai_comparisons/  git-ignored baseline-vs-AI comparison reports (kept
                            separate so the backend never mis-parses one)
  generate_dataset.py   CLI: python -m evaluation.generate_dataset --count 500 --seed 42
  run_evaluation.py     CLI: python -m evaluation.run_evaluation --dataset <path>
  run_ai_evaluation.py  CLI: python -m evaluation.run_ai_evaluation --dataset <path> --limit 20
```

Full methodology, metric definitions, and — importantly — the limitations of
this synthetic-data approach are in `docs/evaluation-methodology.md`. Read
that before citing any number this package produces.

## Why two separate Python virtual environments

`backend/` and `evaluation/` each have their own `requirements.txt` and are
meant to be installed into separate virtualenvs (`backend/.venv`,
`.venv-eval` at repo root, or whatever the developer prefers). This isn't
accidental duplication — `evaluation/` has almost no dependencies
(`pydantic` and, as of Phase 2, `google-genai` for the optional AI
comparison) precisely so it can run in a minimal CI job or a teammate's
machine without installing FastAPI/SQLAlchemy/asyncpg. See "decoupling"
notes in `docs/evaluation-methodology.md`.

## What's implemented vs. planned

See the README's "Current Phase" and "Roadmap" sections for the
authoritative, up-to-date list — it's kept next to the setup instructions so
it's harder for the two to drift apart.
