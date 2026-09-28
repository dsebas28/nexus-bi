"""Machine-learning outputs: segments, churn, forecast, anomalies and model evaluations."""
from __future__ import annotations

from typing import Any

from ..models.database import fetch_all, fetch_one
from .cache import cached

CHURN_SORT = {"probability": "churn_probability DESC", "value": "monetary DESC NULLS LAST"}


@cached
def active_runs() -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT model_name, algorithm, reference_date, trained_at, params, metrics, notes
        FROM ml.model_runs WHERE is_active ORDER BY model_name
    """)


@cached
def run(model_name: str) -> dict[str, Any] | None:
    return fetch_one("""
        SELECT model_name, algorithm, reference_date, trained_at, params, metrics, notes
        FROM ml.model_runs WHERE is_active AND model_name = %(m)s
    """, {"m": model_name})


@cached
def evaluations(model_name: str) -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT candidate, is_selected, split, metric, value
        FROM analytics.v_model_evaluations
        WHERE model_name = %(m)s
        ORDER BY split, candidate, metric
    """, {"m": model_name})


# --- Segmentation -----------------------------------------------------------------

@cached
def segments() -> list[dict[str, Any]]:
    return fetch_all("SELECT * FROM analytics.v_segment_summary ORDER BY display_order")


@cached
def segment_customers(segment: str, limit: int, offset: int) -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT customer_key, left(customer_uid, 8) AS customer_short_id, state_code, city, segment,
               recency_days, frequency, monetary, r_score, f_score, m_score, rfm_code
        FROM analytics.v_customer_segments
        WHERE segment = %(segment)s
        ORDER BY monetary DESC, customer_key
        LIMIT %(limit)s OFFSET %(offset)s
    """, {"segment": segment, "limit": limit, "offset": offset})


# --- Churn -------------------------------------------------------------------------

@cached
def churn_bands() -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT risk_band,
               count(*)                              AS customers,
               round(avg(churn_probability), 5)      AS avg_churn_probability,
               round(min(churn_probability), 5)      AS min_churn_probability,
               round(max(churn_probability), 5)      AS max_churn_probability,
               round(sum(monetary), 2)               AS lifetime_revenue
        FROM analytics.v_churn_scores
        GROUP BY risk_band
        ORDER BY avg_churn_probability DESC
    """)


@cached
def churn_customers(risk_band: str | None, segment: str | None, state: str | None, sort_by: str,
                    limit: int, offset: int) -> list[dict[str, Any]]:
    return fetch_all(f"""
        SELECT customer_key, left(customer_uid, 8) AS customer_short_id, state_code, city, segment,
               churn_probability, risk_band, top_factors, recency_days, frequency, monetary
        FROM analytics.v_churn_scores
        WHERE (%(risk_band)s::text IS NULL OR risk_band = %(risk_band)s)
          AND (%(segment)s::text IS NULL OR segment = %(segment)s)
          AND (%(state)s::text IS NULL OR state_code = %(state)s)
        ORDER BY {CHURN_SORT[sort_by]}, customer_key
        LIMIT %(limit)s OFFSET %(offset)s
    """, {"risk_band": risk_band, "segment": segment, "state": state, "limit": limit, "offset": offset})


@cached
def churn_factor_frequency(risk_band: str) -> list[dict[str, Any]]:
    """How often each feature appears among the top risk factors of a band."""
    return fetch_all("""
        SELECT f ->> 'feature'                                   AS feature,
               count(*)                                          AS customers,
               round(avg((f ->> 'impact_pts')::numeric), 2)      AS avg_impact_pts
        FROM analytics.v_churn_scores, jsonb_array_elements(top_factors) AS f
        WHERE risk_band = %(band)s
        GROUP BY 1
        ORDER BY customers DESC
    """, {"band": risk_band})


# --- Forecast ----------------------------------------------------------------------

@cached
def forecast(history_days: int) -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT day, kind, revenue, lower_80, upper_80
        FROM analytics.v_sales_forecast
        WHERE kind = 'forecast'
           OR day >= (SELECT reference_date FROM analytics.v_reporting_period) - %(days)s
        ORDER BY day
    """, {"days": history_days})


# --- Anomalies ---------------------------------------------------------------------

@cached
def anomalies(entity_type: str | None, direction: str | None, limit: int) -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT anomaly_id, entity_type, entity_key, entity_label, period_start, period_end, metric,
               observed, expected, score, direction, method, description
        FROM analytics.v_anomalies
        WHERE (%(entity_type)s::text IS NULL OR entity_type = %(entity_type)s)
          AND (%(direction)s::text IS NULL OR direction = %(direction)s)
        ORDER BY score DESC, anomaly_id
        LIMIT %(limit)s
    """, {"entity_type": entity_type, "direction": direction, "limit": limit})


@cached
def anomaly_counts() -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT entity_type, method, direction, count(*) AS anomalies
        FROM analytics.v_anomalies GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """)
