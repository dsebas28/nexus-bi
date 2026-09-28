"""Validation: detectors for data problems and the data-quality report.

Detectors here only *find* problems (they return boolean masks or counts).
Deciding what to do about them is the job of ``cleaning.py``, which records
every decision in a :class:`QualityReport`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

UID_PATTERN = r"[0-9a-f]{32}"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

BRAZIL_STATES = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
})

# Bounding box of Brazil's territory, used to reject impossible coordinates.
BRAZIL_BBOX = {"lat_min": -33.76, "lat_max": 5.28, "lng_min": -73.99, "lng_max": -34.79}

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "customers": ["customer_id", "customer_unique_id", "customer_zip_code_prefix",
                  "customer_city", "customer_state"],
    "geolocation": ["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
                    "geolocation_city", "geolocation_state"],
    "order_items": ["order_id", "order_item_id", "product_id", "seller_id",
                    "shipping_limit_date", "price", "freight_value"],
    "payments": ["order_id", "payment_sequential", "payment_type",
                 "payment_installments", "payment_value"],
    "reviews": ["review_id", "order_id", "review_score", "review_comment_title",
                "review_comment_message", "review_creation_date", "review_answer_timestamp"],
    "orders": ["order_id", "customer_id", "order_status", "order_purchase_timestamp",
               "order_approved_at", "order_delivered_carrier_date",
               "order_delivered_customer_date", "order_estimated_delivery_date"],
    "products": ["product_id", "product_category_name", "product_name_lenght",
                 "product_description_lenght", "product_photos_qty", "product_weight_g",
                 "product_length_cm", "product_height_cm", "product_width_cm"],
    "sellers": ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"],
    "category_translation": ["product_category_name", "product_category_name_english"],
}


# -----------------------------------------------------------------------------
# Data-quality report
# -----------------------------------------------------------------------------

@dataclass
class Issue:
    source_table: str
    check_name: str
    category: str       # missing | duplicate | invalid_value | invalid_date | outlier | inconsistency | format | referential
    severity: str       # info | warning | error
    rows_affected: int
    action_taken: str   # removed | imputed | corrected | nulled | flagged | kept
    details: dict[str, Any] = field(default_factory=dict)


class QualityReport:
    """Collects every check the pipeline runs and what was done about it."""

    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self.rows_read: dict[str, int] = {}
        self.rows_loaded: dict[str, int] = {}

    def log(self, source_table: str, check_name: str, category: str, rows_affected: int,
            action_taken: str, severity: str | None = None, **details: Any) -> None:
        rows = int(rows_affected)
        if severity is None:
            severity = "info" if rows == 0 else "warning"
        # Round-trip through JSON so numpy scalars become plain Python values (JSONB-safe).
        details = json.loads(json.dumps(details, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
        self.issues.append(Issue(source_table, check_name, category, severity, rows,
                                 action_taken, details))

    def total(self, category: str, action: str | None = None) -> int:
        return sum(i.rows_affected for i in self.issues
                   if i.category == category and (action is None or i.action_taken == action))

    def summary(self) -> dict[str, int]:
        return {
            "rows_processed": sum(self.rows_read.values()),
            "duplicates_removed": self.total("duplicate", "removed"),
            "missing_values_handled": self.total("missing"),
            "invalid_dates": self.total("invalid_date"),
            "outliers_detected": self.total("outlier"),
            "format_errors_corrected": self.total("format", "corrected"),
            "inconsistencies_flagged": self.total("inconsistency"),
            "final_records": sum(self.rows_loaded.values()),
        }

    def render(self) -> str:
        s = self.summary()
        width = 76
        lines = [
            "",
            "=" * width,
            "  DATA QUALITY REPORT",
            "=" * width,
            f"  Rows processed            {s['rows_processed']:>12,}",
            f"  Duplicates removed        {s['duplicates_removed']:>12,}",
            f"  Missing values handled    {s['missing_values_handled']:>12,}",
            f"  Invalid dates             {s['invalid_dates']:>12,}",
            f"  Outliers detected         {s['outliers_detected']:>12,}",
            f"  Format errors corrected   {s['format_errors_corrected']:>12,}",
            f"  Inconsistencies flagged   {s['inconsistencies_flagged']:>12,}",
            f"  Final records             {s['final_records']:>12,}",
            "-" * width,
            f"  {'Check':<56}{'Rows':>9}  Action",
            "-" * width,
        ]
        for i in self.issues:
            if i.rows_affected:
                label = f"{i.source_table}.{i.check_name}"
                lines.append(f"  {label[:56]:<56}{i.rows_affected:>9,}  {i.action_taken}")
        passed = sum(1 for i in self.issues if i.rows_affected == 0)
        lines += ["-" * width, f"  {len(self.issues)} checks run, {passed} found no problems.", "=" * width]
        if self.rows_loaded:
            lines.append("  Rows loaded per table:")
            lines += [f"    {t:<22}{n:>10,}" for t, n in self.rows_loaded.items()]
            lines.append("=" * width)
        return "\n".join(lines)


# -----------------------------------------------------------------------------
# Detectors
# -----------------------------------------------------------------------------

def check_required_columns(frames: dict[str, pd.DataFrame]) -> None:
    """Fail fast if the source files do not have the expected structure."""
    problems = []
    for name, columns in REQUIRED_COLUMNS.items():
        if name not in frames:
            problems.append(f"missing table '{name}'")
            continue
        absent = sorted(set(columns) - set(frames[name].columns))
        if absent:
            problems.append(f"{name}: missing columns {absent}")
    if problems:
        raise ValueError("Raw data does not match the expected schema: " + "; ".join(problems))


def invalid_uid_mask(values: pd.Series) -> pd.Series:
    """True where a value is not a 32-character lowercase hex id."""
    return ~values.fillna("").str.fullmatch(UID_PATTERN)


def parse_timestamps(values: pd.Series) -> tuple[pd.Series, int]:
    """Parse ``YYYY-MM-DD HH:MM:SS`` strings. Returns (parsed, n_unparseable).

    Missing values stay missing and are not counted as errors.
    """
    parsed = pd.to_datetime(values, format=TIMESTAMP_FORMAT, errors="coerce")
    n_errors = int((values.notna() & parsed.isna()).sum())
    return parsed, n_errors


def parse_numbers(values: pd.Series) -> tuple[pd.Series, int]:
    """Convert text to float. Returns (parsed, n_unparseable)."""
    parsed = pd.to_numeric(values, errors="coerce")
    n_errors = int((values.notna() & parsed.isna()).sum())
    return parsed, n_errors


def outside_brazil_mask(lat: pd.Series, lng: pd.Series) -> pd.Series:
    b = BRAZIL_BBOX
    return ~(lat.between(b["lat_min"], b["lat_max"]) & lng.between(b["lng_min"], b["lng_max"]))


def robust_outlier_mask(values: pd.Series, groups: pd.Series | None = None,
                        threshold: float = 3.5) -> pd.Series:
    """Flag outliers with the modified z-score (Iglewicz & Hoaglin) on log values.

    Prices are right-skewed, so the score is computed on ``log1p``. When ``groups``
    is given the median and MAD are computed within each group, so an expensive
    computer is compared with other computers rather than with cheap accessories.
    Groups whose MAD is 0 have no outliers.
    """
    x = np.log1p(values.astype(float))
    if groups is None:
        groups = pd.Series(0, index=values.index)
    median = x.groupby(groups).transform("median")
    mad = (x - median).abs().groupby(groups).transform("median")
    with np.errstate(divide="ignore", invalid="ignore"):
        score = 0.6745 * (x - median) / mad
    return (score.abs() > threshold) & (mad > 0)


def check_referential_integrity(model: dict[str, pd.DataFrame]) -> list[str]:
    """Verify every foreign key of the transformed model resolves before loading.

    Loading resolves surrogate keys with inner joins, so an orphan row would be
    dropped silently. Returns a list of problems (empty when consistent).
    """
    loc_key = ["zip_code_prefix", "city", "state_code"]
    locations = model["locations"].set_index(loc_key).index
    rules = [
        ("locations", "state_code", set(BRAZIL_STATES), model["locations"]["state_code"]),
        ("products", "category_name_pt", set(model["categories"]["category_name_pt"]),
         model["products"]["category_name_pt"]),
        ("orders", "customer_uid", set(model["customers"]["customer_uid"]),
         model["orders"]["customer_uid"]),
        ("orders", "purchase_date", set(model["calendar"]["date_key"]),
         model["orders"]["purchased_at"].dt.normalize()),
        ("order_items", "order_uid", set(model["orders"]["order_uid"]),
         model["order_items"]["order_uid"]),
        ("order_items", "product_uid", set(model["products"]["product_uid"]),
         model["order_items"]["product_uid"]),
        ("order_items", "seller_uid", set(model["sellers"]["seller_uid"]),
         model["order_items"]["seller_uid"]),
        ("order_payments", "order_uid", set(model["orders"]["order_uid"]),
         model["order_payments"]["order_uid"]),
        ("order_reviews", "order_uid", set(model["orders"]["order_uid"]),
         model["order_reviews"]["order_uid"]),
    ]
    problems = []
    for table, column, parents, values in rules:
        orphans = int((~values.isin(parents)).sum())
        if orphans:
            problems.append(f"{table}.{column}: {orphans} values without parent")

    for table, prefix in (("customers", ""), ("sellers", ""), ("orders", "delivery_")):
        cols = [f"{prefix}{c}" if prefix else c for c in loc_key]
        idx = model[table].set_index(cols).index
        orphans = int((~idx.isin(locations)).sum())
        if orphans:
            problems.append(f"{table}.location: {orphans} addresses without location")
    return problems
