"""Response models for segmentation, churn, forecasting, anomalies, models and data quality."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SegmentName = Literal["VIP", "Loyal", "Potential", "At Risk", "Lost"]
RiskBand = Literal["High", "Medium", "Low"]


class Evaluation(BaseModel):
    candidate: str
    is_selected: bool
    split: str
    metric: str
    value: float | None


class ModelRun(BaseModel):
    model_name: str
    algorithm: str
    reference_date: date
    trained_at: datetime
    params: dict[str, Any] | None
    metrics: dict[str, Any] | None
    notes: str | None


class ModelDetail(ModelRun):
    evaluations: list[Evaluation]


class SegmentRow(BaseModel):
    segment: SegmentName
    display_order: int
    customers: int
    customer_share: float | None
    revenue: float
    revenue_share: float | None
    avg_revenue_per_customer: float | None
    avg_orders: float | None
    avg_recency_days: float | None
    rule: str
    description: str
    recommendation: str


class SegmentResponse(BaseModel):
    method: str
    thresholds: dict[str, Any]
    segments: list[SegmentRow]


class SegmentCustomer(BaseModel):
    customer_key: int
    customer_short_id: str
    state_code: str
    city: str
    segment: SegmentName
    recency_days: int
    frequency: int
    monetary: float
    r_score: int
    f_score: int
    m_score: int
    rfm_code: str


class RiskFactor(BaseModel):
    feature: str
    description: str
    impact_pts: float = Field(description="Churn-probability points added vs a typical customer")


class ChurnCustomer(BaseModel):
    customer_key: int
    customer_short_id: str
    state_code: str
    city: str
    segment: SegmentName | None
    churn_probability: float
    risk_band: RiskBand
    top_factors: list[RiskFactor]
    recency_days: int | None
    frequency: int | None
    monetary: float | None


class ChurnBand(BaseModel):
    risk_band: RiskBand
    customers: int
    avg_churn_probability: float
    min_churn_probability: float
    max_churn_probability: float
    lifetime_revenue: float | None


class ChurnSummary(BaseModel):
    model: ModelDetail
    bands: list[ChurnBand]
    high_risk_factors: list[dict[str, Any]]
    metric_definitions: dict[str, str]


class ForecastPoint(BaseModel):
    day: date
    kind: Literal["actual", "forecast"]
    revenue: float
    lower_80: float | None
    upper_80: float | None


class ForecastResponse(BaseModel):
    model: ModelDetail
    series: list[ForecastPoint]
    forecast_total: float
    disclaimer: str


class Anomaly(BaseModel):
    anomaly_id: int
    entity_type: Literal["day", "product", "customer"]
    entity_key: int | None
    entity_label: str | None
    period_start: date
    period_end: date
    metric: str
    observed: float | None
    expected: float | None
    score: float
    direction: Literal["up", "down", "mixed"]
    method: str
    description: str


class AnomalyResponse(BaseModel):
    counts: list[dict[str, Any]]
    anomalies: list[Anomaly]


class DataQualityIssue(BaseModel):
    source_table: str
    check_name: str
    category: str
    severity: str
    rows_affected: int
    action_taken: str
    details: dict[str, Any] | None


class DataQualityResponse(BaseModel):
    run_id: int
    finished_at: datetime
    rows_processed: int
    duplicates_removed: int
    missing_values_handled: int
    invalid_dates: int
    outliers_detected: int
    format_errors_corrected: int
    inconsistencies_flagged: int
    final_records: int
    checks_run: int
    checks_with_findings: int
    issues: list[DataQualityIssue]
