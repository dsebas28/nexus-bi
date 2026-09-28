"""Shared fixtures.

Unit tests need nothing but the code. Tests marked ``integration`` need the
PostgreSQL database built by ``python -m data_pipeline`` (plus sql/views.sql and
``python -m machine_learning``); they are skipped automatically when it is not
reachable, so the unit suite always runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _database_ready() -> bool:
    try:
        from data_pipeline.load import connect

        with connect() as conn:
            return conn.execute("SELECT count(*) FROM analytics.v_orders").fetchone()[0] > 0
    except Exception:
        return False


DB_READY = _database_ready()


def pytest_collection_modifyitems(config, items):
    if DB_READY:
        return
    skip = pytest.mark.skip(reason="PostgreSQL with NEXUS BI data is not reachable")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def api_client():
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def db():
    from data_pipeline.load import connect

    with connect(autocommit=True) as conn:
        yield conn
