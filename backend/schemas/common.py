"""Shared response models: periods, filters, metadata and errors."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class AppliedFilters(BaseModel):
    start: date
    end: date
    previous_start: date = Field(description="Start of the comparison period")
    previous_end: date = Field(description="End of the comparison period")
    state: str | None = None
    category_key: int | None = None


class ReportingPeriod(BaseModel):
    data_start: date = Field(description="First day of reliable data")
    data_end: date = Field(description="Last day of reliable data")
    first_month: date = Field(description="First complete month")
    last_month: date = Field(description="Last complete month")
    reference_date: date = Field(description="Date treated as 'today' by the analytics")


class StateOption(BaseModel):
    state_code: str
    state_name: str
    region: str


class CategoryOption(BaseModel):
    category_key: int
    category: str


class ModelInfo(BaseModel):
    model_name: str
    algorithm: str
    reference_date: date
    trained_at: datetime


class Freshness(BaseModel):
    data_loaded_at: datetime | None
    models_trained_at: datetime | None


class MetaResponse(BaseModel):
    reporting_period: ReportingPeriod
    default_filters: AppliedFilters
    states: list[StateOption]
    categories: list[CategoryOption]
    models: list[ModelInfo]
    freshness: Freshness
    estimated_metrics_note: str


class HealthResponse(BaseModel):
    status: str
    database: str
    postgres_version: str | None = None
    checked_at: datetime | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
