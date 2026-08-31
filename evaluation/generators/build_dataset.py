"""Builds a full Dataset by distributing `count` cases across scenarios."""
from __future__ import annotations

import random
from datetime import UTC, datetime

from evaluation.generators.scenarios import SCENARIO_GENERATORS
from evaluation.schemas.dataset_schema import Dataset, ScenarioType


def build_dataset(count: int, seed: int) -> Dataset:
    if count < 1:
        raise ValueError("count must be >= 1")

    rng = random.Random(seed)
    scenario_types = list(ScenarioType)
    cases = []

    # Distribute as evenly as possible across scenarios, deterministically.
    base, remainder = divmod(count, len(scenario_types))
    per_scenario_counts = {
        scenario: base + (1 if i < remainder else 0) for i, scenario in enumerate(scenario_types)
    }

    for scenario, scenario_count in per_scenario_counts.items():
        generator = SCENARIO_GENERATORS[scenario]
        for i in range(scenario_count):
            cases.append(generator(rng, i))

    # Shuffle deterministically so scenario order isn't trivially predictable
    # in downstream consumers, while remaining fully reproducible.
    rng.shuffle(cases)

    return Dataset(
        seed=seed,
        count=len(cases),
        generated_at=datetime.now(UTC).isoformat(),
        cases=cases,
    )
