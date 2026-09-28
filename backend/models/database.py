"""PostgreSQL access: a connection pool whose sessions are read-only.

Every API session runs with ``default_transaction_read_only = on`` and a
statement timeout, so even a faulty query cannot modify data or hang a worker.
NUMERIC values are loaded as float, which is JSON-friendly and precise enough
for reporting.
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader
from psycopg_pool import ConnectionPool

from ..config import get_settings

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def _configure(conn: psycopg.Connection) -> None:
    settings = get_settings()
    conn.adapters.register_loader("numeric", FloatLoader)
    conn.execute("SET default_transaction_read_only = on")
    conn.execute(f"SET statement_timeout = {int(settings.db_statement_timeout_ms)}")
    conn.commit()


def open_pool() -> None:
    global _pool
    settings = get_settings()
    _pool = ConnectionPool(
        settings.database_conninfo,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        kwargs={"row_factory": dict_row, "autocommit": True},
        configure=_configure,
        open=True,
        name="nexus_api",
    )
    log.info("Database pool opened (%s-%s connections)", settings.db_pool_min_size, settings.db_pool_max_size)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def pool() -> ConnectionPool:
    if _pool is None:
        open_pool()
    return _pool  # type: ignore[return-value]


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with pool().connection() as conn:
        return conn.execute(sql, params or {}).fetchall()


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    with pool().connection() as conn:
        return conn.execute(sql, params or {}).fetchone()
