"""Unit tests for the AI analyst's guards: the SQL validator and the figure check.

These are the controls that stop a language model from running anything but a
read-only query over the documented views, and that expose invented numbers.
"""
from __future__ import annotations

import pytest

from ai_analyst.validation import SQLValidationError, extract_numbers, unverified_numbers, validate_sql

ACCEPTED = [
    "SELECT category, revenue FROM analytics.v_category_performance ORDER BY revenue DESC LIMIT 5",
    "WITH m AS (SELECT * FROM analytics.v_monthly_kpis WHERE is_complete) SELECT month, revenue FROM m;",
    "SELECT * FROM analytics.kpi_summary('2018-07-01', '2018-07-31')",
    "SELECT s.state_name, g.revenue FROM analytics.v_geo_state g JOIN core.states s USING (state_code)",
    "SELECT category FROM analytics.v_category_performance UNION SELECT category_name_en FROM core.categories",
]

REJECTED = {
    "DELETE FROM core.orders": "SELECT",
    "SELECT 1; DROP TABLE core.orders": "one SQL statement",
    "SELECT * FROM core.customers": "not available",
    "SELECT * FROM pg_catalog.pg_user": "not available",
    "SELECT * FROM information_schema.tables": "not available",
    "SELECT pg_sleep(10) FROM analytics.v_orders": "pg_sleep",
    "SELECT * INTO core.x FROM analytics.v_orders": "INTO",
    "SELECT * FROM analytics.v_orders FOR UPDATE": "LOCK",
    "SELECT * FROM v_orders": "schema-qualified",
    "SELECT set_config('default_transaction_read_only', 'off', false) FROM analytics.v_orders": "set_config",
    "SELECT pg_read_file('/etc/passwd') FROM analytics.v_orders": "pg_read_file",
    "WITH x AS (DELETE FROM core.orders RETURNING *) SELECT * FROM x": "DELETE",
    "SELECT * FROM dblink('host=evil', 'select 1') AS t(a int)": "dblink",
    "": "empty",
}


@pytest.mark.parametrize("sql", ACCEPTED)
def test_validator_accepts_read_only_queries_and_adds_a_limit(sql):
    v = validate_sql(sql)
    assert v.executable.startswith("SELECT * FROM (") and v.executable.rstrip().endswith("LIMIT 201")
    assert all(r.startswith(("analytics.", "core.")) for r in v.relations)


@pytest.mark.parametrize("sql, reason", REJECTED.items())
def test_validator_rejects_writes_catalogs_and_dangerous_functions(sql, reason):
    with pytest.raises(SQLValidationError, match=reason):
        validate_sql(sql)


def test_extract_numbers_reads_business_formats_and_ignores_ids():
    found = [n["text"] for n in extract_numbers("R$ 1.2M, 12.5%, 2,5 millones, product d6160fb7, customer 0a0a9211")]
    assert found == ["R$ 1.2M", "12.5%", "2,5 millones"]
    assert extract_numbers("R$ 1,234,567")[0]["value"] == 1234567


def test_grounded_figures_pass_and_invented_ones_are_flagged():
    results = [[[878044.27, 863265.53, 1.71], [0.387, 140.87]], [[6233]]]
    answer = ("Revenue was R$ 878,044 (+1.7%) vs R$ 863,266; margin 38.7%; average order R$ 140.87; "
              "6,233 orders. Growth was 55% and revenue R$ 1.2M.")
    assert unverified_numbers(answer, "", results) == ["55%", "R$ 1.2M"]


def test_structural_numbers_and_sql_constants_are_not_flagged():
    answer = "The top 5 cities in 2018 with at least R$ 20,000 of base revenue."
    assert unverified_numbers(answer, "", [[["x"]]], ["SELECT * FROM analytics.v_geo_city WHERE revenue_prev_6m >= 20000"]) == []
