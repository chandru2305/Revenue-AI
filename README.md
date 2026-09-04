# RecoverAI

## Problem

Failed payments quietly leak revenue. Most of that money is recoverable —
but only if someone diagnoses *why* the payment failed, picks the right
recovery action, and does it within safe, auditable bounds. Most teams
either do this manually (slow, inconsistent) or not at all (money left on
the table).

## Solution

RecoverAI is a bounded, auditable revenue-recovery pipeline. It identifies
at-risk failed payments, analyzes the available payment and customer
context, determines an appropriate recovery strategy, validates that
strategy against deterministic safety policies, executes only permitted
actions, and records every decision and outcome for measurement and audit.

The core design principle: **AI reasons. Deterministic systems enforce.**
An AI layer (Groq, `openai/gpt-oss-120b`) diagnoses and recommends — but
retry limits, recovery windows, contact caps, amount validation, and
action eligibility are enforced by a pure, dependency-free policy engine
([`backend/app/domain/policy.py`](backend/app/domain/policy.py)) that no
LLM can bypass. See [`docs/architecture.md`](docs/architecture.md),
[`docs/ai-safety.md`](docs/ai-safety.md), and
[`docs/recovery-state-machine.md`](docs/recovery-state-machine.md) for how
this is enforced structurally, not just by convention.

**On the word "agentic":** the *system* runs an autonomous
sense → decide → act → observe loop
([`orchestrator_service.py`](backend/app/services/orchestrator_service.py)):
it discovers at-risk revenue, diagnoses it, decides, and — when permitted
— acts, on a schedule, without a human in the loop. **The LLM is not the
agent.** It makes a single structured inference per case; it has no tools
and no authority to act. The agency lives in the orchestration, not in
the model.

That split is the design, not a shortfall. Giving the model tools that
create payment links or contact customers would move enforcement inside
the model, which is exactly what the policy engine exists to prevent. So
the loop is autonomous and the *authority* is deterministic — and
execution is opt-in (`ORCHESTRATOR_AUTO_EXECUTE`, default off) so the
system reasons continuously but only moves money when told it may.

### Why AI, specifically — stated as a hypothesis, not a result

A pure rule-based system (the `evaluation/baseline` strategy, also
shipped) applies fixed thresholds: retry count, days elapsed, contact
count. The hypothesis behind adding an LLM is that it can *read the
specific case* — that "insufficient funds" on attempt 1 warrants a
different action than the same decline on attempt 4 with two prior
contacts, and that the gap widens on messy free-text failure reasons.

**That hypothesis is not demonstrated in this repository, and this
section deliberately does not claim otherwise.** A real head-to-head has
now been run (Groq, `openai/gpt-oss-120b`, 30 clean held-out cases): the
LLM scored **worse than the rule-based baseline on every metric** — see
[AI evaluation](#ai-evaluation-what-was-actually-run) below. Two things
bound that result:

- The synthetic dataset's ground truth was authored from the same
  heuristics the baseline implements, so the comparison measures "does
  the LLM reproduce our rules", not "does the LLM recover more money".
- The dataset encodes `failure_reason` as a fixed enum, so it cannot
  exercise the "messy free-text" case the hypothesis actually leans on.

The honest position: on the evidence available, the deterministic
baseline is the better recommender, and the LLM's value — if any — is
unproven and would need a dataset with realistic decline strings to
test.

What *is* established: the AI's job is narrowly the judgment call —
diagnosis category, a recovery action recommendation, and a confidence
score — never the money-moving decision itself. Every recommendation,
AI-sourced or a safe fallback, passes through the same policy engine
before anything happens; see "Core workflow" below and
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

## Against the Track 03 bar

> *"Don't just identify the problem. Show measured money recovered across
> a batch, with compliant escalation, stopping rules, and an audit trail."*

| Bar item | Where it is |
|---|---|
| **Measured money recovered across a batch** | `POST /api/v1/demo/seed-batch` (or the dashboard's **Seed demo batch (dev)** button) runs 30 cases through the real pipeline — **live AI diagnosis**, real policy engine, real aggregation — and returns the recovery rate. ⚠️ The Razorpay write + webhook confirmation are **simulated** (Test Mode not configured); the recovered figure is a real measurement over real rows whose payment confirmation was faked. See "Recovery Batch" below. |
| **Compliant escalation** | Policy `BLOCK` → `STOPPED`/`ESCALATED`; AI unavailable → safe `ESCALATE`; provider failure → `ESCALATE`; ambiguous provider result → reconcile-or-escalate; permitted-but-unexecutable action → `ESCALATE` with the reason recorded. Live `escalation_rate`. |
| **Stopping rules** | Retry cap, recovery window, contact cap, confidence threshold (higher for high-value), amount bounded both ways, terminal-state protection — all in [`policy.py`](backend/app/domain/policy.py), which no LLM can bypass. `STOP`/`ESCALATE` are never blocked by a stale window, so a case can always be halted. Live `stop_rate`. |
| **Audit trail** | Append-only `AuditEvent`, single writer, correlation-ID keyed, every transition — including the autonomous cycle itself, recorded as a machine actor. `GET /recovery-cases/{id}/timeline` replays the full diagnose → policy → execute → webhook story. Dedicated **Audit Trail** and **Policy Decisions** dashboard views. |
| **An agent** | An autonomous discover → diagnose → decide → act loop ([`orchestrator_service.py`](backend/app/services/orchestrator_service.py)), runnable on a schedule or on demand. The *loop* is the agent; the LLM is one bounded inference inside it — see "On the word agentic" above. |

**The one honest gap:** the batch measurement runs the real, unmodified
pipeline with **live AI diagnosis** (real `openai/gpt-oss-120b` calls when
`GROQ_API_KEY` is set), but `FakePaymentProvider` stands in for the one
external write and the confirmation webhook, because **no Razorpay Test
Mode credentials are available** — that stand-in is the only way to reach
`RECOVERED` without a live gateway. The recovery rate is a real
measurement over real database rows; the payment that confirms a recovery
is simulated, and the API response says so in a mandatory `provenance`
field. `razorpay_live_check.py` is written and ready for whoever has
credentials.

## Track

Razorpay Buildathon — Track 03: AI Revenue Recovery.

Scope for this build is intentionally narrow: **failed payment recovery**
only (payment failure → root-cause diagnosis → recovery decision → bounded
recovery action → result → stop/escalate → audit). Checkout abandonment,
subscription recovery, and receivables are explicitly out of scope.

## Submission Status

**Feature-complete through Phase 8.** The per-phase narrative below
records what each phase actually added; the
[Project Roadmap](#project-roadmap) at the end of this file is the
authoritative list. Phases 5–8 (failed-payment ingestion, the operator
dashboard rebuild, Docker, API-key authentication, a six-defect
correctness pass, the first live Groq runs and the Gemini→Groq migration,
and the autonomous recovery loop + measured-batch endpoint) are
summarised there rather than repeated here.

A follow-up pass since Phase 8 rebuilt the dashboard's first screen as an
**autonomous-agent Command Center** (the `discover → diagnose → decide →
act → observe` pipeline, a live audit-event activity feed, an AI→policy
comparison in the case drawer, a dynamic **DEMO MODE** indicator), added
two read-only endpoints for it (`GET /api/v1/orchestrator/status`, `GET
/api/v1/system/info`), and made `POST /api/v1/payments` idempotent on
`provider_payment_id` so a re-delivered ingestion event returns the
original payment instead of creating a duplicate. Backend: 224 tests.

Phase 1 established the domain model, deterministic policy engine, state
machine, API, database, synthetic evaluation framework, and frontend
shell. Phase 2 added a real Groq-backed diagnosis layer
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
"Recovery Batch" below). None of this touched the working architecture. See
[Implemented / Planned / Experimental](#implemented--planned--experimental)
below for the precise line, and
[`docs/razorpay-integration.md`](docs/razorpay-integration.md) for the
full Razorpay integration write-up — including the explicit statement:
**a Payment Link being created is not counted as recovered revenue;
revenue is counted only after provider-confirmed successful payment.**

**A real Groq API key was supplied on 1 Sep 2026 and the AI path was
run against the live API** — see [AI evaluation: what was actually
run](#ai-evaluation-what-was-actually-run) for the results, which include
two real defects it exposed and an explicitly *inconclusive*
decision-quality comparison. **Razorpay credentials remain
unavailable**, so every "recovered revenue" figure in this repository is
still either a synthetic-evaluation figure or a
`FakePaymentProvider`-backed test result — never a real Razorpay result
presented as one. See "Known Limitations" below.

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
non-AI baseline strategy, a Groq-backed strategy scored on a deterministic
held-out split, and financial/decision/safety/operational metrics computed
from an actual run — never fabricated. Includes an honest limitations
section (ground truth and baseline share domain heuristics by construction;
"recovered revenue" is a simulated proxy since there's no live gateway
yet). Full methodology:
[`docs/evaluation-methodology.md`](docs/evaluation-methodology.md).

## AI evaluation: what was actually run

A real Groq API key was supplied on 1 Sep 2026 and the AI path was
executed against a live API for the first time. What that produced,
stated plainly:

**The integration works.** Real calls return valid structured output with
case-grounded reasoning — e.g. for a first-attempt `network_error` on a
customer with an 0.88 historical success rate, `retry_payment` at 0.9
confidence, rationale citing exactly those two facts.

**It exposed real defects that no fake provider could have caught**, each
of which the safe-fallback path absorbed exactly as designed — a recorded
`ESCALATE`, no crash, no invented recommendation:

| Defect | Detail |
|---|---|
| `json_object` prompt constraint | Groq rejects `response_format={"type":"json_object"}` with a `400` unless the literal word "json" appears in the messages. The versioned prompt didn't contain it. `GroqProvider` now appends an output-format directive rather than editing a prompt that is pinned to the audit trail. |
| Dead model pin (pre-migration) | The project previously ran on Gemini, whose pinned model had been retired (`404 — no longer available to new users`). Found only by calling the real API. |
| Timeout below real latency | The 20s ceiling was under observed latency and was converting good answers into fallback `ESCALATE`s. Raised to 45s — generous headroom against Groq's ~1.3s mean, because a timeout that trips on ordinary latency protects nothing. |

### The comparison: a clean run, and a negative result

Groq (`openai/gpt-oss-120b`) ran 30 held-out cases with **0 provider
failures, 30/30 real answers**, ~1.3s mean latency:

| Metric (held-out, n=30) | Rule-based baseline | Groq |
|---|---|---|
| Intervention accuracy | 0.87 | **0.47** |
| Appropriate escalation rate | 0.50 | 0.25 |
| Inappropriate intervention rate | 0.00 | **0.63** |
| Policy violations | 5 | 11 |
| Recovery rate (simulated) | 1.00 | 0.71 |

**On this benchmark the LLM is clearly worse than the rules it was meant
to augment.** Stated without spin. But two things bound what that number
means:

1. **The benchmark is structurally unfavourable to any LLM.** Ground
   truth was authored from the same heuristics the baseline implements,
   so "accuracy" here measures *"does the model reproduce our rules"*,
   not *"does the model recover more money"*. The baseline scores 0.87
   against a rubric derived from itself; a model reasoning from first
   principles is penalised for every defensible disagreement.
2. **The prompt withholds the thresholds that define "correct".** The
   eval prompt never tells the model the retry cap is 3, the window 14
   days, the contact cap 2 — but those numbers *are* the ground truth.
   The baseline is those numbers.

So the honest reading: the harness now works, it is real and
reproducible, and it says **do not ship this LLM setup against these
rules as-is.** It cannot yet test the case the LLM was actually added
for — messy free-text failure reasons — because the synthetic dataset
encodes `failure_reason` as a fixed enum. Full write-up and the raw
reports: [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md).

**A reporting weakness this exposed, now fixed:**
`evaluation/run_ai_evaluation.py` previously labelled a 100%-failed run
`"status": "ok"`. It now degrades the status
(`degraded_majority_calls_failed` / `unusable_all_calls_failed`), prints
a warning above the table, and records `failure_reasons` and
`real_answers` in the report so a contaminated run can never be mistaken
for a finding.

## AI Architecture & Safety

How the provider abstraction is built (Groq via the `groq` SDK) and what
was verified live against the real API:
[`docs/ai-architecture.md`](docs/ai-architecture.md).
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
- **AI provider unavailable or returns malformed output** — falls back to a
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

## Run the whole stack with Docker

The fastest path — PostgreSQL, the FastAPI backend (migrated automatically),
and the frontend, one command:

```bash
make up
```

Then open:

| | |
|---|---|
| Frontend | http://localhost:8080 |
| API + Swagger docs | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |

`make up` runs in the foreground (Ctrl-C to stop). `make up-d` runs
detached. Other targets: `make down` (stop, keep the DB volume),
`make logs`, `make ps`, `make seed` (run the recovery batch),
`make test` (backend suite in a container), `make clean` (stop + delete
the DB volume), `make help` (list them all).

Requires **Docker** (with the Compose v2 plugin — Docker Desktop includes
it) and **GNU Make**. On Windows: `winget install ezwinports.make` for
Make, and make sure Docker Desktop is running. No `make`? The equivalent
is `docker compose up --build`.

Razorpay Test Mode and Groq keys are optional — without them the app
degrades gracefully (execute escalates, diagnose falls back to
`ESCALATE`). To use them, put them in a repo-root `.env` and `docker
compose` picks them up:

```bash
GROQ_API_KEY=...
RAZORPAY_KEY_ID=...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
```

API authentication is off by default, so the stack runs with no
configuration. To turn it on, set `API_KEY` in that same repo-root `.env`
— compose passes it to the backend and bakes the matching `VITE_API_KEY`
into the frontend bundle:

```bash
API_KEY=pick-a-long-random-string
```

Because the frontend key is a *build* argument, changing it needs
`make rebuild`, not just `make down && make up`. See
[`docs/security.md`](docs/security.md) for the full posture — including
why that bundled key is a deployment gate, not a secret.

The frontend container's nginx reverse-proxies `/api` and `/health` to
the backend, so the browser talks to a single origin (no CORS in the
container setup). Files: [`Makefile`](Makefile),
[`docker-compose.yml`](docker-compose.yml),
[`backend/Dockerfile`](backend/Dockerfile),
[`frontend/Dockerfile`](frontend/Dockerfile) +
[`frontend/nginx.conf`](frontend/nginx.conf).

## Local Setup (without Docker)

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

AI diagnosis works with **no** `GROQ_API_KEY` set — every diagnose call
gracefully falls back to a safe `ESCALATE` recommendation instead of
crashing (see [`docs/ai-safety.md`](docs/ai-safety.md)). To exercise real
Groq calls, get a key at https://console.groq.com/keys and set
`GROQ_API_KEY` in `.env`.

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

To compare the baseline against the LLM on a held-out subset (works without
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
suites on every push/PR. CI never requires real Razorpay (or Groq)
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

## Recovery Batch (measured recovery on a fresh database)

On a fresh database `GET /api/v1/evaluation/recovery-summary` has nothing
to show — every figure is honestly zero — which makes the Track 03
"measured money recovered across a batch" requirement hard to *see* in
this repository as cloned. `backend/scripts/seed_demo_batch.py` (and
`POST /api/v1/demo/seed-batch`) closes that gap: it seeds a curated
spread of 30 realistic failed-payment cases and runs every one through
the real pipeline — `diagnose_recovery_case` → `evaluate_policy` →
`execute_recovery_case` → webhook processing → `compute_recovery_summary`.

**What is real:** the case inputs, the **AI diagnosis** (`run_demo_batch`
calls the live `get_ai_service` — with `GROQ_API_KEY` set, every case
gets a real `openai/gpt-oss-120b` diagnosis; with no key, the safe
ESCALATE fallback), the deterministic policy engine, the state machine,
the append-only audit trail, and the recovery-rate arithmetic.

**What is simulated, because it has to be:** the one external write
(`create_payment_link`) and the `payment_link.paid`/`.expired` webhooks
are served by `FakePaymentProvider` and hand-built payloads fed through
the real `parse_event` + `webhook_service` path. Razorpay Test Mode is
not configured here, and this is the only way to reach `RECOVERED`
without a live gateway. **The recovered-revenue figure's payment
confirmation is simulated — never quote it as a Razorpay Test Mode
result.** The response and CLI output both carry this provenance.

It is a deliberately mixed batch — some recovered, some expired unpaid,
some stopped by policy, some escalated — so the numbers demonstrate
compliant escalation and stopping rules, not a suspiciously perfect 100%.

```bash
cd backend && ./.venv/Scripts/python -m scripts.seed_demo_batch
```

Covered by `backend/tests/test_demo_batch.py` and `test_demo_api.py`,
which inject a deterministic scripted AI stand-in so the outcome
distribution is reproducible in CI (the endpoint and CLI use the live
provider), plus a test that the no-key path escalates every case safely.

## Implemented / Planned / Experimental

**Implemented (Phase 1 through Phase 8 + the agent-visibility pass):**
- Domain models, recovery-case state machine, deterministic policy engine —
  all unit-tested (backend: 224 tests; evaluation: 25 tests).
- Versioned API (`/health`, payments, recovery-cases, audit-events,
  evaluation summary + live recovery summary, `POST payments` (failed-
  payment ingestion, idempotent on `provider_payment_id`), `POST
  recovery-cases` + `POST recovery-cases/discover` (open a case from a
  failed payment, singly or as a sweep), `POST
  recovery-cases/{id}/diagnose`, `POST recovery-cases/{id}/execute`, `GET
  recovery-cases/{id}/timeline`, `POST orchestrator/cycle` (one autonomous
  pass), `GET orchestrator/status` (agent snapshot from the audit trail),
  `GET system/info` (provider modes + policy limits, read-only), `POST
  demo/seed-batch` (measured batch, non-production only), `POST
  webhooks/razorpay`) with pagination, typed errors, structured logging,
  correlation IDs.
- **Autonomous recovery loop**
  (`backend/app/services/orchestrator_service.py` + `orchestrator_runner.py`):
  discover → diagnose → (optionally) execute, on a schedule or on demand.
  Adds no decision-making of its own — every step goes through the same
  state machine, policy engine, and audit trail. Three bounds make it safe
  to leave running: execution is opt-in and **off by default**, per-cycle
  budgets cap discovery/diagnosis/execution, and one failing case never
  aborts the pass. The cycle records itself in the audit trail as a
  machine actor, and drains the approved backlog left by earlier cycles.
- **Failed-payment ingestion** (`backend/app/services/ingestion_service.py`):
  the workflow's entry point — a provider-reported failed payment becomes
  a `Payment` row and a `RecoveryCase` in `DISCOVERED`, either inline on
  `POST /payments` or via a `POST /recovery-cases/discover` sweep over
  un-cased failed payments. Idempotent at both levels: `POST /payments`
  with a `provider_payment_id` that has already been ingested returns the
  original payment and case (recording a `payment_ingest_deduplicated`
  audit event, creating nothing), and case creation is idempotent per
  payment (`RecoveryCase.payment_id` is unique) — the same contract the
  webhook path enforces via `ProcessedWebhookEvent`. Adds no new state
  transitions or policy rules; the existing diagnosis pipeline picks the
  case up unchanged. Covered by
  `backend/tests/test_ingestion_workflow.py`.
- Alembic migrations for the full schema (through Phase 3), verified by
  actually running `alembic upgrade head` against both a fresh database
  and one seeded with pre-Phase-3 data.
- Synthetic dataset generator (6 scenarios, deterministic seeding,
  reproducibility-tested) and a rule-based non-AI baseline strategy.
- Evaluation framework computing real financial/decision/safety/operational
  metrics from an actual run — surfaced live through the API and frontend.
- **AI diagnosis layer** (`backend/app/ai/`): provider abstraction (Groq +
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
  financial-operations command center built for a security/assurance
  reviewer, not a generic CRUD dashboard. Five sections:
  - **Command Center** — leads with an autonomous-agent panel:
    agent status (running / idle / error), cycle count, the last cycle's
    counts, and a five-stage `discover → diagnose → decide → act →
    observe` pipeline whose per-stage state is derived from recent audit
    events; a "Start recovery cycle" button (`POST /orchestrator/cycle`,
    honouring `ORCHESTRATOR_AUTO_EXECUTE`); a live activity feed off
    `/audit-events` with AI / POLICY / EXEC / WEBHOOK lane tags; and an
    executive metrics strip (all from live DB state).
  - **Recovery Cases** — filterable, with a case-investigation drawer:
    Payment, AI diagnosis, **Policy decision** (a visual AI-recommendation
    → per-check ✓/✕ → recorded ALLOW/BLOCK ladder, checked against the
    real limits from `/system/info`), Execution, Verification, and a
    stage-labelled audit timeline.
  - **Policy Decisions** — every ALLOW/BLOCK the deterministic gate
    produced, at diagnosis time and at the pre-execution re-check, with
    reason codes and policy version.
  - **Audit Trail** — the append-only event stream, filterable by entity /
    event type / correlation ID, with inline payloads.
  - **Evaluation** — the synthetic-dataset run (separate methodology).

  A top-bar **DEMO MODE — payment simulated** pill (from `/system/info`)
  makes the Razorpay-credential gap explicit, and switches to the real
  gateway mode automatically if a key is configured. The case drawer keeps
  the same safety posture: a recommendation is never shown as
  authorization, the frontend only *requests* execution, and the backend
  re-verifies everything independently.
- `docker-compose.yml` for local PostgreSQL.
- **Recovery batch** (`backend/scripts/seed_demo_batch.py`,
  `POST /api/v1/demo/seed-batch`): 30 curated cases through the real
  diagnose → policy → execute → webhook pipeline. AI diagnosis is **live**
  (the endpoint and CLI call `get_ai_service`; tests inject a scripted
  stand-in for reproducibility); only the Razorpay write + webhook
  signature layer is simulated by `FakePaymentProvider`, because Test Mode
  isn't configured here. Produces a genuinely measured recovery rate over
  real rows and a mix of recovered/failed/stopped/escalated outcomes with
  full audit trails. See "Recovery Batch" above.
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

- **No real Razorpay credentials have been available in any build
  environment.** Every "recovered revenue" figure is a
  synthetic-evaluation result or a `FakePaymentProvider`/fallback-path
  test result. `backend/tests/integration/razorpay_live_check.py` is
  real, ready to run, and still unrun.
- **The AI side has been run for real, on a small sample.** 30 held-out
  cases against a live LLM — enough to prove the integration works and
  to expose a real defect, but *not* enough to claim a decision-quality
  result. See [AI evaluation](#ai-evaluation-what-was-actually-run).
- **The synthetic evaluation's ground truth and the baseline strategy
  share authorship**, which structurally disadvantages the AI in any
  head-to-head: the baseline is scored against a rubric derived from the
  same heuristics it implements, so the comparison measures "does the AI
  reproduce our rules" rather than "does the AI recover more money."
  This was already documented in `docs/evaluation-methodology.md`; the
  live run makes it matter, because the comparison is now real.
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
2. **Phase 2 — AI diagnosis & recommendation.** Groq-backed diagnosis
   behind a provider abstraction, gated entirely through the existing
   policy engine; frontend investigation view; AI-vs-baseline evaluation.
3. **Phase 3 — Razorpay Test Mode integration & bounded execution.**
   `PaymentProvider` implementation against current Razorpay Test Mode
   docs; execution pipeline (`APPROVED → EXECUTING →
   RECOVERED`/`FAILED`) via Payment Links; signature-verified webhooks;
   the Phase 2 concurrency gap fixed; frontend execution/timeline view.
4. **Phase 4 — Submission hardening + Track 03 audit.**
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
6. **Phase 6 — Correctness, security & hardening pass.** Added API-key
   authentication, and fixed six defects found in a self-review, each
   with regression tests (backend 117 → 166 tests):
   - **API authentication.** Every `/api/v1` endpoint now requires a
     shared `X-API-Key`, compared in constant time, with identical `401`
     responses for a missing and a wrong key. An unset key leaves auth
     unenforced (with a startup warning) so local dev and `make up` stay
     zero-config — but an unset key under `APP_ENV=production` makes the
     app **refuse to start**, mirroring the `RAZORPAY_MODE` guard.
     `/health` and the Razorpay webhook are deliberately exempt (an
     infrastructure probe, and a path that authenticates by HMAC
     signature instead). See docs/security.md "API authentication",
     including why the frontend's bundled copy of the key is a
     deployment gate rather than a secret.
   - **Policy amount ceiling.** `policy.py` bounded amounts only from
     below (`<= 0`), so a corrupted or mis-ingested `Payment.amount`
     could pass on AI confidence alone. Added a configurable
     `max_recovery_amount` (`POLICY_MAX_RECOVERY_AMOUNT`), reusing the
     existing `AMOUNT_OUT_OF_BOUNDS` reason code.
   - **Silently un-configurable thresholds.** `get_policy_config` built
     `PolicyConfig` from only four of its six fields, so
     `high_value_amount_threshold` and
     `high_value_min_confidence_threshold` ignored any environment
     override. All fields are now wired, and
     `tests/test_policy_service.py` enumerates `PolicyConfig` at runtime
     so the next added field cannot repeat the omission.
   - **Webhook claim ordering.** `ProcessedWebhookEvent` was inserted
     *after* the case transition and `recovered_amount` increment, so
     losing the insert race rolled back already-applied state while still
     returning `"processed"`. The claim now happens first
     (`webhook_service._claim_event`), bounding any rollback to that one
     INSERT.
   - **Weak webhook dedup key.** Now prefers Razorpay's stable
     `X-Razorpay-Event-Id` header over a payload-derived key, falling
     back to the old derivation when the header is absent.
   - **Leaked HTTP client.** The Razorpay provider's pooled
     `httpx.AsyncClient` was never closed; `PaymentProvider.aclose()` is
     now part of the contract and runs from the FastAPI lifespan
     teardown.
   - **`APPROVED` cases that could not execute.** Policy permits
     `RETRY_PAYMENT` / `SEND_REMINDER`, but no executor implements them,
     so such cases reached `APPROVED` and offered an Execute action that
     could only fail. `EXECUTABLE_ACTIONS` now lives in the domain layer
     and diagnosis routes non-executable actions to `ESCALATED` with the
     real reason recorded.

   Also hardened: inbound `X-Correlation-Id` is length- and
   charset-validated before reaching the logs (closing a log-forging
   vector), CORS methods/headers are enumerated instead of wildcarded,
   and CI gained a `migrations` job that runs `alembic upgrade head`,
   `alembic check` (model/migration drift), and a downgrade/upgrade
   round-trip against a real PostgreSQL service container — the test
   suite builds its schema with `create_all`, so nothing previously
   exercised the migrations at all.
7. **Phase 7 — First live AI runs, and the move to Groq.** The AI path
   ran against a live API for the first time (Razorpay credentials remain
   unavailable). This confirmed the integration works, exposed real
   defects a fake provider could never surface (a retired model pin, a
   timeout below observed latency, Groq's `json_object` prompt
   constraint), and validated the safe-fallback path under genuine
   provider failures. The project had been running on Google Gemini;
   after its free-tier quota made a clean comparison impossible, it was
   **migrated to Groq** (`openai/gpt-oss-120b`) — ~7× lower latency and
   workable limits. The migration touched only the provider file, the
   dependency wiring, and config, which is the `AIProvider` abstraction
   doing its job. First clean head-to-head (30 held-out cases, 0
   failures): **the LLM scored worse than the rule-based baseline on
   every metric**, on a benchmark whose ground truth is authored from the
   baseline's own heuristics. Full write-up:
   [AI evaluation](#ai-evaluation-what-was-actually-run).
8. **Phase 8 — Closing the loop (Track 03 gaps).** Added the autonomous
   recovery cycle (`orchestrator_service` + a background runner, both
   bounded and opt-in for execution) so the system runs itself rather
   than waiting on an operator's button — this is what makes "agent"
   accurate. Exposed the measured batch as `POST /demo/seed-batch` with a
   mandatory provenance disclaimer, and surfaced both from the dashboard,
   so "measured money recovered across a batch" is demonstrable by anyone
   who can open the UI. A live run caught a real design bug: cases left
   `APPROVED` by an earlier cycle were never picked up again (`APPROVED`
   isn't a diagnosable status), which made enabling auto-execute
   retroactively do nothing — fixed with an explicit backlog pass, with
   regression tests. Backend 166 → 209 tests. **Still open:** no real
   Razorpay money has moved.
9. **Agent-visibility pass (post-Phase 8).** Rebuilt the dashboard's first
   screen as an autonomous-agent **Command Center** — the `discover →
   diagnose → decide → act → observe` pipeline with per-stage state from
   real audit events, a live activity feed, an AI→policy comparison ladder
   in the case drawer, and a dynamic **DEMO MODE** indicator. Added two
   read-only endpoints to back it (`GET /orchestrator/status`, `GET
   /system/info`) and made `POST /payments` idempotent on
   `provider_payment_id`. Verified from an empty database: no seed data on
   any startup path; one ingested event flows through live Groq → policy →
   decision → audit → dashboard. Backend 209 → 224 tests. STOP-cycle
   control was **not** added — a single operator cycle is one
   uninterruptible request and the backend has no cancellation; the UI
   states this rather than faking it.
10. **Future — pick at most one at a time.** Checkout abandonment
   recovery, subscription recovery, or a larger controlled real Test
   Mode batch (bounded by Razorpay's 30-Payment-Links-per-business Test
   Mode limit) for a measured real-revenue demo; auth; CI hardening;
   pitch materials (created separately, not committed to this repo).
