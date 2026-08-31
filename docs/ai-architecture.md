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
    gemini.py           GeminiProvider (google-genai Interactions API)
    fake.py             FakeAIProvider — deterministic, used in every test
  service.py           AIRecommendationService — retry + safe fallback
  dependencies.py      FastAPI wiring (get_ai_provider / get_ai_service)
```

`AIProvider.diagnose_payment(context) -> RecoveryRecommendation` is the
entire interface. `GeminiProvider` and `FakeAIProvider` both implement it;
`app.services.diagnosis_service` and `AIRecommendationService` never know
or care which one they're talking to. Adding a second real provider later
means writing one more file in `providers/` — nothing else changes.

## Gemini integration — what was actually verified

The current official docs were fetched and cross-checked during Phase 2
development (not assumed from training knowledge), and the request shape
was verified against the real API with an intentionally invalid key
(confirmed the failure was `API key not valid`, a real API-level 400, not
a client-side shape error):

- Package: `google-genai==2.20.0` (`pip install -U google-genai`).
- Call surface: **Interactions API**, not the older `generate_content`
  API — `client.aio.interactions.create(model=..., system_instruction=...,
  input=..., response_format={...}, timeout=...)`. The async variant
  (`client.aio.interactions`) is a real coroutine function, verified by
  introspection against the installed package.
- Structured output: `response_format={"type": "text", "mime_type":
  "application/json", "schema": PydanticModel.model_json_schema()}`,
  result parsed via `Model.model_validate_json(interaction.output_text)`.
- Default model: `gemini-2.5-flash` — documented by Google as "best
  price-performance... low-latency, high-volume tasks that require
  reasoning," which is exactly this workload (a bounded classification +
  short-explanation task, run per recovery case). Configurable via
  `GEMINI_MODEL`; a newer flagship (`gemini-3.7-flash` at the time of
  writing) can be swapped in without a code change.
- API key resolution: read only from `Settings.gemini_api_key`
  (`GEMINI_API_KEY` env var) — never falls back to the SDK's own
  environment-variable auto-detection, to keep all configuration flowing
  through `app.core.config` as the rest of the app does.

**Exception handling caveat, documented rather than hidden:** the
Interactions API raises exceptions from a private module
(`google.genai._gaos.lib.compat_errors`) — a different, non-public
hierarchy from the documented `google.genai.errors.APIError` used by the
older API. `GeminiProvider._classify` matches on exception *class name*
(`"RateLimitError"`, `"AuthenticationError"`, `"APITimeoutError"`, ...)
rather than importing that private module, so a future SDK release
changing its internals doesn't break an import — but it also means
classification is best-effort. This doesn't weaken safety: every failure,
correctly classified or not, produces the same outcome — a fallback
ESCALATE — so a classification miss only affects the audit trail's
`failure_code` label, never behavior.

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

Gemini's structured-output mode constrains the model to this JSON schema
at generation time, and `model_validate_json` re-validates on the way
back in — an out-of-range confidence, an invalid enum string, or
non-JSON output all raise `AIProviderInvalidResponseError`, never get
silently coerced or clamped.

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
same seed. `evaluation/run_ai_evaluation.py` scores both the baseline and
a Gemini-backed strategy (`evaluation/ai_strategy/gemini_strategy.py`,
independently implemented from `backend/app/ai` — see
docs/evaluation-methodology.md on why) against the *same* held-out subset
and writes a comparison report to `evaluation/reports/ai_comparisons/`
(a separate directory from `evaluation/reports/` specifically so the
backend's `GET /api/v1/evaluation/summary`, which expects the
single-strategy shape, never picks one up by mistake).

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
3. **No real Gemini evaluation could be run during Phase 2 development** —
   no `GEMINI_API_KEY` was available in the build environment. Everything
   in this document was verified through: (a) fetching current official
   docs, (b) a live request against the real API with an intentionally
   invalid key (confirmed the request shape reaches the API and gets a
   real auth error back), and (c) the full `FakeAIProvider`-driven test
   suite plus a live browser run of the actual fallback path (screenshot
   in the Phase 2 report). The `python -m evaluation.run_ai_evaluation`
   command is implemented and tested for its skip-gracefully path; running
   it with real comparison numbers requires a key this environment didn't
   have. See the Phase 2 report for exactly what was and wasn't executed.
4. **`evaluation/ai_strategy/gemini_strategy.py` duplicates prompt logic**
   from `backend/app/ai/prompts/diagnosis_v1.py` rather than importing it
   — consistent with `evaluation/`'s existing no-backend-dependency design
   (see docs/evaluation-methodology.md), but it means the two prompts can
   drift, and a real accuracy comparison is only as good as how similar
   they're kept by hand.
