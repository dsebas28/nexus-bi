from typing import Literal

from fastapi import APIRouter, Depends, Query

from ..schemas.common import AppliedFilters
from ..schemas.sales import ProductResponse
from ..services import products
from .deps import filters

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("/top", response_model=ProductResponse, summary="Best-selling or most profitable products in the period")
def get_top(f: AppliedFilters = Depends(filters),
            sort_by: Literal["revenue", "units", "estimated_profit", "estimated_margin"] = "revenue",
            limit: int = Query(20, ge=1, le=200),
            min_units: int = Query(1, ge=1, le=1000, description="Minimum units sold (useful when sorting by margin)")):
    rows = products.top(f.start, f.end, f.state, f.category_key, sort_by, limit, min_units)
    return ProductResponse(criteria=f"Top {limit} by {sort_by} between {f.start} and {f.end} "
                                    f"(min {min_units} units)", rows=rows)


@router.get("/low-rotation", response_model=ProductResponse, summary="Products that stopped selling")
def get_low_rotation(category: int | None = Query(None, ge=1),
                     min_units: int = Query(10, ge=1, le=1000),
                     limit: int = Query(20, ge=1, le=200)):
    return ProductResponse(
        criteria=f"At least {min_units} units sold historically and no sale in the 90 days before the reference date",
        rows=products.low_rotation(category, min_units, limit))


@router.get("/high-revenue-low-margin", response_model=ProductResponse,
            summary="Products with above-median revenue but bottom-quartile estimated margin")
def get_high_revenue_low_margin(min_units: int = Query(10, ge=1, le=1000), limit: int = Query(20, ge=1, le=200)):
    return ProductResponse(
        criteria=(f"Products with >= {min_units} units: revenue above the median and estimated margin in the bottom "
                  "quartile. Margin relies on the synthetic cost model."),
        rows=products.high_revenue_low_margin(min_units, limit))
