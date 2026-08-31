"""CLI: compare the rule-based baseline against Gemini on a held-out subset.

Usage:
    # Uses GEMINI_API_KEY from the environment. Without it, still runs and
    # reports real baseline numbers, with the AI side explicitly marked as
    # skipped — never fabricated.
    python -m evaluation.run_ai_evaluation \
        --dataset evaluation/datasets/generated/dataset_500_seed42.json --limit 20

Cost/quota controls: `--limit` caps how many held-out cases are actually
sent to Gemini (default 20 — this is a small, controlled subset, not the
full dataset). `--rate-limit-delay` sleeps between calls. A per-case
failure (timeout, malformed output, ...) is scored as a safe-fallback
ESCALATE and counted, never silently dropped or hidden.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from evaluation.ai_strategy.gemini_strategy import GeminiNotConfiguredError, build_client, decide
from evaluation.baseline.rule_based import decide as baseline_decide
from evaluation.generators.build_dataset import build_dataset
from evaluation.generators.split import DEFAULT_HOLDOUT_FRACTION, held_out_split
from evaluation.metrics.decision import compute_decision_metrics
from evaluation.metrics.financial import compute_financial_metrics
from evaluation.metrics.safety import compute_safety_metrics
from evaluation.schemas.dataset_schema import Dataset

REPORTS_DIR = Path(__file__).resolve().parent / "reports" / "ai_comparisons"


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
    client = build_client(api_key)
    decisions = []
    failures = 0
    latencies_ms = []

    for index, case in enumerate(cases):
        outcome = await decide(client, case.input)
        decisions.append(outcome.decision)
        latencies_ms.append(outcome.latency_ms)
        if not outcome.succeeded:
            failures += 1
        if index < len(cases) - 1:
            await asyncio.sleep(rate_limit_delay)

    operational = {
        "cases_processed": len(cases),
        "provider_failures": failures,
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
        description="Compare the baseline strategy against Gemini on a held-out subset."
    )
    parser.add_argument("--dataset", type=Path, default=None, help="Path to an existing dataset JSON.")
    parser.add_argument("--count", type=int, default=500, help="Cases to generate if --dataset is omitted.")
    parser.add_argument("--seed", type=int, default=42, help="Seed to generate if --dataset is omitted.")
    parser.add_argument("--holdout-fraction", type=float, default=DEFAULT_HOLDOUT_FRACTION)
    parser.add_argument("--limit", type=int, default=20, help="Max held-out cases actually sent to Gemini.")
    parser.add_argument("--rate-limit-delay", type=float, default=1.0, help="Seconds between Gemini calls.")
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

    api_key = os.environ.get("GEMINI_API_KEY", "")
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
    except GeminiNotConfiguredError as exc:
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
    print(f"AI status: {ai_status}")
    _print_comparison_table(baseline_metrics, ai_metrics)
    print(f"Report written to {output_path}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
