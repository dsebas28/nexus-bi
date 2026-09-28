from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from ..schemas.ml import (AnomalyResponse, ChurnCustomer, ChurnSummary, ForecastResponse, ModelDetail, RiskBand,
                          SegmentCustomer, SegmentName, SegmentResponse)
from ..services import ml

router = APIRouter(tags=["Machine learning"])

MODEL_NAMES = Literal["rfm_segmentation", "sales_forecast", "churn", "anomaly_detection"]

CHURN_METRICS = {
    "roc_auc": "Probability that a customer who returned is ranked above one who churned (0.5 = random, 1 = perfect).",
    "pr_auc_returning": "Average precision when searching for returning customers; compare with the return rate.",
    "precision": "Of the customers predicted in a class, the share that truly belong to it.",
    "recall": "Of the customers truly in a class, the share the model found.",
    "f1": "Harmonic mean of precision and recall.",
    "brier": "Mean squared error of the probabilities (lower is better).",
    "confusion_matrix": "tp/fn/fp/tn with churn as the positive class.",
}


def _model_detail(model_name: str) -> ModelDetail:
    run = ml.run(model_name)
    if run is None:
        raise HTTPException(status_code=404,
                            detail=f"No active '{model_name}' model. Run: python -m machine_learning")
    return ModelDetail(**run, evaluations=ml.evaluations(model_name))


@router.get("/models", response_model=list[ModelDetail], summary="Active models with all candidate evaluations")
def get_models():
    return [_model_detail(r["model_name"]) for r in ml.active_runs()]


@router.get("/models/{model_name}", response_model=ModelDetail, summary="One active model and its evaluations")
def get_model(model_name: MODEL_NAMES):
    return _model_detail(model_name)


@router.get("/segments", response_model=SegmentResponse, summary="RFM segments: size, value, rules and recommendations")
def get_segments():
    run = ml.run("rfm_segmentation")
    if run is None:
        raise HTTPException(status_code=404, detail="No active segmentation. Run: python -m machine_learning")
    return SegmentResponse(method=run["algorithm"], thresholds=run["params"], segments=ml.segments())


@router.get("/segments/{segment}/customers", response_model=list[SegmentCustomer],
            summary="Customers of a segment, highest spend first")
def get_segment_customers(segment: SegmentName, limit: int = Query(50, ge=1, le=500),
                          offset: int = Query(0, ge=0, le=1_000_000)):
    return ml.segment_customers(segment, limit, offset)


@router.get("/churn/summary", response_model=ChurnSummary,
            summary="Churn model metrics, risk bands and most common high-risk factors")
def get_churn_summary():
    return ChurnSummary(model=_model_detail("churn"), bands=ml.churn_bands(),
                        high_risk_factors=ml.churn_factor_frequency("High"), metric_definitions=CHURN_METRICS)


@router.get("/churn/customers", response_model=list[ChurnCustomer],
            summary="Customers ranked by churn probability or by value, with risk factors")
def get_churn_customers(risk_band: RiskBand | None = None, segment: SegmentName | None = None,
                        state: str | None = Query(None, pattern="^[A-Za-z]{2}$"),
                        sort_by: Literal["probability", "value"] = "probability",
                        limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0, le=1_000_000)):
    return ml.churn_customers(risk_band, segment, state.upper() if state else None, sort_by, limit, offset)


@router.get("/forecast", response_model=ForecastResponse,
            summary="Recent daily revenue, 8-week forecast and 80% interval")
def get_forecast(history_days: int = Query(120, ge=7, le=730)):
    series = ml.forecast(history_days)
    return ForecastResponse(
        model=_model_detail("sales_forecast"), series=series,
        forecast_total=round(sum(p["revenue"] for p in series if p["kind"] == "forecast"), 2),
        disclaimer=("Forecasts are estimates. The 80% interval comes from the model's own backtest errors "
                    "and is indicative, not a guarantee."))


@router.get("/anomalies", response_model=AnomalyResponse, summary="Detected anomalies, strongest first")
def get_anomalies(entity_type: Literal["day", "product", "customer"] | None = None,
                  direction: Literal["up", "down", "mixed"] | None = None,
                  limit: int = Query(50, ge=1, le=500)):
    return AnomalyResponse(counts=ml.anomaly_counts(), anomalies=ml.anomalies(entity_type, direction, limit))
