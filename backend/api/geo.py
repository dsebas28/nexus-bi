from fastapi import APIRouter, Depends, Query

from ..schemas.common import AppliedFilters
from ..schemas.sales import GeoResponse
from ..services import geo
from .deps import filters

router = APIRouter(prefix="/geo", tags=["Geography"])


@router.get("/states", response_model=GeoResponse,
            summary="Sales, customers, growth, profitability and delivery by state (map layer)")
def get_states(f: AppliedFilters = Depends(filters)):
    return GeoResponse(filters=f, rows=geo.states(f.start, f.end, f.previous_start, f.category_key))


@router.get("/cities", response_model=GeoResponse, summary="Top cities with coordinates (map bubbles)")
def get_cities(f: AppliedFilters = Depends(filters), limit: int = Query(300, ge=1, le=2000)):
    return GeoResponse(filters=f, rows=geo.cities(f.start, f.end, f.previous_start, f.state, f.category_key, limit))
