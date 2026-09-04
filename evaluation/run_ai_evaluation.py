"""CLI: compare the rule-based baseline against the LLM on a held-out subset.

Usage:
    # Uses GROQ_API_KEY from the environment. Without it, still runs and
    # reports real baseline numbers, with the AI side explicitly marked as
    # skipped — never fabricated.
    python -m evaluation.run_ai_evaluation \
        --dataset evaluation/datasets/generated/dataset_500_seed42.json --limit 30

Cost/quota controls: `--limit` caps how many held-out cases are actually
sent to the LLM (default 20 — a small, controlled subset, not the full
dataset). `--rate-limit-delay` sleeps between calls. A per-case failure
(timeout, malformed output, quota) is scored as a safe-fallback ESCALATE,
counted, and tallied by reason — never silently dropped or hidden. When
failures dominate, `ai.status` degrades so the comparison table can't be
mistaken for a decision-quality result.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from evaluation.ai_strategy import groq_strategy
from evaluation.ai_strategy.shared import AINotConfiguredError
from evaluation.baseline.rule_based import decide as baseline_decide
from evaluation.generators.build_dataset import build_dataset
from evaluation.generators.split import DEFAULT_HOLDOUT_FRACTION, held_out_split
from evaluation.metrics.decision import compute_decision_metrics
from evaluation.metrics.financial import compute_financial_metrics
from evaluation.metrics.safety import compute_safety_metrics
from evaluation.schemas.dataset_schema import Dataset

REPORTS_DIR = Path(__file__).resolve().parent / "reports" / "ai_comparisons"

# The strategy module (exposes build_client, decide, MODEL) and the env
# var its key comes from. Kept as a lookup rather than inlined so adding a
# second provider stays a one-line change.
_STRATEGY = groq_strategy
_KEY_ENV = "GROQ_API_KEY"


def _strategy_metrics(cases, decisions) -> dict:
    financial = compute_financial_metrics(cases, decisions)
    decision_metrics = compute_decision_metrics(cases, decisions)
    safety = compute_safety_metrics(cases, decisions)
    return {
        "financial": financial.__dict__,
        "decision": decision_metrics.__dict__,
        "safety": safety.__dict__,
    }


async def _run_ai_side(cases, *, api_key: str, rate_limit_delay: float) -> tuple[list, dict]:
    module = _STRATEGY
    client = module.build_client(api_key)
    decisions = []
    failures = 0
    failure_reasons: dict[str, int] = {}
    latencies_ms = []

    for index, case in enumerate(cases):
        outcome = await module.decide(client, case.input)
        decisions.append(outcome.decision)
        latencies_ms.append(outcome.latency_ms)
        if not outcome.succeeded:
            failures += 1
            tag = (outcome.error or "unknown").split(":", 1)[0][:40]
            failure_reasons[tag] = failure_reasons.get(tag, 0) + 1
        if index < len(cases) - 1:
            await asyncio.sleep(rate_limit_delay)

    operational = {
        "provider": "groq",
        "model": module.MODEL,
        "cases_processed": len(cases),
        "provider_failures": failures,
        # Broken out so a run dominated by quota/timeout errors can't be
        # mistaken for one where the model genuinely answered badly.
        "failure_reasons": failure_reasons,
        "real_answers": len(cases) - failures,
        "average_latency_ms": round(sum(latencies_ms) / len(latencies_ms), 1) if latencies_ms else 0.0,
    }
    return decisions, operational


def _print_comparison_table(baseline_metrics: dict, ai_metrics: dict | None) -> None:
    print()
    print(f"{'Metric':<38}{'Baseline':>14}{'AI':>14}")
    print("-" * 66)
    rows = [
        ("Intervention accuracy", baseline_metrics["decision"]["intervention_accuracy"]),
        ("Appropriate escalation rate", baseline_metrics["decision"]["appropriate_escalation_rate"]),
        ("Inappropriate intervention rate", baseline_metrics["decision"]["inappropriate_intervention_rate"]),
        ("Policy violations", baseline_metrics["safety"]["policy_violations"]),
        ("Recovery rate (simulated)", baseline_metrics["financial"]["recovery_rate"]),
    ]
    keys = [
        ("decision", "intervention_accuracy"),
        ("decision", "appropriate_escalation_rate"),
        ("decision", "inappropriate_intervention_rate"),
        ("safety", "policy_violations"),
        ("financial", "recovery_rate"),
    ]
    for (label, baseline_value), (group, key) in zip(rows, keys, strict=True):
        ai_value = ai_metrics[group][key] if ai_metrics else "n/a"
        print(f"{label:<38}{baseline_value!s:>14}{ai_value!s:>14}")
    print()


def _build_report(
    dataset: Dataset,
    holdout_fraction: float,
    holdout_size: int,
    cases_evaluated: int,
    baseline_metrics: dict,
    ai_metrics: dict | None,
    ai_operational: dict | None,
    ai_status: str,
) -> dict:
    import uuid

    return {
        "run_id": str(uuid.uuid4()),
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_seed": dataset.seed,
        "dataset_count": dataset.count,
        "holdout_fraction": holdout_fraction,
        "holdout_size": holdout_size,
        "cases_evaluated": cases_evaluated,
        "baseline": baseline_metrics,
        "ai": {
            "status": ai_status,
            **(ai_metrics or {}),
            **({"operational": ai_operational} if ai_operational else {}),
        },
    }


async def main_async() -> None:
    parser = argparse.ArgumentParser(
        description="Compare the rule-based baseline against the LLM on a held-out subset."
    )
    parser.add_argument("--dataset", type=Path, default=None, help="Path to an existing dataset JSON.")
    parser.add_argument("--count", type=int, default=500, help="Cases to generate if --dataset is omitted.")
    parser.add_argument("--seed", type=int, default=42, help="Seed to generate if --dataset is omitted.")
    parser.add_argument("--holdout-fraction", type=float, default=DEFAULT_HOLDOUT_FRACTION)
    parser.add_argument("--limit", type=int, default=20, help="Max held-out cases actually sent to the LLM.")
    parser.add_argument("--rate-limit-delay", type=float, default=1.0, help="Seconds between LLM calls.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.dataset:
        dataset = Dataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    else:
        dataset = build_dataset(count=args.count, seed=args.seed)

    _development, held_out = held_out_split(dataset, args.holdout_fraction)
    cases = held_out[: args.limit]
    if not cases:
        raise SystemExit("Held-out split produced zero cases — check --holdout-fraction and dataset size.")

    baseline_decisions = [baseline_decide(case.input) for case in cases]
    baseline_metrics = _strategy_metrics(cases, baseline_decisions)

    api_key = os.environ.get(_KEY_ENV, "")
    ai_metrics: dict | None = None
    ai_operational: dict | None = None
    ai_status = "ok"
    try:
        started = time.perf_counter()
        ai_decisions, ai_operational = await _run_ai_side(
            cases, api_key=api_key, rate_limit_delay=args.rate_limit_delay
        )
        ai_operational["wall_clock_seconds"] = round(time.perf_counter() - started, 1)
        ai_metrics = _strategy_metrics(cases, ai_decisions)

        # A run where provider failures dominate says nothing about model
        # quality — the "AI" numbers are then almost entirely the
        # safe-fallback ESCALATE path. Degrade the status so a reader
        # can't mistake the comparison table for a finding.
        failed = ai_operational["provider_failures"]
        if failed == len(cases):
            ai_status = "unusable_all_calls_failed"
        elif failed > len(cases) / 2:
            ai_status = "degraded_majority_calls_failed"
    except AINotConfiguredError as exc:
        ai_status = "skipped_no_credentials"
        print(f"AI evaluation skipped: {exc}")

    report = _build_report(
        dataset,
        args.holdout_fraction,
        len(held_out),
        len(cases),
        baseline_metrics,
        ai_metrics,
        ai_operational,
        ai_status,
    )

    output_path = args.output or REPORTS_DIR / f"ai_comparison_seed{dataset.seed}_{report['run_id'][:8]}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Held-out set: {len(held_out)} cases ({args.holdout_fraction:.0%} of {dataset.count}).")
    print(f"Evaluated {len(cases)} of them (--limit {args.limit}).")
    if ai_operational:
        print(
            f"Provider: {ai_operational['provider']} ({ai_operational['model']}) — "
            f"{ai_operational['real_answers']}/{len(cases)} real answers, "
            f"{ai_operational['provider_failures']} failures {ai_operational['failure_reasons'] or ''}"
        )
    print(f"AI status: {ai_status}")
    if ai_status.startswith(("unusable", "degraded")):
        print(
            "  ^ provider failures dominate this run. The AI column below is "
            "mostly the safe-fallback ESCALATE path, NOT a decision-quality result."
        )
    _print_comparison_table(baseline_metrics, ai_metrics)
    print(f"Report written to {output_path}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
