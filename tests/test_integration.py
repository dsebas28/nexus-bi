"""Integration tests against the loaded PostgreSQL database and the running API app.

They check invariants that must hold for any correct load of the data, not
hard-coded results: totals reconcile with the raw files, KPI definitions agree
across views, the API validates input, and the analyst never states an
unverified figure in guided mode.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.integration
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


# ---------------------------------------------------------------- data and SQL

def test_revenue_reconciles_with_the_raw_file(db):
    """Every item price in the CSV is loaded, to the cent."""
    raw_file = RAW / "olist_order_items_dataset.csv"
    if not raw_file.exists():
        pytest.skip("raw CSV not downloaded")
    raw_total = round(pd.read_csv(raw_file, usecols=["price"])["price"].sum(), 2)
    loaded = db.execute("SELECT sum(unit_price) FROM core.order_items").fetchone()[0]
    assert float(loaded) == pytest.approx(raw_total, abs=0.01)


def test_one_customer_per_unique_id(db):
    raw_file = RAW / "olist_customers_dataset.csv"
    if not raw_file.exists():
        pytest.skip("raw CSV not downloaded")
    unique_people = pd.read_csv(raw_file, usecols=["customer_unique_id"])["customer_unique_id"].nunique()
    assert db.execute("SELECT count(*) FROM core.customers").fetchone()[0] == unique_people


def test_reporting_period_is_consistent(db):
    first_month, last_month, reference, data_start, data_end = db.execute(
        "SELECT first_month, last_month, reference_date, data_start, data_end FROM analytics.v_reporting_period").fetchone()
    assert first_month.day == 1 and last_month.day == 1
    assert data_start <= first_month <= last_month < reference
    assert reference == data_end + pd.Timedelta(days=1).to_pytimedelta()


def test_kpi_summary_agrees_with_monthly_kpis(db):
    """Regression: the KPI cards and the monthly view once used different comparison windows."""
    month, growth = db.execute("""
        SELECT month, revenue_growth_mom FROM analytics.v_monthly_kpis
        WHERE month = (SELECT last_month FROM analytics.v_reporting_period)""").fetchone()
    change_pct = db.execute("""
        SELECT change_pct FROM analytics.kpi_summary(%s, (%s::date + interval '1 month - 1 day')::date)
        WHERE metric = 'revenue'""", (month, month)).fetchone()[0]
    assert float(change_pct) == pytest.approx(float(growth) * 100, abs=0.01)


@pytest.mark.parametrize("start, end, prev_start, prev_end", [
    ("2018-07-01", "2018-07-31", "2018-06-01", "2018-06-30"),      # whole month -> previous calendar month
    ("2018-04-01", "2018-06-30", "2018-01-01", "2018-03-31"),      # quarter -> previous quarter
    ("2018-07-10", "2018-07-20", "2018-06-29", "2018-07-09"),      # arbitrary range -> previous N days
])
def test_previous_period(db, start, end, prev_start, prev_end):
    row = db.execute("SELECT prev_start::text, prev_end::text FROM analytics.previous_period(%s, %s)", (start, end)).fetchone()
    assert row == (prev_start, prev_end)


def test_growth_is_null_when_the_comparison_month_is_incomplete(db):
    """Regression: YoY growth against a near-empty month once showed +6,808,925%."""
    rows = db.execute("""
        SELECT count(*) FROM analytics.v_monthly_kpis m
        JOIN analytics.v_month_coverage c ON c.month = (m.month - interval '12 months')::date
        WHERE NOT c.is_complete AND m.revenue_growth_yoy IS NOT NULL""").fetchone()[0]
    assert rows == 0


def test_every_active_model_has_outputs(db):
    counts = dict(db.execute("""
        SELECT r.model_name,
               CASE r.model_name
                   WHEN 'rfm_segmentation' THEN (SELECT count(*) FROM ml.customer_segments s WHERE s.run_id = r.run_id)
                   WHEN 'churn' THEN (SELECT count(*) FROM ml.churn_scores s WHERE s.run_id = r.run_id)
                   WHEN 'sales_forecast' THEN (SELECT count(*) FROM ml.sales_forecast s WHERE s.run_id = r.run_id)
                   WHEN 'anomaly_detection' THEN (SELECT count(*) FROM ml.anomalies s WHERE s.run_id = r.run_id)
               END
        FROM ml.model_runs r WHERE r.is_active""").fetchall())
    assert set(counts) == {"rfm_segmentation", "churn", "sales_forecast", "anomaly_detection"}
    assert all(n > 0 for n in counts.values())


def test_segments_partition_the_customer_base(db):
    seg, base = db.execute("""
        SELECT (SELECT count(*) FROM analytics.v_customer_segments), (SELECT count(*) FROM analytics.v_customer_summary)
    """).fetchone()
    assert seg == base


# ---------------------------------------------------------------- API

@pytest.mark.parametrize("path", [
    "/api/v1/health", "/api/v1/meta", "/api/v1/kpis", "/api/v1/insights", "/api/v1/sales/monthly",
    "/api/v1/sales/categories", "/api/v1/sales/heatmap", "/api/v1/products/top", "/api/v1/products/low-rotation",
    "/api/v1/customers/overview", "/api/v1/customers/cohorts", "/api/v1/geo/states", "/api/v1/geo/cities",
    "/api/v1/segments", "/api/v1/churn/summary", "/api/v1/churn/customers", "/api/v1/forecast",
    "/api/v1/anomalies", "/api/v1/models", "/api/v1/data-quality", "/api/v1/ai/status",
])
def test_endpoints_respond(api_client, path):
    assert api_client.get(path).status_code == 200


def test_kpi_cards_default_to_last_complete_month(api_client, db):
    last_month = db.execute("SELECT last_month::text FROM analytics.v_reporting_period").fetchone()[0]
    body = api_client.get("/api/v1/kpis").json()
    assert body["filters"]["start"] == last_month
    assert [k["metric"] for k in body["kpis"]] == [
        "revenue", "estimated_profit", "estimated_margin", "orders", "customers", "avg_order_value"]
    assert {k["metric"] for k in body["kpis"] if k["is_estimated"]} == {"estimated_profit", "estimated_margin"}


@pytest.mark.parametrize("query, status", [
    ("state=XX", 422), ("start=2018-05-01&end=2018-01-01", 422), ("start=2018-05-01", 422),
    ("category=9999", 422), ("state=S%27P", 422), ("start=2010-01-01&end=2018-01-01", 422),
])
def test_kpi_filters_are_validated(api_client, query, status):
    r = api_client.get(f"/api/v1/kpis?{query}")
    assert r.status_code == status and r.json()["error"]["code"] == "invalid_request"


def test_sort_parameter_cannot_inject_sql(api_client):
    r = api_client.get("/api/v1/products/top?sort_by=revenue;DROP TABLE core.orders")
    assert r.status_code == 422


def test_unknown_customer_is_404(api_client):
    assert api_client.get("/api/v1/customers/999999999").status_code == 404


def test_security_headers(api_client):
    headers = api_client.get("/api/v1/health").headers
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"


def test_filtered_revenue_is_a_subset(api_client):
    total = api_client.get("/api/v1/kpis").json()["kpis"][0]["current_value"]
    sp = api_client.get("/api/v1/kpis?state=SP").json()["kpis"][0]["current_value"]
    assert 0 < sp < total


# ---------------------------------------------------------------- AI analyst (guided mode: free, deterministic)

def test_guided_answers_have_only_verified_figures():
    from ai_analyst import guided

    for item in guided.LIBRARY:
        result = guided.ask(item.question)
        assert result.status == "answered", item.id
        assert result.steps and all(s["error"] is None for s in result.steps), item.id
        assert result.unverified_numbers == [], (item.id, result.unverified_numbers)


def test_guided_mode_declines_questions_outside_its_library():
    from ai_analyst import guided

    result = guided.ask("¿Cuál es la capital de Francia?")
    assert result.status == "cannot_answer" and not result.steps


def test_ai_endpoint_in_guided_mode(api_client):
    r = api_client.post("/api/v1/ai/ask", json={"question": "¿Qué estados generan más ventas?", "mode": "guided"})
    body = r.json()
    assert r.status_code == 200 and body["mode"] == "guided" and body["status"] == "answered"
    assert body["unverified_numbers"] == [] and body["steps"][0]["sql"].lower().startswith("select")
