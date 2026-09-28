"""Shared ML infrastructure: database I/O, run bookkeeping and the reporting period."""
from __future__ import annotations

import io
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RANDOM_STATE = 42
RUNS_TO_KEEP = 3   # older inactive runs per model are pruned

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        load_dotenv(PROJECT_ROOT / ".env")
        _engine = create_engine(URL.create(
            "postgresql+psycopg",
            username=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ["POSTGRES_HOST"],
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.environ["POSTGRES_DB"],
        ))
    return _engine


def query(sql: str, **params: Any) -> pd.DataFrame:
    with engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def reporting_period() -> tuple[date, date, date]:
    """(first_month, last_month, reference_date) of the complete-data window."""
    row = query("SELECT first_month, last_month, reference_date FROM analytics.v_reporting_period").iloc[0]
    return row["first_month"], row["last_month"], row["reference_date"]


def artifact_dir(model_name: str) -> Path:
    path = Path(__file__).resolve().parent / model_name / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda v: v.item() if hasattr(v, "item") else str(v)))


class ModelRun:
    """Writes one training run and its outputs in a single transaction.

    Usage::

        with ModelRun("churn", "XGBoost", reference_date) as run:
            run.params = {...}; run.metrics = {...}
            run.add_evaluations(df)            # candidate, split, metric, value
            run.write("ml.churn_scores", scores_df)

    On success the run becomes the active run for the model and old runs are
    pruned. On error everything is rolled back.
    """

    def __init__(self, model_name: str, algorithm: str, reference_date: date, notes: str | None = None):
        self.model_name, self.algorithm, self.reference_date, self.notes = model_name, algorithm, reference_date, notes
        self.params: dict[str, Any] = {}
        self.metrics: dict[str, Any] = {}
        self.run_id: int | None = None

    def __enter__(self) -> "ModelRun":
        self._raw = engine().raw_connection()
        self._conn = self._raw.driver_connection          # psycopg connection (transaction open)
        self.run_id = self._conn.execute(
            "INSERT INTO ml.model_runs (model_name, algorithm, reference_date, notes) "
            "VALUES (%s, %s, %s, %s) RETURNING run_id",
            (self.model_name, self.algorithm, self.reference_date, self.notes),
        ).fetchone()[0]
        return self

    def write(self, table: str, df: pd.DataFrame) -> None:
        """Bulk-insert ``df`` (without run_id) into ``table`` tagged with this run."""
        df = df.copy()
        df.insert(0, "run_id", self.run_id)
        for col in df.columns:
            if df[col].map(lambda v: isinstance(v, (dict, list))).any():
                df[col] = df[col].map(lambda v: json.dumps(_json_safe(v)))
        buffer = io.StringIO()
        df.to_csv(buffer, index=False, header=False, na_rep="", date_format="%Y-%m-%d")
        with self._conn.cursor().copy(
                f"COPY {table} ({', '.join(df.columns)}) FROM STDIN WITH (FORMAT csv)") as copy:
            copy.write(buffer.getvalue())
        log.info("  wrote %s rows to %s", f"{len(df):,}", table)

    def add_evaluations(self, evaluations: pd.DataFrame) -> None:
        cols = ["candidate", "split", "metric", "value"]
        ev = evaluations[cols].copy()
        ev["value"] = ev["value"].astype(float).replace([np.inf, -np.inf], np.nan)
        self.write("ml.model_evaluations", ev)

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is not None:
                self._conn.rollback()
                return
            with self._conn.cursor() as cur:
                cur.execute("UPDATE ml.model_runs SET is_active = FALSE WHERE model_name = %s AND is_active",
                            (self.model_name,))
                cur.execute(
                    "UPDATE ml.model_runs SET params = %s, metrics = %s, is_active = TRUE WHERE run_id = %s",
                    (json.dumps(_json_safe(self.params)), json.dumps(_json_safe(self.metrics)), self.run_id))
                cur.execute(
                    """DELETE FROM ml.model_runs
                       WHERE model_name = %s AND run_id NOT IN (
                           SELECT run_id FROM ml.model_runs WHERE model_name = %s
                           ORDER BY run_id DESC LIMIT %s)""",
                    (self.model_name, self.model_name, RUNS_TO_KEEP))
            self._conn.commit()
            log.info("  run %d is now the active '%s' model", self.run_id, self.model_name)
        finally:
            self._raw.close()
