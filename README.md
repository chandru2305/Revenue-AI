# RecoverAI

## Problem

Failed payments quietly leak revenue. Most of that money is recoverable —
but only if someone diagnoses *why* the payment failed, picks the right
recovery action, and does it within safe, auditable bounds. Most teams
either do this manually (slow, inconsistent) or not at all (money left on
the table).

## Solution

RecoverAI is an agentic revenue-recovery system that identifies at-risk
failed payments, analyzes the available payment and customer context,
determines an appropriate recovery strategy, validates that strategy
against deterministic safety policies, executes only permitted actions, and
records every decision and outcome for measurement and audit.

The core design principle: **AI reasons. Deterministic systems enforce.**
An AI layer (Google Gemini, as of Phase 2) diagnoses, estimates, and
recommends — but retry limits, recovery windows, contact caps, amount
validation, and action eligibility are enforced by a pure, dependency-free
policy engine ([`backend/app/domain/policy.py`](backend/app/domain/policy.py))
that no LLM can bypass. See [`docs/architecture.md`](docs/architecture.md),
[`docs/ai-safety.md`](docs/ai-safety.md), and
[`docs/recovery-state-machine.md`](docs/recovery-state-machine.md) for how
this is enforced structurally, not just by convention.

### Why AI, specifically

A pure rule-based system (the `evaluation/baseline` strategy, also shipped)
can already apply fixed thresholds — retry count, days elapsed, contact
count. What it can't do well is *read the specific case*: a card declined
for "insufficient funds" on attempt 1 calls for a different action than
the same decline on attempt 4 with two prior contacts, and the gap widens
with messier real-world failure reasons text. The AI's job is narrowly
that judgment call — diagnosis category, a recovery action recommendation,
and a confidence score — never the money-moving decision itself. Every
recommendation, AI-sourced or a safe fallback, still passes through the
same policy engine before anything happens; see "Core workflow" below and
[`docs/ai-safety.md`](docs/ai-safety.md) for the enforcement mechanics.

### Core workflow

```
Revenue at risk (failed payment)
  → AI diagnosis (root cause + recommended action + confidence)
  → deterministic policy decision (ALLOW / BLOCK)
      ├─ BLOCK → STOPPED / ESCALATED (human review, nothing executes)
      └─ ALLOW → bounded execution
  → Razorpay Test Mode Payment Link created ("recovery initiated")
  → signature-verified webhook (provider confirmation)
      ├─ confirmed paid in full → RECOVERED (confirmed recovered revenue)
      └─ expired / cancelled / ambiguous → FAILED / ESCALATED
  → full audit trail + measurable recovery rate
```

Every arrow above is a real, tested code path — not illustrative. See
`docs/recovery-state-machine.md` for the exact state machine and
`docs/razorpay-integration.md` for the execution/webhook flow this
diagram summarizes.

## Track

Razorpay Buildathon — Track 03: AI Revenue Recovery.

Scope for this build is intentionally narrow: **failed payment recovery**
only (payment failure → root-cause diagnosis → recovery decision → bounded
recovery action → result → stop/escalate → audit). Checkout abandonment,
subscription recovery, and receivables are explicitly out of scope.

## Submission Status

**Feature-complete through Phase 3 (Razorpay Test Mode integration &
bounded execution), hardened for submission in a Phase 4 review pass.**
Phase 1 established the domain model, deterministic policy engine, state
machine, API, database, synthetic evaluation framework, and frontend
shell. Phase 2 added a real Gemini-backed diagnosis layer
(`backend/app/ai/`) walking a case through
`DIAGNOSING → RECOMMENDED → POLICY_REVIEW → APPROVED/STOPPED/ESCALATED`,
still executing nothing. Phase 3 added the execution side: a
`PaymentProvider` abstraction (`backend/app/domain/providers/base.py`) +
a real `RazorpayPaymentProvider` (Test Mode only) that creates Razorpay
Payment Links, a `POST /api/v1/recovery-cases/{id}/execute` endpoint that
re-checks policy with fresh data before calling Razorpay, a signature-
verified `POST /api/v1/webhooks/razorpay` that is the *only* path to
`RECOVERED`, an optimistic-locking concurrency fix, and a frontend
execution/timeline view. Phase 4 reviewed the whole system end to end for
submission readiness — closed a log-redaction gap (exact-match keys →
substring match, with a new regression test), a `.gitignore` gap (a local
SQLite dev database wasn't excluded), several stale/misleading UI
strings, and — the one genuine gap found in a final audit against the
literal Track 03 requirement ("measured money recovered across a batch,
with compliant escalation, stopping rules, and an audit trail") — that a
credential-less environment had no way to *demonstrate* a measured batch
recovery at all, closed by `backend/scripts/seed_demo_batch.py` (see
"Demo Batch" below). None of this touched the working architecture. See
[Implemented / Planned / Experimental](#implemented--planned--experimental)
below for the precise line, and
[`docs/razorpay-integration.md`](docs/razorpay-integration.md) for the
full Razorpay integration write-up — including the explicit statement:
**a Payment Link being created is not counted as recovered revenue;
revenue is counted only after provider-confirmed successful payment.**

No credentials for a real Razorpay Test Mode account or the Gemini API
were available in the build environment at any phase. **Every number in
this repository that looks like "recovered revenue" or "AI accuracy" is
either a synthetic-evaluation figure or a `FakePaymentProvider`-backed
test result — never a real Razorpay or Gemini result presented as one.**
See "Known Limitations" below.

## Architecture

Modular monolith: `backend/` (FastAPI + SQLAlchemy + PostgreSQL),
`evaluation/` (standalone Python package, no dependency on `backend/`), and
`frontend/` (React + TypeScript + Vite), sharing one database and one JSON
report contract. Full write-up, including the layering rationale and why
`evaluation/` is kept dependency-free: [`docs/architecture.md`](docs/architecture.md).

## Domain Model

`Customer` → `Payment` → `RecoveryCase` → `RecoveryAttempt` →
`RecoveryPaymentRequest` (a live Razorpay Payment Link), plus an
append-only `AuditEvent` trail and a `ProcessedWebhookEvent` dedup table.
Full field list and design rationale:
[`docs/architecture.md#domain-model`](docs/architecture.md#domain-model).
Recovery-case lifecycle (state machine, valid transitions, why `STOP`/
`ESCALATE` can never be blocked by an expired window):
[`docs/recovery-state-machine.md`](docs/recovery-state-machine.md).

## Evaluation Methodology

Synthetic dataset (6 scenario types, deterministic seeding), a rule-based
non-AI baseline strategy, a Gemini-backed strategy scored on a deterministic
held-out split, and financial/decision/safety/operational metrics computed
from an actual run — never fabricated. Includes an honest limitations
section (ground truth and baseline share domain heuristics by construction;
"recovered revenue" is a simulated proxy since there's no live gateway
yet). Full methodology:
[`docs/evaluation-methodology.md`](docs/evaluation-methodology.md).

## AI Architecture & Safety

How the Gemini provider abstraction is built and what was actually
verified against current API docs: [`docs/ai-architecture.md`](docs/ai-architecture.md).
What the AI can and cannot do, enforced by code shape rather than
convention, fallback behavior, and the prompt-injection posture:
[`docs/ai-safety.md`](docs/ai-safety.md).

## Razorpay Integration

What was verified against current Razorpay API/webhook documentation,
why the only implemented recovery action is a Payment Link (not an
invented "retry" endpoint), amount safety, idempotency and ambiguous-
result handling, webhook verification, the concurrency fix, and the hard
line between "Payment Link created" and "revenue recovered":
[`docs/razorpay-integration.md`](docs/razorpay-integration.md).

## Failure Handling

RecoverAI is built for the case where something goes wrong, not just the
happy path. Concretely tested, not just described:

- **Provider timeout during Payment Link creation** — the create call may
  or may not have succeeded server-side. RecoverAI never blindly retries
  (that risks a duplicate real Payment Link); it reconciles by asking
  Razorpay directly (`find_payment_link_by_reference`) using the same
  reference ID, adopts the link if it was actually created, and escalates
  for human review if it genuinely can't tell. See
  `test_ambiguous_result_reconciled_as_success` and
  `test_ambiguous_result_unresolved_escalates` in
  `backend/tests/test_execution_workflow.py`.
- **Duplicate/concurrent execution** — a double-clicked "Execute" button
  or two requests racing on one case: the state machine (only `APPROVED`
  can enter `EXECUTING`) plus optimistic locking
  (`ConcurrentModificationError`, HTTP 409) make the second one fail
  safely rather than double-executing. `backend/tests/test_concurrency.py`
  proves this with two real, separate concurrent DB sessions.
- **Duplicate/replayed webhook** — deduplicated via
  `ProcessedWebhookEvent` before any state change; never double-counts
  recovered revenue. `backend/tests/test_webhook_workflow.py`.
- **Invalid/forged webhook signature** — rejected with `401` before the
  payload is even parsed; nothing from an unverified request is trusted.
- **Payment expired / cancelled / partially paid** — none of these mark a
  case `RECOVERED`; only a fully-paid, signature-verified event does.
- **AI/Gemini unavailable or returns malformed output** — falls back to a
  safe `ESCALATE` recommendation rather than crashing or guessing
  (`backend/tests/test_diagnosis_workflow.py`).
- **Razorpay unconfigured/unreachable** — execution gracefully escalates
  rather than the API crashing; verified against a real running server in
  this environment (`RAZORPAY_KEY_ID` unset → `provider_auth_error` →
  `ESCALATED`, not a 500).

See [`docs/razorpay-integration.md`](docs/razorpay-integration.md)
"Handling an ambiguous provider result" and "Failure handling and
stopping rules" for the full design.

## Metrics

Two genuinely different metric *sources*, never combined into one number
(see "Evaluation Methodology" below and
[`docs/razorpay-integration.md`](docs/razorpay-integration.md)
"Simulated vs. real evaluation"):

| Metric | Source | Endpoint |
|---|---|---|
| Eligible revenue, revenue at risk | Real DB (this deployment's actual `recovery_cases` rows) | `GET /api/v1/evaluation/recovery-summary` |
| Confirmed recovered revenue | Real DB — only webhook-confirmed payments | same |
| Recovery rate | `confirmed_recovered_revenue / eligible_revenue` — **never** "Payment Links created / cases" | same |
| Recovery attempts, successful Payment Links created | Real DB | same |
| Cases by status (incl. escalated, stopped, failed) | Real DB | same |
| Escalation rate, stop rate, provider failure rate | Real DB | same |
| Simulated recovery rate, decision accuracy, safety violations | Synthetic dataset (500+ cases), never real money, different methodology entirely | `GET /api/v1/evaluation/summary` |

All amounts are integer minor units (paise) end to end — no
floating-point currency math anywhere in the codebase.

`GET /api/v1/evaluation/recovery-summary` is a pure aggregation over
whatever `recovery_cases` rows exist — it has no way to know whether they
came from real Razorpay execution or from `scripts/seed_demo_batch.py`
(below); the honesty obligation is on whoever ran the batch and is
presenting the number, not on the endpoint. State which one you ran.

## Security

[`docs/security.md`](docs/security.md) — secrets/config handling, structured
logging with redaction, error responses that never leak stack traces, the
append-only audit model, and what's explicitly deferred (auth, rate
limiting) with the reasoning for deferring it.

## Local Setup

Prerequisites: Python 3.11+, Node.js 20+, and either Docker (for
PostgreSQL) or nothing else (SQLite works for local dev — see below).

### 1. Infrastructure (PostgreSQL)

```bash
docker compose up -d postgres
```

This starts Postgres on `localhost:5432` with credentials from
`docker-compose.yml` (`recoverai`/`recoverai`/`recoverai`). No Docker?
Point `DATABASE_URL` at a local SQLite file instead (see `.env.example`) —
the ORM and Alembic setup work against both; PostgreSQL remains the
source-of-truth architecture for anything beyond local development.

### 2. Backend

```bash
cd backend
python -m venv .venv
./.venv/Scripts/pip install -r requirements-dev.txt   # .venv/bin/pip on macOS/Linux
cp ../.env.example ../.env   # then edit DATABASE_URL if not using Docker Postgres defaults
./.venv/Scripts/python -m alembic upgrade head
./.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs` once running.

AI diagnosis works with **no** `GEMINI_API_KEY` set — every diagnose call
gracefully falls back to a safe `ESCALATE` recommendation instead of
crashing (see [`docs/ai-safety.md`](docs/ai-safety.md)). To exercise real
Gemini calls, get a key at https://aistudio.google.com/apikey and set
`GEMINI_API_KEY` in `.env`.

Recovery execution works with **no** `RAZORPAY_KEY_ID`/
`RAZORPAY_KEY_SECRET` set — every execute call gracefully escalates
instead of crashing (see
[`docs/razorpay-integration.md`](docs/razorpay-integration.md)). To
exercise real Razorpay Test Mode calls, create a Test Mode key pair in
the Razorpay Dashboard and set `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`/
`RAZORPAY_WEBHOOK_SECRET` in `.env`. **Never** set `RAZORPAY_MODE` to
anything but `test` in this repo — the provider refuses to construct
otherwise.

### 3. Evaluation package (separate, minimal environment)

```bash
python -m venv .venv-eval          # from repo root
./.venv-eval/Scripts/pip install -r evaluation/requirements-dev.txt
```

### 4. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local   # VITE_API_BASE_URL, defaults to http://localhost:8000
npm run dev
```

Open `http://localhost:5173`.

## Environment Variables

See [`.env.example`](.env.example) (backend/repo-root) and
[`frontend/.env.example`](frontend/.env.example). Every variable is
documented inline; no `.env` file with real values is ever committed
(enforced by `.gitignore`).

## Running the Application

With Postgres, backend, and frontend all running (see Local Setup):

- Frontend dashboard: `http://localhost:5173`
- Backend API + docs: `http://localhost:8000/docs`
- Health check: `curl http://localhost:8000/health`

## Generating Synthetic Data

```bash
# from repo root, using the evaluation venv
./.venv-eval/Scripts/python -m evaluation.generate_dataset --count 500 --seed 42
./.venv-eval/Scripts/python -m evaluation.run_evaluation --dataset evaluation/datasets/generated/dataset_500_seed42.json
```

The second command writes a report to `evaluation/reports/` (git-ignored).
`GET /api/v1/evaluation/summary` on the backend and the frontend's
Evaluation tab both surface the most recent report automatically — no
restart needed.

To compare the baseline against Gemini on a held-out subset (works without
a key too — the AI side is explicitly marked skipped rather than faked):

```bash
./.venv-eval/Scripts/python -m evaluation.run_ai_evaluation \
    --dataset evaluation/datasets/generated/dataset_500_seed42.json --limit 20
```

See [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md) for
the held-out split methodology and what this comparison does and doesn't
prove.

## Running Tests

```bash
./scripts/validate.sh   # runs everything below, from repo root
```

Or individually:

```bash
# backend
cd backend && ./.venv/Scripts/python -m pytest -q

# evaluation
./.venv-eval/Scripts/python -m pytest evaluation/tests -q

# frontend
cd frontend && npx tsc -b && npx oxlint && npm run build
```

A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the same three
suites on every push/PR. CI never requires real Razorpay (or Gemini)
credentials — every backend test that exercises execution/webhook logic
runs against `FakePaymentProvider`.

## Real Razorpay Test Mode Check (manual, not CI)

A separate, explicitly non-CI script exercises the real Razorpay Test
Mode API — creates exactly one Payment Link and fetches it back, to
demonstrate the integration actually works end to end. It is not
collected by pytest (its filename deliberately doesn't match `test_*.py`)
and requires real Test Mode credentials in `.env`:

```bash
cd backend && ./.venv/Scripts/python -m tests.integration.razorpay_live_check
```

See [`docs/razorpay-integration.md`](docs/razorpay-integration.md) for
why this is deliberately small and separate from the simulated
evaluation.

## Demo Batch (measured recovery, no credentials needed)

Without either Razorpay or Gemini credentials, `GET
/api/v1/evaluation/recovery-summary` has nothing to show on a fresh
database — every figure is honestly zero, which makes the Track 03
"measured money recovered across a batch" requirement hard to *see* in
this repository as cloned. `backend/scripts/seed_demo_batch.py` closes
that gap: it seeds 30 realistic failed-payment cases and runs every one
through the real, unmodified pipeline (`diagnose_recovery_case` →
`execute_recovery_case` → webhook processing →
`compute_recovery_summary`), substituting only the two external I/O
boundaries — `FakeAIProvider` for Gemini, `FakePaymentProvider` for
Razorpay — exactly as the automated test suite already does. It is not
the synthetic evaluation (different methodology, 500+ cases, scored
against a held-out ground truth) and it is not a real Razorpay result;
it is a deliberately mixed batch (some recovered, some expired
unpaid, some stopped by policy, some escalated) so the resulting numbers
demonstrate compliant escalation and stopping rules, not just a
suspiciously perfect 100% recovery rate.

```bash
cd backend && ./.venv/Scripts/python -m scripts.seed_demo_batch
```

Prints a full breakdown and leaves the rows in your database — reload
the frontend's Overview page or `GET /evaluation/recovery-summary`
afterward to see them. Covered by `backend/tests/test_demo_batch.py`,
including an assertion on the exact expected outcome distribution and
that a recovered case's audit trail contains the complete diagnose →
policy → execute → webhook-confirmed story.

## Implemented / Planned / Experimental

**Implemented (Phase 1 + Phase 2 + Phase 3):**
- Domain models, recovery-case state machine, deterministic policy engine —
  all unit-tested (backend: 117 tests; evaluation: 25 tests).
- Versioned API (`/health`, payments, recovery-cases, audit-events,
  evaluation summary + live recovery summary, `POST payments` (failed-
  payment ingestion), `POST recovery-cases` + `POST
  recovery-cases/discover` (open a case from a failed payment, singly or
  as a sweep), `POST recovery-cases/{id}/diagnose`, `POST
  recovery-cases/{id}/execute`, `GET recovery-cases/{id}/timeline`, `POST
  webhooks/razorpay`) with pagination, typed errors, structured logging,
  correlation IDs.
- **Failed-payment ingestion** (`backend/app/services/ingestion_service.py`):
  the workflow's entry point — a provider-reported failed payment becomes
  a `Payment` row and a `RecoveryCase` in `DISCOVERED`, idempotently
  (`RecoveryCase.payment_id` is unique), either inline on `POST /payments`
  or via a `POST /recovery-cases/discover` sweep over un-cased failed
  payments. Adds no new state transitions or policy rules; the existing
  diagnosis pipeline picks the case up unchanged. Covered by
  `backend/tests/test_ingestion_workflow.py`.
- Alembic migrations for the full schema (through Phase 3), verified by
  actually running `alembic upgrade head` against both a fresh database
  and one seeded with pre-Phase-3 data.
- Synthetic dataset generator (6 scenarios, deterministic seeding,
  reproducibility-tested) and a rule-based non-AI baseline strategy.
- Evaluation framework computing real financial/decision/safety/operational
  metrics from an actual run — surfaced live through the API and frontend.
- **AI diagnosis layer** (`backend/app/ai/`): provider abstraction (Gemini +
  deterministic fake for tests), versioned prompt, schema-validated
  structured output, retry + safe ESCALATE fallback on any failure,
  full audit trail. Every recommendation — AI or fallback — passes through
  the same deterministic policy engine before it can change a case's
  state. Never executes a recovery action. See docs/ai-architecture.md and
  docs/ai-safety.md.
- **AI-vs-baseline evaluation** (`evaluation/run_ai_evaluation.py`):
  deterministic held-out split, same metrics functions as the baseline
  report, graceful no-credentials skip (never fabricates a comparison).
- **Razorpay Test Mode execution layer** (`backend/app/payments/`):
  `PaymentProvider` abstraction, real `RazorpayPaymentProvider` (Test
  Mode only, guarded against Live Mode at construction) + a
  `FakePaymentProvider` used by every automated test, Payment Link
  creation with amount safety (always from the canonical `Payment` row),
  ambiguous-result reconciliation instead of blind retry, and a
  signature-verified, deduplicated webhook endpoint that is the *only*
  path to `RECOVERED`. Full write-up: docs/razorpay-integration.md.
- **Concurrency fix**: optimistic locking (`RecoveryCase.version`) plus a
  shared guard wrapper around both diagnose and execute, empirically
  proven with two real concurrent DB sessions racing on one case.
- **Operator dashboard** (React/TypeScript/Vite, real API integration,
  strict TypeScript, explicit empty states — never fabricated data): a
  clean-corporate-SaaS control surface built for a security/assurance
  reviewer. Five sections — Overview (live recovery posture), Recovery
  Cases (filterable, with a case-investigation drawer to ingest, diagnose,
  and execute), **Policy Decisions** (every ALLOW/BLOCK the deterministic
  gate produced, at diagnosis time and at the pre-execution re-check, with
  reason codes and policy version), **Audit Trail** (the append-only event
  stream, filterable by entity / event type / correlation ID, with inline
  payloads), and Evaluation (the synthetic-dataset run). The case drawer
  keeps the same safety posture as before: a recommendation is never shown
  as authorization, the frontend only *requests* execution, and the
  backend re-verifies everything independently.
- `docker-compose.yml` for local PostgreSQL.
- **Demo batch** (`backend/scripts/seed_demo_batch.py` +
  `backend/tests/test_demo_batch.py`): runs 30 cases through the real
  diagnose → policy → execute → webhook pipeline with FakeAIProvider/
  FakePaymentProvider standing in for Gemini/Razorpay, producing a
  genuine measured (not simulated) recovery rate, a mix of recovered/
  failed/stopped/escalated outcomes, and full audit trails — closes the
  gap where a credential-less environment could never demonstrate
  "measured money recovered across a batch" at all. See "Demo Batch"
  above.
- **Phase 4 submission hardening**: closed a structured-logging redaction
  gap (`app/core/logging.py` moved from an exact-match key list to a
  substring match, so e.g. a field named `webhook_secret` is caught, not
  just `secret`/`key_secret`), backed by a new dedicated test file
  (`test_logging_redaction.py`); added `*.db`/`*.db-journal` to
  `.gitignore` (the SQLite local-dev fallback path wasn't previously
  excluded); fixed several stale Phase-1-era UI strings (header tagline,
  an empty-state hint); added a confirmed-recovered-revenue display and a
  clearer "awaiting payment" sub-state to the case detail view; made
  failed/stopped/escalated cases visually distinct (red) from recovered
  (green) and in-progress (blue) in the case list.

**Planned (later phases):**
- A `RETRY_PAYMENT` executor — deliberately not implemented, since
  Razorpay has no generic "retry a failed payment" endpoint; see
  docs/razorpay-integration.md "Reality check."
- Automated notification (SMS/email) as part of the execution flow, vs.
  the provider interface method that exists but isn't auto-invoked yet.
- Authentication/authorization on the API.
- A polished dashboard (still intentionally a foundation, not a finished
  product).
- Checkout abandonment, subscription recovery, receivables (explicitly
  out of this project's scope per the Track description above).

**Experimental / not started:** none currently claimed — anything not
listed above as implemented is simply not built yet. See "Known
Limitations" immediately below for the full, honest list, including what
was and wasn't actually run with real credentials.

## Known Limitations

Stated plainly, not hidden:

- **No real Razorpay or Gemini credentials were available in any build
  environment, at any phase.** Nothing in this repository claims
  otherwise — every "recovered revenue" or "AI accuracy" figure is either
  a synthetic-evaluation result or a `FakePaymentProvider`/fallback-path
  test result. `backend/tests/integration/razorpay_live_check.py` and
  `evaluation/run_ai_evaluation.py` are both real, ready to run, and both
  unrun here.
- **`RETRY_PAYMENT` has no real-provider implementation** — Razorpay has
  no generic "retry a failed payment" endpoint, so a case recommending it
  is escalated for human handling rather than a fabricated endpoint being
  invented. See `docs/razorpay-integration.md` "Reality check."
- **No authentication/authorization on the API** — not intended to be
  internet-facing as shipped; called out explicitly rather than left as
  an unstated gap. See `docs/security.md`.
- **No automatic retry of a failed execution or automatic customer
  notification** — every execution failure escalates to a human by
  design (see "Failure Handling" above); the notify-by-SMS/email provider
  method exists but isn't wired into the automated flow.
- **The synthetic evaluation's ground truth and baseline strategy share
  authorship logic** — it's a decision-quality/safety regression harness,
  not proof of real-world accuracy. See
  `docs/evaluation-methodology.md` "Known limitations."
- **Scope is deliberately narrow**: failed-payment recovery only.
  Checkout abandonment, subscription recovery, and receivables were
  considered for this submission (see Project Roadmap) and deliberately
  not added, to keep the one implemented workflow reliable and fully
  tested rather than adding a second, thinner one.

## Project Roadmap

1. **Phase 1 — Foundation.** Domain model, policy engine, state machine,
   API, evaluation framework, frontend shell.
2. **Phase 2 — AI diagnosis & recommendation.** Gemini-backed diagnosis
   behind a provider abstraction, gated entirely through the existing
   policy engine; frontend investigation view; AI-vs-baseline evaluation.
3. **Phase 3 — Razorpay Test Mode integration & bounded execution.**
   `PaymentProvider` implementation against current Razorpay Test Mode
   docs; execution pipeline (`APPROVED → EXECUTING →
   RECOVERED`/`FAILED`) via Payment Links; signature-verified webhooks;
   the Phase 2 concurrency gap fixed; frontend execution/timeline view.
4. **Phase 4 (this phase) — Submission hardening + Track 03 audit.**
   Reviewed the entire system end to end for a security/reliability/
   documentation pass (see "Submission Status" above); then audited
   specifically against the Track 03 bar and closed the one real gap
   found — a credential-less environment couldn't demonstrate a measured
   batch recovery at all — with `scripts/seed_demo_batch.py` (see "Demo
   Batch"); explicitly considered and declined adding a second recovery
   scenario (checkout abandonment, subscriptions, or a larger real Test
   Mode batch) for this submission, to keep the one implemented workflow
   fully tested and reliable rather than adding a second, thinner one
   (see "Known Limitations").
5. **Phase 5 — Ingestion entry point + operator dashboard rebuild.**
   Implemented the workflow's missing left edge: `POST /payments`
   (failed-payment ingestion) and `POST /recovery-cases` /
   `POST /recovery-cases/discover` to open a recovery case from a failed
   payment — idempotent, no new state transitions, existing pipeline
   unchanged (`backend/tests/test_ingestion_workflow.py`). Rebuilt the
   frontend as a clean-corporate-SaaS operator dashboard for a
   security/assurance audience, with **Policy Decisions** and **Audit
   Trail** as first-class sections and a case-investigation drawer.
6. **Future — pick at most one at a time.** Checkout abandonment
   recovery, subscription recovery, or a larger controlled real Test
   Mode batch (bounded by Razorpay's 30-Payment-Links-per-business Test
   Mode limit) for a measured real-revenue demo; auth; CI hardening;
   pitch materials (created separately, not committed to this repo).
