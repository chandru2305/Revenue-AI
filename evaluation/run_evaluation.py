"""CLI: run the baseline strategy over a dataset and write a metrics report.

Usage:
    python -m evaluation.run_evaluation --dataset evaluation/datasets/generated/dataset_500_seed42.json
    python -m evaluation.run_evaluation --count 500 --seed 42   # generates the dataset first
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.baseline.rule_based import decide
from evaluation.generators.build_dataset import build_dataset
from evaluation.metrics.decision import compute_decision_metrics
from evaluation.metrics.financial import compute_financial_metrics
from evaluation.metrics.operational import run_and_measure
from evaluation.metrics.report import build_report
from evaluation.metrics.safety import compute_safety_metrics
from evaluation.schemas.dataset_schema import Dataset

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _baseline_strategy(case):
    return decide(case.input)


def run(dataset: Dataset, strategy_name: str = "baseline_rule_based") -> dict:
    decisions, operational = run_and_measure(dataset.cases, _baseline_strategy)
    financial = compute_financial_metrics(dataset.cases, decisions)
    decision_metrics = compute_decision_metrics(dataset.cases, decisions)
    safety = compute_safety_metrics(dataset.cases, decisions)
    return build_report(dataset, strategy_name, financial, decision_metrics, safety, operational)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RecoverAI evaluation baseline and report metrics.")
    parser.add_argument("--dataset", type=Path, default=None, help="Path to an existing dataset JSON.")
    parser.add_argument("--count", type=int, default=500, help="Cases to generate if --dataset is omitted.")
    parser.add_argument("--seed", type=int, default=42, help="Seed to generate if --dataset is omitted.")
    parser.add_argument("--output", type=Path, default=None, help="Report output path.")
    args = parser.parse_args()

    if args.dataset:
        dataset = Dataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    else:
        dataset = build_dataset(count=args.count, seed=args.seed)

    report = run(dataset)

    run_id_prefix = report["run_id"][:8]
    default_name = f"report_{dataset.count}_seed{dataset.seed}_{run_id_prefix}.json"
    output_path = args.output or REPORTS_DIR / default_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Evaluated {dataset.count} cases (seed={dataset.seed}) -> {output_path}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
