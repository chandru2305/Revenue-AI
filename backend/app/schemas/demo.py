"""Response schema for the recovery batch endpoint."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.evaluation import RecoverySummaryRead


class DemoBatchResponse(BaseModel):
    correlation_id: str
    cases_processed: int
    final_status_counts: dict[str, int] = Field(
        description="How many cases ended in each status — a deliberately mixed batch."
    )
    ai_model: str = Field(
        description="The model that produced the diagnoses in this run — the live "
        "provider's model when a key is configured, or a fallback label otherwise."
    )
    summary: RecoverySummaryRead = Field(
        description="Measured recovery summary, computed by the real aggregation "
        "service over the rows this batch produced."
    )
    provenance: str = Field(
        description="What was real and what was simulated in this run. Carried in "
        "the response so the numbers cannot be quoted without it."
    )
