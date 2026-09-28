from fastapi import APIRouter, HTTPException

from ..schemas.common import AppliedFilters, HealthResponse, MetaResponse
from ..services import meta, ml
from .deps import default_period

router = APIRouter(tags=["Meta"])

ESTIMATED_NOTE = ("Profit and margin are ESTIMATES based on a synthetic cost model (Olist does not publish "
                  "product costs). Revenue, orders, customers and all other metrics are real.")


@router.get("/health", response_model=HealthResponse, summary="Liveness and database connectivity")
def health():
    try:
        info = meta.health()
    except Exception as exc:  # reported as a 503 without internals
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return HealthResponse(status="ok", database="ok", **info)


@router.get("/meta", response_model=MetaResponse,
            summary="Reporting period, default filters, filter options and model status")
def get_meta():
    start, end = default_period()
    previous = meta.previous_period(start, end)
    return MetaResponse(
        reporting_period=meta.reporting_period(),
        default_filters=AppliedFilters(start=start, end=end, previous_start=previous["prev_start"],
                                       previous_end=previous["prev_end"]),
        states=meta.states(),
        categories=meta.categories(),
        models=ml.active_runs(),
        freshness=meta.freshness(),
        estimated_metrics_note=ESTIMATED_NOTE,
    )
