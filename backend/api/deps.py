"""Global filters shared by every analytics endpoint, validated once here."""
from __future__ import annotations

import calendar
from datetime import date

from fastapi import HTTPException, Query

from ..schemas.common import AppliedFilters
from ..services import meta

MAX_RANGE_DAYS = 3 * 366


def default_period() -> tuple[date, date]:
    """Last complete month of reliable data."""
    last_month = meta.reporting_period()["last_month"]
    return last_month, last_month.replace(day=calendar.monthrange(last_month.year, last_month.month)[1])


def _invalid(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def filters(
    start: date | None = Query(None, description="Period start (YYYY-MM-DD). Default: last complete month."),
    end: date | None = Query(None, description="Period end, inclusive (YYYY-MM-DD)."),
    state: str | None = Query(None, min_length=2, max_length=2, pattern="^[A-Za-z]{2}$",
                              description="Brazilian state code, e.g. SP"),
    category: int | None = Query(None, ge=1, le=32767, description="category_key from /meta"),
) -> AppliedFilters:
    if start is None and end is None:
        start, end = default_period()
    elif start is None or end is None:
        raise _invalid("Provide both 'start' and 'end', or neither.")
    if start > end:
        raise _invalid("'start' must be on or before 'end'.")
    if (end - start).days > MAX_RANGE_DAYS:
        raise _invalid(f"The period cannot exceed {MAX_RANGE_DAYS} days.")
    if state is not None:
        state = state.upper()
        if not meta.state_exists(state):
            raise _invalid(f"Unknown state '{state}'. See /api/v1/meta for valid codes.")
    if category is not None and not meta.category_exists(category):
        raise _invalid(f"Unknown category_key {category}. See /api/v1/meta for valid keys.")
    previous = meta.previous_period(start, end)
    return AppliedFilters(start=start, end=end, previous_start=previous["prev_start"],
                          previous_end=previous["prev_end"], state=state, category_key=category)
