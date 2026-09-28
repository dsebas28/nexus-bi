"""Build the whole database from scratch, in the right order (used by Docker and for fresh installs).

    python -m data_pipeline.setup_db            # skip if the data and models are already there
    python -m data_pipeline.setup_db --force    # rebuild everything

Order matters: schema -> data pipeline -> analytics views (materialized views need data)
-> machine-learning models (they read the views).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import load

log = logging.getLogger("setup_db")
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def apply_sql(name: str) -> None:
    """Run a SQL file. Plain SQL only (no psql meta-commands), so it works without psql installed."""
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    with load.connect(autocommit=True) as conn:
        conn.execute(sql)
    log.info("Applied sql/%s", name)


def is_built() -> bool:
    try:
        with load.connect() as conn:
            row = conn.execute("""
                SELECT (SELECT count(*) FROM core.orders),
                       (SELECT count(*) FROM pg_matviews WHERE schemaname = 'analytics'),
                       (SELECT count(*) FROM ml.model_runs WHERE is_active)
            """).fetchone()
        return row[0] > 0 and row[1] > 0 and row[2] == 4
    except Exception:
        return False


def wait_for_database(timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with load.connect() as conn:
                conn.execute("SELECT 1")
            return
        except Exception as exc:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Database not reachable after {timeout}s: {exc}") from exc
            time.sleep(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the NEXUS BI database")
    parser.add_argument("--force", action="store_true", help="rebuild even if data is already loaded")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)

    wait_for_database()
    if is_built() and not args.force:
        log.info("Database already built (data, views and 4 active models). Use --force to rebuild.")
        return 0

    from data_pipeline.__main__ import main as run_pipeline
    from machine_learning.__main__ import main as run_models

    apply_sql("schema.sql")
    run_pipeline([])
    apply_sql("views.sql")
    run_models([])
    log.info("Database built.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
