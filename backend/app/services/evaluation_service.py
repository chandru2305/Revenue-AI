"""Surfaces the most recent evaluation report to the API.

This service never computes metrics itself. Metrics are only ever produced
by `evaluation/run_evaluation.py` against actual executions of the
generator + baseline strategy; this module just finds and parses the
latest JSON report file. If no report exists yet, it returns an explicit
empty state rather than fabricated numbers.

Only single-strategy reports (the shape `run_evaluation.py` writes) live
directly in `evaluation/reports/` — the baseline-vs-AI comparison reports
from `evaluation/run_ai_evaluation.py` go in a separate `ai_comparisons/`
subdirectory precisely so they're never picked up here and mis-parsed as
this (different) shape. `_parse` is still defensive about that in case a
stray file ever ends up in the wrong place: it skips anything that doesn't
validate rather than 500ing the endpoint.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.core.logging import get_logger, log_event
from app.schemas.evaluation import EvaluationSummaryRead

REPORTS_DIR = Path(__file__).resolve().parents[3] / "evaluation" / "reports"

logger = get_logger(__name__)


def _parse(path: Path) -> EvaluationSummaryRead | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return EvaluationSummaryRead(status="ok", **data)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        log_event(logger, logging.WARNING, "evaluation_report_unparsable", path=str(path), error=str(exc))
        return None


def get_latest_summary() -> EvaluationSummaryRead:
    if not REPORTS_DIR.exists():
        return EvaluationSummaryRead(status="no_evaluation_run")

    report_files = sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in report_files:
        parsed = _parse(path)
        if parsed is not None:
            return parsed

    return EvaluationSummaryRead(status="no_evaluation_run")
