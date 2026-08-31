"""Operational metrics, measured from an actual run of the strategy."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from evaluation.baseline.rule_based import BaselineDecision
from evaluation.schemas.dataset_schema import SyntheticCase


@dataclass(frozen=True)
class OperationalMetrics:
    cases_processed: int
    average_processing_time_ms: float
    throughput_per_second: float


def run_and_measure(
    cases: list[SyntheticCase], strategy: Callable[[SyntheticCase], BaselineDecision]
) -> tuple[list[BaselineDecision], OperationalMetrics]:
    decisions: list[BaselineDecision] = []
    start = time.perf_counter()
    for case in cases:
        decisions.append(strategy(case))
    elapsed_seconds = time.perf_counter() - start

    cases_processed = len(cases)
    average_ms = (elapsed_seconds / cases_processed * 1000) if cases_processed else 0.0
    throughput = (cases_processed / elapsed_seconds) if elapsed_seconds > 0 else float(cases_processed)

    metrics = OperationalMetrics(
        cases_processed=cases_processed,
        average_processing_time_ms=round(average_ms, 4),
        throughput_per_second=round(throughput, 2),
    )
    return decisions, metrics
