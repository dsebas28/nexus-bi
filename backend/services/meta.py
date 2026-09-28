"""Reference data: reporting period, filter options, data freshness and health."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..models.database import fetch_all, fetch_one
from .cache import cached


@cached
def reporting_period() -> dict[str, Any]:
    return fetch_one("SELECT * FROM analytics.v_reporting_period")


@cached
def previous_period(start: date, end: date) -> dict[str, date]:
    return fetch_one("SELECT * FROM analytics.previous_period(%(start)s, %(end)s)", {"start": start, "end": end})


@cached
def states() -> list[dict[str, Any]]:
    return fetch_all("SELECT state_code, state_name, region FROM core.states ORDER BY state_name")


@cached
def categories() -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT c.category_key, c.category_name_en AS category
        FROM core.categories c
        WHERE EXISTS (SELECT 1 FROM core.products p WHERE p.category_key = c.category_key)
        ORDER BY c.category_name_en
    """)


@cached
def state_exists(state: str) -> bool:
    return fetch_one("SELECT 1 AS ok FROM core.states WHERE state_code = %(s)s", {"s": state}) is not None


@cached
def category_exists(category_key: int) -> bool:
    return fetch_one("SELECT 1 AS ok FROM core.categories WHERE category_key = %(c)s", {"c": category_key}) is not None


@cached
def freshness() -> dict[str, Any]:
    return fetch_one("""
        SELECT (SELECT max(finished_at) FROM ops.pipeline_runs WHERE status = 'success') AS data_loaded_at,
               (SELECT max(trained_at) FROM ml.model_runs WHERE is_active)             AS models_trained_at
    """)


def health() -> dict[str, Any]:
    """Uncached: proves the database answers right now."""
    return fetch_one("SELECT current_setting('server_version') AS postgres_version, now() AS checked_at")
