"""Data-quality report of the latest successful pipeline run."""
from __future__ import annotations

from typing import Any

from ..models.database import fetch_all, fetch_one
from .cache import cached


@cached
def summary() -> dict[str, Any] | None:
    return fetch_one("""
        SELECT max(run_id)                                                           AS run_id,
               max(finished_at)                                                      AS finished_at,
               max(rows_read)                                                        AS rows_processed,
               max(rows_loaded)                                                      AS final_records,
               sum(rows_affected) FILTER (WHERE category = 'duplicate' AND action_taken = 'removed')
                                                                                     AS duplicates_removed,
               sum(rows_affected) FILTER (WHERE category = 'missing')                AS missing_values_handled,
               sum(rows_affected) FILTER (WHERE category = 'invalid_date')           AS invalid_dates,
               sum(rows_affected) FILTER (WHERE category = 'outlier')                AS outliers_detected,
               sum(rows_affected) FILTER (WHERE category = 'format' AND action_taken = 'corrected')
                                                                                     AS format_errors_corrected,
               sum(rows_affected) FILTER (WHERE category = 'inconsistency')          AS inconsistencies_flagged,
               count(*)                                                              AS checks_run,
               count(*) FILTER (WHERE rows_affected > 0)                             AS checks_with_findings
        FROM analytics.v_data_quality_latest
    """)


@cached
def issues() -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT source_table, check_name, category, severity, rows_affected, action_taken, details
        FROM analytics.v_data_quality_latest
        WHERE rows_affected > 0
        ORDER BY rows_affected DESC
    """)
