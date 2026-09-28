from fastapi import APIRouter, Depends, Query

from ..schemas.common import AppliedFilters
from ..schemas.sales import (CategoryResponse, DailyPoint, HeatmapCell, InsightResponse, Kpi, KpiResponse,
                             MonthlyPoint)
from ..services import insights, sales
from .deps import filters

router = APIRouter(tags=["Sales"])

# metric -> (label, unit, is_estimated)
KPI_META = {
    "revenue": ("Revenue", "currency", False),
    "estimated_profit": ("Profit (est.)", "currency", True),
    "estimated_margin": ("Margin (est.)", "ratio", True),
    "orders": ("Orders", "count", False),
    "customers": ("Customers", "count", False),
    "avg_order_value": ("Average order value", "currency", False),
}
FLAT_THRESHOLD = 0.5   # |change| below 0.5% (or 0.5 margin points) is shown as flat


def _trend(metric: str, change_abs: float | None, change_pct: float | None) -> str:
    change = (change_abs * 100) if metric == "estimated_margin" and change_abs is not None else change_pct
    if change is None:
        return "n/a"
    if abs(change) < FLAT_THRESHOLD:
        return "flat"
    return "up" if change > 0 else "down"


@router.get("/kpis", response_model=KpiResponse,
            summary="KPI cards: current period vs previous period (growth = revenue change_pct)")
def get_kpis(f: AppliedFilters = Depends(filters)):
    rows = sales.kpis(f.start, f.end, f.state, f.category_key)
    kpis = []
    for r in rows:
        label, unit, estimated = KPI_META[r["metric"]]
        kpis.append(Kpi(label=label, unit=unit, is_estimated=estimated,
                        trend=_trend(r["metric"], r["change_abs"], r["change_pct"]), **r))
    return KpiResponse(filters=f, kpis=kpis)


@router.get("/sales/monthly", response_model=list[MonthlyPoint], summary="Monthly revenue, profit, orders and growth")
def get_monthly(f: AppliedFilters = Depends(filters),
                include_incomplete: bool = Query(False, description="Include months outside the reliable window")):
    return sales.monthly(f.state, f.category_key, include_incomplete)


@router.get("/sales/daily", response_model=list[DailyPoint], summary="Daily revenue and orders in the period")
def get_daily(f: AppliedFilters = Depends(filters)):
    return sales.daily(f.start, f.end, f.state, f.category_key)


@router.get("/sales/categories", response_model=CategoryResponse,
            summary="Revenue, estimated margin, share and growth by category")
def get_categories(f: AppliedFilters = Depends(filters)):
    return CategoryResponse(filters=f, rows=sales.by_category(f.start, f.end, f.previous_start, f.state,
                                                               f.category_key))


@router.get("/sales/heatmap", response_model=list[HeatmapCell], summary="Orders by weekday and hour")
def get_heatmap(f: AppliedFilters = Depends(filters)):
    return sales.weekday_hour(f.start, f.end, f.state, f.category_key)


@router.get("/insights", response_model=InsightResponse,
            summary="Data-backed findings and recommendations for the selected period")
def get_insights(f: AppliedFilters = Depends(filters)):
    return InsightResponse(filters=f, insights=insights.build(f.start, f.end, f.previous_start, f.state,
                                                              f.category_key))
