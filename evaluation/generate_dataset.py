"""CLI: generate a reproducible synthetic recovery-case dataset.

Usage:
    python -m evaluation.generate_dataset --count 500 --seed 42
    python -m evaluation.generate_dataset --count 500 --seed 42 --output path/to/file.json
"""
from __future__ import annotations

import argparse
from pathlib import Path

from evaluation.generators.build_dataset import build_dataset

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "datasets" / "generated"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a synthetic RecoverAI evaluation dataset.")
    parser.add_argument("--count", type=int, default=500, help="Number of cases to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path. Defaults to evaluation/datasets/generated/dataset_<count>_seed<seed>.json",
    )
    args = parser.parse_args()

    dataset = build_dataset(count=args.count, seed=args.seed)

    output_path = args.output or DEFAULT_OUTPUT_DIR / f"dataset_{args.count}_seed{args.seed}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(dataset.model_dump_json(indent=2), encoding="utf-8")

    print(f"Generated {dataset.count} cases (seed={dataset.seed}) -> {output_path}")


if __name__ == "__main__":
    main()
