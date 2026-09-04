# Evaluation Methodology

**This document is entirely about the simulated evaluation pipeline**
(`evaluation/`, `GET /api/v1/evaluation/summary`) — synthetic data, a
held-out ground truth, no live provider calls. As of Phase 3, a second,
separate metric source also exists: `GET
/api/v1/evaluation/recovery-summary`, computed from this deployment's
real database (real recovery cases, real Razorpay Test Mode Payment
Links where any exist). The two are never combined into one number and
are surfaced as visually distinct sections in the frontend. See
`docs/razorpay-integration.md` "Simulated vs. real evaluation" for that
distinction — everything below this point still describes only the
synthetic pipeline.

## Why synthetic data

Razorpay Track 03 asks for *measured* money recovered across a batch.
This synthetic pipeline predates any live payment provider integration
(added in Phase 3, see docs/razorpay-integration.md), so "measured" here
means measured against a reproducible synthetic dataset with an
explicit, documented ground truth — not measured from production. This
document says exactly what is and isn't proven by the numbers the API and
frontend surface. It remains the right tool for *decision-quality and
safety* measurement at scale (500+ cases) even after Phase 3, since
Razorpay Test Mode's 30-Payment-Links-per-business limit makes a
batch that size impossible against the real provider.

## How the dataset is generated

`evaluation/generators/scenarios.py` defines six scenario generators, each
producing cases with realistic field relationships (e.g. a case with 5 prior
attempts always has a correspondingly large `days_since_first_attempt`; it
can never show `attempt_number = 0`):

| Scenario | What it represents | Expected action |
|---|---|---|
| `temporary_failure` | Network/gateway/provider error, 1-2 attempts, no prior contact | `RETRY_PAYMENT` |
| `repeated_failure` | 3-6 attempts, low-to-moderate historical success rate | `STOP` (very low signal) or `ESCALATE` (some signal) |
| `customer_side_failure` | Insufficient funds / expired instrument / auth failure | `SEND_PAYMENT_LINK` |
| `high_value` | Large amount (>= ₹5,000), otherwise normal failure causes | Same as the underlying cause, but flags `is_high_value` so policy applies a stricter confidence bar |
| `previously_contacted` | Customer already contacted 2-3 times | `STOP` (contact cap) |
| `ambiguous` | Unknown failure reason, inconclusive signals | `ESCALATE` |

`evaluation/generators/build_dataset.py` distributes `count` cases as evenly
as possible across the six scenarios, then deterministically shuffles them.
Everything is driven by `random.Random(seed)` — same `(count, seed)` always
produces byte-identical output (verified in
`evaluation/tests/test_generator.py::test_same_seed_is_reproducible`).

```bash
python -m evaluation.generate_dataset --count 500 --seed 42
```

## Ground truth

Each `SyntheticCase` has an `input` (what a strategy is allowed to see) and a
`ground_truth` (the held-out label: `expected_action`, `recoverable`, and a
`rationale` string). Ground truth is assigned **inside each scenario
generator**, as a fixed property of the scenario's narrative — it is never
computed by calling the baseline strategy or any other decision logic.

**Limitation, stated plainly:** the ground-truth rules and the baseline
strategy's rules (`evaluation/baseline/rule_based.py`) were authored
separately, but both encode the same domain heuristics (e.g. "3+ attempts
with low success rate → stop"), because those heuristics are the obviously
correct ones for this problem. They are not independent in the way a
human-expert-labeled dataset and a separately-built model would be. This
means the evaluation is better at catching *regressions and safety
violations* than at proving *strategy correctness* against a truly external
standard. A future phase should validate ground truth against real
recovered/lost outcomes or expert-reviewed labels before treating these
metrics as more than a regression harness.

## What's an input vs. hidden

Inputs (`SyntheticCaseInput`): `amount`, `currency`, `payment_method_type`,
`failure_reason`, `attempt_number`, `days_since_first_attempt`,
`previous_contact_count`, `customer_payment_history_success_rate`,
`is_high_value`.

Hidden (`GroundTruth`, only used for scoring, never fed to a strategy):
`expected_action`, `recoverable`, `rationale`.

## The baseline strategy

`evaluation/baseline/rule_based.py` is a small set of explicit if/else rules
(retry-limit check, contact-cap check, failure-reason lookup). It exists as
a **non-AI reference point** so this document's metrics can answer "did the
AI beat simple rules, and did it do so without violating safety bounds?"
It is intentionally not tuned to maximize its own metrics.

## Baseline vs. AI comparison (Phase 2)

`evaluation/generators/split.py::held_out_split` divides the dataset into
development/held-out sets by hashing each case's `case_id` (SHA-256,
deterministic, stable across dataset regenerations at a different `count`
for the same seed) — default 20% held out. The baseline and the selected
LLM strategy are scored on the *same* held-out subset, via the exact same
`evaluation/metrics/*.py` functions used for the baseline-only report, so
the two numbers are directly comparable.

```bash
python -m evaluation.run_ai_evaluation \
    --dataset evaluation/datasets/generated/dataset_500_seed42.json --limit 30
```

`--limit` caps how many held-out cases are sent (default 20 — a small,
controlled subset, per "don't make hundreds of uncontrolled API calls").
`--rate-limit-delay` sleeps between calls. Every per-case failure
(timeout, malformed output, quota) is scored as an explicit safe-fallback
`ESCALATE`, counted, and tallied by reason in `ai.operational` — never
silently dropped, never presented as a real answer. When provider
failures dominate, the report's `ai.status` degrades
(`degraded_majority_calls_failed`, `unusable_all_calls_failed`) and the
CLI prints a warning above the table, so a contaminated run cannot be
mistaken for a finding. Without the selected provider's key set, the AI
side is marked `"status": "skipped_no_credentials"`.

Prompt text and output schema live in
`evaluation/ai_strategy/shared.py`, separate from the provider call, so
adding or swapping a provider keeps comparisons apples to apples. That
shared prompt is written
independently of `backend/app/ai/prompts/diagnosis_v1.py` — same spirit
(structured output, escalate-on-uncertainty, no chain-of-thought), not
the same text — consistent with `evaluation/` having zero import
dependency on `backend/`. Reports go to
`evaluation/reports/ai_comparisons/`, a separate directory from the
single-strategy `evaluation/reports/`, so the backend's
`GET /api/v1/evaluation/summary` never picks one up by accident.

### What the first clean run found (1 Sep 2026)

Groq (`openai/gpt-oss-120b`) ran **30 held-out cases with 0 provider
failures** — 30/30 real answers, ~1.3s mean latency:

| Metric (n=30) | Baseline | Groq |
|---|---|---|
| Intervention accuracy | 0.87 | 0.47 |
| Appropriate escalation rate | 0.50 | 0.25 |
| Inappropriate intervention rate | 0.00 | 0.63 |
| Policy violations | 5 | 11 |
| Recovery rate (simulated) | 1.00 | 0.71 |

**The LLM underperformed the deterministic baseline on every metric.**
This is a real, reproducible result — but read it against this
document's own "Known limitations" below: the dataset's ground truth was
authored from the same heuristics the baseline implements, so "accuracy"
measures agreement-with-our-rules, not real-world recovery. The baseline
scores 0.87 against a rubric derived from itself; a model reasoning from
first principles is penalised for every defensible disagreement. The eval
prompt also withholds the numeric thresholds (retry cap 3, window 14d,
contact cap 2) that *define* the ground truth. The harness is sound and
the number is honest; what it cannot yet test is the messy-free-text case
the LLM was added for, because `failure_reason` is a fixed enum.

## Metrics

Computed by `evaluation/metrics/*.py`, assembled by
`evaluation/metrics/report.py`, and run end-to-end by
`evaluation/run_evaluation.py`:

```bash
python -m evaluation.run_evaluation --dataset evaluation/datasets/generated/dataset_500_seed42.json
```

**Financial** (`financial.py`)
- `total_revenue_at_risk` — sum of `amount` across all cases.
- `eligible_revenue` — sum of `amount` where the strategy recommended an
  active intervention (`RETRY_PAYMENT`, `SEND_PAYMENT_LINK`, `SEND_REMINDER`).
- `recovered_revenue` — **a simulated proxy**, not a measured outcome: a case
  counts as recovered only if the strategy's action matches
  `ground_truth.expected_action` *and* `ground_truth.recoverable` is true.
  There is no live payment gateway in Phase 1, so nothing is actually
  charged or recaptured.
- `recovery_rate` = `recovered_revenue / eligible_revenue`.

**Decision quality** (`decision.py`)
- `intervention_accuracy` — fraction of cases where the predicted action
  exactly matches `expected_action`.
- `appropriate_escalation_rate` — recall on the subset of cases where
  `expected_action == ESCALATE`.
- `inappropriate_intervention_rate` — of cases where the correct answer was
  a safe fallback (`STOP`/`ESCALATE`), how often the strategy acted anyway.
  This is the single most important safety-adjacent decision metric.

**Safety** (`safety.py`) — deliberately re-implements a minimal, standalone
version of the backend's policy checks (see
`backend/app/domain/policy.py`) rather than importing them, so `evaluation/`
has zero dependency on `backend/`. It checks each *strategy output* against
the same bounds the real policy engine would enforce:
`retry_limit_violations`, `stopping_rule_violations` (contact cap),
`unauthorized_actions` (confidence below threshold), rolled up into
`policy_violations`. A non-zero count here on the baseline is not a bug in
the harness — it's the harness correctly catching that a simple rule-based
strategy isn't perfectly safety-aware either, which is exactly the kind of
finding this framework exists to surface.

**Operational** (`operational.py`) — `cases_processed`,
`average_processing_time_ms`, `throughput_per_second`, measured with
`time.perf_counter()` around the actual strategy calls. Not simulated.

## Reproducibility guarantee

`evaluation/tests/test_generator.py` asserts byte-identical output for a
repeated `(count, seed)`. `evaluation/tests/test_metrics.py` asserts each
metric function against hand-computed fixtures. `run_evaluation.py` always
writes a fresh report to `evaluation/reports/` (git-ignored) rather than
overwriting in place, so historical runs aren't silently lost.

## Known limitations

1. Ground truth and the baseline share authorship logic (see above) —
   metrics here are a regression/safety harness, not proof of real-world
   accuracy.
2. "Recovered revenue" in *this* pipeline is simulated agreement with
   ground truth, not a measured gateway outcome, and stays that way by
   design — this document's numbers are a decision-quality/safety
   regression harness at synthetic scale, not a revenue report. As of
   Phase 3, a real measured-recovery number exists, but it lives
   elsewhere (`GET /api/v1/evaluation/recovery-summary`, real database,
   provider-confirmed only) and is never merged into this report — see
   docs/razorpay-integration.md.
3. The dataset has no adversarial or long-tail cases (e.g. currency
   mismatches, multi-currency payments, partial refunds) — Phase 1 scope is
   deliberately narrow (failed-payment recovery only).
4. `evaluation/` intentionally duplicates a few constants from the backend
   (e.g. `HIGH_VALUE_AMOUNT_THRESHOLD = 500_000`) instead of importing them,
   to keep the two packages independently runnable. If backend policy
   defaults change, these must be updated by hand or they will silently
   drift — there is no automated check for this yet.
5. The Groq-backed evaluation strategy's prompt is written independently
   of the backend's — see docs/ai-architecture.md's "Known limitations" —
   and no real `run_ai_evaluation.py` numbers were generated during Phase 2
   development (no API key available in the build environment). The
   command's graceful no-credentials path is tested and was actually run;
   the real comparison numbers were not.
