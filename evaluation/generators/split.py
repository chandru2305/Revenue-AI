"""Deterministic dev/held-out split for a generated Dataset.

`evaluation/run_evaluation.py` (the baseline) has historically been run
against the full dataset — that's still valid as a regression harness (see
docs/evaluation-methodology.md). For a baseline-vs-AI *comparison*
(`run_ai_evaluation.py`), both strategies are scored on the same held-out
slice so the comparison is apples-to-apples and not tuned-on-the-answers.

The split is by `case_id` (stable regardless of shuffle order or dataset
size), not by index, so it stays reproducible even if the dataset is
regenerated with a different `count` for the same seed.
"""
from __future__ import annotations

import hashlib

from evaluation.schemas.dataset_schema import Dataset, SyntheticCase

DEFAULT_HOLDOUT_FRACTION = 0.2


def _hash_fraction(case_id: str) -> float:
    """Maps a case_id deterministically to [0.0, 1.0) — stable across
    Python versions/processes, unlike the builtin `hash()`."""
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def held_out_split(
    dataset: Dataset, holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION
) -> tuple[list[SyntheticCase], list[SyntheticCase]]:
    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be between 0 and 1 (exclusive).")

    development: list[SyntheticCase] = []
    held_out: list[SyntheticCase] = []
    for case in dataset.cases:
        bucket = held_out if _hash_fraction(case.input.case_id) < holdout_fraction else development
        bucket.append(case)

    return development, held_out
