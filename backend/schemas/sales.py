"""Response models for KPIs, sales, products, customers and geography."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from .common import AppliedFilters


class Kpi(BaseModel):
    metric: str
    label: str
    unit: Literal["currency", "count", "ratio"]
    is_estimated: bool = Field(description="True for profit and margin (synthetic cost model)")
    current_value: float | None
    previous_value: float | None
    change_abs: float | None
    change_pct: float | None = Field(description="Relative change in %; for ratios prefer change_abs (points)")
    trend: Literal["up", "down", "flat", "n/a"]


class KpiResponse(BaseModel):
    filters: AppliedFilters
    kpis: list[Kpi]


class MonthlyPoint(BaseModel):
    month: date
    is_complete: bool
    revenue: float
    estimated_profit: float
    estimated_margin: float | None
    orders: int
    customers: int
    avg_order_value: float | None
    revenue_growth_mom: float | None


class DailyPoint(BaseModel):
    day: date
    revenue: float
    orders: int


class CategoryRow(BaseModel):
    category_key: int
    category: str
    revenue: float
    estimated_profit: float
    estimated_margin: float | None
    units: int
    orders: int
    revenue_share: float | None
    revenue_previous: float
    revenue_growth: float | None


class CategoryResponse(BaseModel):
    filters: AppliedFilters
    rows: list[CategoryRow]


class HeatmapCell(BaseModel):
    day_of_week: int
    day_name: str
    hour: int
    orders: int


class ProductRow(BaseModel):
    product_key: int
    product_short_id: str
    category: str
    units: int
    orders: int | None = None
    revenue: float
    estimated_profit: float | None = None
    estimated_margin: float | None = None
    avg_price: float | None = None
    first_sale: date | None = None
    last_sale: date | None = None
    days_since_last_sale: int | None = None


class ProductResponse(BaseModel):
    criteria: str
    rows: list[ProductRow]


class CustomerOverview(BaseModel):
    filters: AppliedFilters
    active_customers: int
    new_customers: int
    returning_customers: int
    avg_orders_per_customer: float | None
    avg_spend_in_period: float | None
    avg_lifetime_revenue: float | None
    avg_lifetime_estimated_profit: float | None
    repeat_customer_rate: float | None
    base: dict = Field(description="Whole customer base up to the reference date (CLV, repeat rate, cadence)")
    frequency_distribution: list[dict]


class NewVsReturningPoint(BaseModel):
    month: date
    new_customers: int
    returning_customers: int
    returning_revenue_share: float | None


class CohortCell(BaseModel):
    cohort_month: date
    cohort_customers: int
    month_offset: int
    active_customers: int
    retention: float


class GeoStateRow(BaseModel):
    state_code: str
    state_name: str
    region: str
    revenue: float
    estimated_profit: float
    estimated_margin: float | None
    orders: int
    customers: int
    avg_order_value: float | None
    revenue_previous: float
    revenue_growth: float | None
    avg_delivery_days: float | None
    late_delivery_rate: float | None
    avg_review_score: float | None
    latitude: float | None
    longitude: float | None


class GeoCityRow(BaseModel):
    state_code: str
    city: str
    revenue: float
    estimated_profit: float
    estimated_margin: float | None
    orders: int
    customers: int
    revenue_previous: float
    revenue_growth: float | None
    latitude: float | None
    longitude: float | None


class GeoResponse(BaseModel):
    filters: AppliedFilters
    rows: list[GeoStateRow] | list[GeoCityRow]


class Insight(BaseModel):
    kind: str
    tone: Literal["positive", "negative", "neutral"]
    title: str
    text: str
    source: str = Field(description="Endpoint or view the numbers come from")
    values: dict


class InsightResponse(BaseModel):
    filters: AppliedFilters
    insights: list[Insight]
