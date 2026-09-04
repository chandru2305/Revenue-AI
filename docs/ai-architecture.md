# AI Architecture

## Why AI is used, and what it's not trusted to do

Diagnosing *why* a payment failed and picking a recovery action from five
options is exactly the kind of judgment call an LLM is good at and a fixed
if/else tree is bad at (see the baseline's blunt rules in
`evaluation/baseline/rule_based.py` for the alternative). But the
consequences of getting it wrong — retrying a payment that will never
succeed, contacting a customer too many times, quietly giving up on a
recoverable case — are exactly the kind of thing that must never depend on
a model having a good day.

So the AI's authority is narrow and structural, not a convention someone
could forget to follow:

- It can populate `diagnosis_category`, `recovery_confidence`,
  `recommended_action`, `decision_explanation` — nothing else.
- It cannot write a `RecoveryCaseStatus` transition, cannot construct a
  `PolicyDecision`, cannot touch `RecoveryAttempt`, cannot call a payment
  provider. Those types aren't even importable from `app/ai/`.
- Every recommendation — AI-sourced or fallback-sourced — passes through
  `app.domain.policy.evaluate_policy` before it affects a case's state.
  There is no code path that skips this. See docs/ai-safety.md.

## Provider abstraction

```
app/ai/
  context.py         PaymentRecoveryContext + build_context() — no I/O
  schemas.py          RecoveryRecommendation — the only shape a provider may return
  prompts/
    diagnosis_v1.py    versioned system prompt + input formatter
  providers/
    base.py            AIProvider interface + AIProviderError hierarchy
    groq.py             GroqProvider (groq SDK, OpenAI-compatible chat API)
    fake.py             FakeAIProvider — deterministic, used in every test
  service.py           AIRecommendationService — retry + safe fallback
  dependencies.py      FastAPI wiring (get_ai_provider / get_ai_service)
```

`AIProvider.diagnose_payment(context) -> RecoveryRecommendation` is the
entire interface. `GroqProvider` and `FakeAIProvider` implement it;
`app.services.diagnosis_service` and `AIRecommendationService` never know
or care which one they're talking to.

**The abstraction is load-bearing, not decorative.** `FakeAIProvider`
depends on it for every test, and swapping vendors means adding one file
in `providers/` and changing `dependencies.get_ai_provider` — nothing in
the services or API layer moves. That was demonstrated in practice: this
project previously ran on Google Gemini, and the migration to Groq
touched only the provider file, the dependency wiring, and config.

`GROQ_API_KEY` must be set or the diagnose endpoint degrades to the safe
ESCALATE fallback — an unconfigured deployment still works, it just never
automates.

## Groq integration — what was actually verified

Verified against the live API on 1 Sep 2026 with a real key, not assumed
from training knowledge:

- Package: `groq==0.33.0`.
- Call surface: OpenAI-compatible chat completions —
  `client.chat.completions.create(model=..., messages=[...],
  response_format=..., temperature=..., timeout=...)`. The async client
  (`AsyncGroq`) exposes it as a real coroutine function, verified by
  introspection against the installed package.
- Structured output: `response_format={"type": "json_object"}`. This
  guarantees *valid JSON* but does **not** enforce a schema, so
  `RecoveryRecommendation.model_validate_json` in the provider is the only
  thing standing between model output and the domain — a bad enum value or
  an out-of-range confidence is rejected there, not downstream.
- **Groq requires the literal word "json" somewhere in the messages** when
  `json_object` is used, or the request is rejected with a 400. The
  versioned prompt (`diagnosis_v1`) predates Groq and must not be edited
  in place — its wording is pinned to the audit trail — so `GroqProvider`
  appends a short output-format directive itself. Found by a live call,
  not by reading docs.
- Model: `openai/gpt-oss-120b`, pinned. **Not a floating alias**: the
  model name is written into every AI-sourced audit event, so a moving
  target would make a recorded decision unreproducible after the fact. For
  an auditable financial system that trade-off runs the other way than
  usual. Configurable via `GROQ_MODEL`.

  Candidates were compared on the real API across a spread of cases
  (transient failure, repeated failure, expired card, stale high-value)
  before pinning — all returned valid, *varied*, case-appropriate
  recommendations rather than one canned answer:

  | Model | Result |
  |---|---|
  | **`openai/gpt-oss-120b`** | **OK** — most nuanced, ~0.5s server time |
  | `openai/gpt-oss-20b` | OK — faster, slightly coarser |
  | `qwen/qwen3.8-27b` | OK |
  | `qwen/qwen3.6-27b` | OK — slowest of the four |

- API key resolution: read only from `Settings.groq_api_key`
  (`GROQ_API_KEY` env var) — never the SDK's own environment auto-detection,
  so all configuration keeps flowing through `app.core.config`.
- Observed latency: **~1.3s mean** end-to-end per diagnosis. The 45s
  timeout is deliberate headroom, not a tight bound: a timeout that trips
  on ordinary latency doesn't protect anything, it just discards a good
  recommendation and escalates to a human.

**Exception classification** uses `isinstance` against the SDK's public
exception classes (`RateLimitError`, `AuthenticationError`,
`APITimeoutError`, `APIConnectionError`, `InternalServerError`,
`PermissionDeniedError`). Anything unrecognized falls through to
"unavailable" — failing closed, so an unclassified error still produces a
safe fallback rather than being mistaken for success. Every failure,
classified correctly or not, produces the same outcome (a fallback
ESCALATE), so a classification miss only affects the audit trail's
`failure_code` label, never behavior.

### A note on provider migration

This project previously ran on Google Gemini via `google-genai`'s
Interactions API. The migration to Groq touched the provider file, the
dependency wiring, and config — nothing in the services, API, policy
engine, or state machine. That is the `AIProvider` abstraction doing the
job it exists for.

The migration also surfaced two defects a fake provider could never have
caught, both worth recording: the previously pinned Gemini model had been
retired (`404 — no longer available to new users`), and the 20s timeout
then in place was below observed latency. In both cases the safe-fallback
path behaved exactly as designed — a recorded `ESCALATE`, no crash, no
invented recommendation — which is stronger evidence for the fallback
design than any test double could provide.

## Structured output & validation

`app/ai/schemas.py::RecoveryRecommendation` is the only shape that can
reach the rest of the system from an AI call:

```python
class RecoveryRecommendation(BaseModel):
    diagnosis_category: DiagnosisCategory   # enum, 6 values
    recovery_confidence: float              # 0.0–1.0, enforced
    recommended_action: RecoveryAction      # enum, 5 values
    decision_explanation: str               # 1–600 chars
```

Groq's `json_object` mode guarantees the response *parses* as JSON, but
enforces no schema — so `model_validate_json` in the provider is the only
thing that constrains it to this shape. An out-of-range confidence, an
invalid enum string, or non-JSON output all raise
`AIProviderInvalidResponseError`, and are never silently coerced or
clamped. Because the provider does not enforce the schema, this
validation is load-bearing rather than a second line of defence.

## Fallback behavior

`AIRecommendationService.get_recommendation` never raises and never
returns nothing. On any `AIProviderError` (after retrying transient ones —
timeout, rate limit, unavailable — up to `AI_MAX_RETRIES` times; auth and
invalid-response errors are not retried since trying again won't fix
them), it returns a synthetic recommendation:

```python
RecoveryRecommendation(
    diagnosis_category=DiagnosisCategory.UNKNOWN_FAILURE,
    recovery_confidence=0.0,
    recommended_action=RecoveryAction.ESCALATE,
    decision_explanation="AI diagnosis unavailable; escalating to a human reviewer. (...)",
)
```

marked `decision_source=FALLBACK`. This is deliberately **not** a special
code path around the policy engine — it's fed through the exact same
`RECOMMENDED -> POLICY_REVIEW -> evaluate_policy -> ESCALATED` sequence a
real AI recommendation would take (see `app/services/diagnosis_service.py`).
One reason: it makes "the AI is down" structurally indistinguishable, at
the policy layer, from "the AI is up but has no confidence" — both are
just a low-confidence ESCALATE recommendation being evaluated by the same
deterministic rules.

## Idempotency

`diagnose_recovery_case` only allows re-diagnosis from
`DISCOVERED`/`ELIGIBLE`/`FAILED` — every other status makes the state
machine's own `validate_transition` raise `InvalidStateTransitionError`
(HTTP 409). No separate idempotency table or lock was added; the existing
Phase 1 state machine already made re-processing an already-diagnosed
case structurally invalid. See `test_repeated_diagnose_request_is_rejected_not_reexecuted`.

## Evaluation extension (Phase 2)

`evaluation/generators/split.py::held_out_split` divides the dataset by a
SHA-256 hash of `case_id` into development/held-out sets — deterministic,
stable even if the dataset is regenerated at a different size for the
same seed. `evaluation/run_ai_evaluation.py`
scores the baseline and the LLM strategy against the *same* held-out
subset and writes a comparison report to
`evaluation/reports/ai_comparisons/`. Prompt and schema live in
`evaluation/ai_strategy/shared.py`, independently implemented from
`backend/app/ai` — see docs/evaluation-methodology.md,
which also carries the first real result: **the LLM underperformed the
rule-based baseline on every metric** on a 30-case clean run, on a
benchmark structurally biased toward the baseline.

## Known limitations

1. **Concurrency race on a single case.** Two simultaneous `POST
   .../diagnose` requests for the *same* case could both read the
   pre-transition status before either commits, and both proceed. The
   state machine prevents re-diagnosis *after* a transition commits, but
   not a true simultaneous race. Fixing this properly needs an optimistic
   lock (a version column) or `SELECT ... FOR UPDATE`, deliberately not
   added in Phase 2 to avoid over-engineering a scenario that requires two
   requests to land within the same few milliseconds on a UI-driven
   dashboard with no current multi-operator access.
2. **Exception classification is best-effort** (see above) — informational
   only, doesn't affect the fallback behavior itself.
3. **The LLM underperforms the rule-based baseline on the current
   benchmark.** A clean 30-case run (0 provider failures) scored the LLM
   below the baseline on every metric. The benchmark is structurally
   unfavourable to it — ground truth was authored from the same
   heuristics the baseline implements — but the result stands as measured:
   on this evidence the deterministic baseline is the better recommender.
   See docs/evaluation-methodology.md for the numbers and the caveats.
4. **`evaluation/ai_strategy/groq_strategy.py` duplicates prompt logic**
   from `backend/app/ai/prompts/diagnosis_v1.py` rather than importing it
   — consistent with `evaluation/`'s existing no-backend-dependency design
   (see docs/evaluation-methodology.md), but it means the two prompts can
   drift, and a real accuracy comparison is only as good as how similar
   they're kept by hand.
