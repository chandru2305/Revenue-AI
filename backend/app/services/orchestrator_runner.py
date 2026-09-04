"""Background runner for the autonomous recovery loop.

Started from the FastAPI lifespan when `ORCHESTRATOR_ENABLED` is true, and
cancelled cleanly on shutdown. Off by default: a dev server, a test run,
and `make up` must never start making provider calls on their own.

Deliberately a plain asyncio task rather than Celery/APScheduler. The work
is a single idempotent function, the cadence is minutes, and the whole
system is one process — a task queue would add operational surface with
nothing to show for it. If this ever needs multiple workers or durable
scheduling, the service it calls (`orchestrator_service.run_recovery_cycle`)
is already the right seam to move behind one.

Each cycle gets its own DB session, so a failure can never poison the next
one. An unexpected exception is logged and the loop continues — a
background worker that dies silently on one bad cycle is worse than one
that keeps trying.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from app.ai.dependencies import get_ai_service
from app.core.config import get_settings
from app.core.logging import get_logger, log_event, set_correlation_id
from app.db.session import AsyncSessionLocal
from app.payments.dependencies import get_payment_provider
from app.services import orchestrator_service

logger = get_logger(__name__)

_task: asyncio.Task | None = None


async def _run_forever() -> None:
    settings = get_settings()
    interval = settings.orchestrator_interval_seconds

    log_event(
        logger,
        logging.INFO,
        "orchestrator_runner_started",
        interval_seconds=interval,
        auto_execute=settings.orchestrator_auto_execute,
    )

    while True:
        correlation_id = f"cycle-{uuid.uuid4()}"
        set_correlation_id(correlation_id)
        try:
            async with AsyncSessionLocal() as session:
                await orchestrator_service.run_recovery_cycle(
                    session,
                    ai_service=get_ai_service(),
                    provider=get_payment_provider(),
                    correlation_id=correlation_id,
                    auto_execute=settings.orchestrator_auto_execute,
                    max_discover=settings.orchestrator_max_discover,
                    max_diagnose=settings.orchestrator_max_diagnose,
                    max_execute=settings.orchestrator_max_execute,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the loop
            log_event(
                logger,
                logging.ERROR,
                "orchestrator_cycle_failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        await asyncio.sleep(interval)


def status() -> dict[str, bool]:
    """A read-only snapshot of the background runner, for the agent console.

    `running` is true only while the asyncio task is alive. `errored` is
    true if the task finished on its own with an exception — `_run_forever`
    only ever exits via cancellation, so a self-terminated task means a bug
    escaped the per-cycle guard.
    """
    settings = get_settings()
    running = _task is not None and not _task.done()
    errored = False
    if _task is not None and _task.done() and not _task.cancelled():
        errored = _task.exception() is not None
    return {
        "enabled": settings.orchestrator_enabled,
        "running": running,
        "errored": errored,
    }


def start() -> None:
    """Start the loop if enabled. Idempotent."""
    global _task
    settings = get_settings()
    if not settings.orchestrator_enabled:
        log_event(logger, logging.INFO, "orchestrator_runner_disabled")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_run_forever(), name="recovery-orchestrator")


async def stop() -> None:
    """Cancel the loop and wait for it to unwind. Safe if never started."""
    global _task
    if _task is None:
        return
    _task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _task
    _task = None
    log_event(logger, logging.INFO, "orchestrator_runner_stopped")
