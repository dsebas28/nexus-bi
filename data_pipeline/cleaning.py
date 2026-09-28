"""Cleaning: repair, remove or flag every data problem, and record the decision.

Each ``clean_*`` function receives a raw DataFrame of strings and returns a
typed DataFrame. Nothing is changed without a matching entry in the
:class:`~data_pipeline.validation.QualityReport`, so every number in the
data-quality report is traceable to a rule in this module.

Policy
------
* **removed**   the row cannot be trusted or loaded (bad ids, exact duplicates,
                coordinates outside Brazil, orphans).
* **corrected** the true value is unambiguous (lost leading zeros, typos, 0 installments).
* **imputed**   a missing value is replaced from another reliable source.
* **nulled**    the value is impossible and the true value is unknown.
* **flagged**   the value is suspicious but plausible, so it is kept and reported.
"""
from __future__ import annotations

import pandas as pd

from .validation import (
    BRAZIL_STATES,
    QualityReport,
    invalid_uid_mask,
    outside_brazil_mask,
    parse_numbers,
    parse_timestamps,
    robust_outlier_mask,
)

ORDER_STATUSES = frozenset({"created", "approved", "invoiced", "processing",
                            "shipped", "delivered", "canceled", "unavailable"})
PAYMENT_TYPES = frozenset({"credit_card", "boleto", "voucher", "debit_card", "not_defined"})
UNCATEGORIZED = "sem_categoria"

# Typos present in Olist's own English category names.
CATEGORY_NAME_FIXES = {"costruction": "construction", "fashio_": "fashion_", "confort": "comfort"}

_CITY_SEPARATOR = r"\s*(?:/|\\|,|\(|\s-\s)\s*"
_CITY_STATE_SUFFIX = r"-(?:" + "|".join(sorted(s.lower() for s in BRAZIL_STATES)) + r")$"
# Must start with a letter; digits are allowed inside (e.g. 'quilometro 14 do mutum').
_VALID_CITY = r"[a-z][a-z0-9' -]*[a-z0-9]"


# -----------------------------------------------------------------------------
# Shared helpers
# -----------------------------------------------------------------------------

def normalize_city(values: pd.Series) -> pd.Series:
    """Canonical city name: ASCII, lowercase, single spaces, no state suffix.

    'São Paulo' -> 'sao paulo', 'lages - sc' -> 'lages', 'auriflama/sp' -> 'auriflama',
    "santa barbara d´oeste" -> "santa barbara d'oeste". Hyphenated names such as
    'mogi-guacu' are preserved.
    """
    out = values.fillna("").str.replace("[´`’]", "'", regex=True)
    out = out.str.normalize("NFKD").str.encode("ascii", "ignore").str.decode("ascii")
    out = out.str.lower().str.split(_CITY_SEPARATOR, n=1, regex=True).str[0]
    out = out.str.replace(_CITY_STATE_SUFFIX, "", regex=True)
    return out.str.replace(r"\s+", " ", regex=True).str.strip()


def invalid_city_mask(city: pd.Series) -> pd.Series:
    """True for values that are not a plausible city name (numbers, e-mails, 'sp')."""
    return ~city.str.fullmatch(_VALID_CITY).fillna(False) | (city.str.len() < 3)


def _drop(df: pd.DataFrame, mask: pd.Series, report: QualityReport, table: str,
          check: str, category: str, severity: str | None = None, **details) -> pd.DataFrame:
    report.log(table, check, category, int(mask.sum()), "removed", severity, **details)
    return df.loc[~mask]


def _drop_invalid_uids(df: pd.DataFrame, columns: list[str], report: QualityReport,
                       table: str) -> pd.DataFrame:
    mask = pd.Series(False, index=df.index)
    for col in columns:
        mask |= invalid_uid_mask(df[col])
    severity = "error" if mask.any() else "info"
    return _drop(df, mask, report, table, "invalid_id_format", "format", severity, columns=columns)


def _drop_duplicates(df: pd.DataFrame, key: list[str] | None, report: QualityReport,
                     table: str) -> pd.DataFrame:
    mask = df.duplicated(subset=key, keep="first")
    check = "duplicate_rows" if key is None else "duplicate_key"
    return _drop(df, mask, report, table, check, "duplicate", key=key or "all columns")


def _pad_zip(df: pd.DataFrame, col: str, report: QualityReport, table: str) -> pd.DataFrame:
    """Restore leading zeros lost when the source was exported as numbers."""
    raw = df[col].str.strip()
    digits = raw.str.fullmatch(r"\d{1,5}").fillna(False)
    df = _drop(df, ~digits, report, table, "invalid_zip_code", "invalid_value")
    raw = raw[digits]
    report.log(table, "zip_leading_zeros_lost", "format", int((raw.str.len() < 5).sum()), "corrected")
    df = df.copy()
    df[col] = raw.str.zfill(5)
    return df


def _drop_invalid_states(df: pd.DataFrame, col: str, report: QualityReport,
                         table: str) -> pd.DataFrame:
    df = df.copy()
    df[col] = df[col].str.strip().str.upper()
    return _drop(df, ~df[col].isin(BRAZIL_STATES), report, table, "invalid_state", "invalid_value")


def _clean_city(df: pd.DataFrame, col: str, zip_col: str, zip_to_city: pd.Series,
                report: QualityReport, table: str) -> pd.DataFrame:
    """Normalise city names and impute unusable ones from the zip-code prefix."""
    df = df.copy()
    normalized = normalize_city(df[col])
    changed = normalized != df[col].fillna("")
    report.log(table, "city_name_format", "format", int(changed.sum()), "corrected",
               examples=df.loc[changed, col].drop_duplicates().head(5).tolist())

    invalid = invalid_city_mask(normalized)
    from_zip = df.loc[invalid, zip_col].map(zip_to_city)
    report.log(table, "city_name_invalid", "invalid_value", int(from_zip.notna().sum()), "imputed",
               examples=df.loc[invalid, col].head(5).tolist(), source="modal city of zip prefix")
    unresolved = from_zip.isna()
    report.log(table, "city_name_unresolved", "missing", int(unresolved.sum()), "imputed",
               severity="warning" if unresolved.any() else "info", value="unknown")
    normalized.loc[invalid] = from_zip.fillna("unknown")
    df[col] = normalized
    return df


# -----------------------------------------------------------------------------
# Table-level cleaning
# -----------------------------------------------------------------------------

def clean_geolocation(raw: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
    t = "geolocation"
    df = _drop_duplicates(raw, None, report, t)
    df = _pad_zip(df, "geolocation_zip_code_prefix", report, t)

    lat, lat_err = parse_numbers(df["geolocation_lat"])
    lng, lng_err = parse_numbers(df["geolocation_lng"])
    df = df.assign(geolocation_lat=lat, geolocation_lng=lng)
    df = _drop(df, lat.isna() | lng.isna(), report, t, "coordinates_unparseable", "format",
               unparseable=lat_err + lng_err)
    df = _drop(df, outside_brazil_mask(df["geolocation_lat"], df["geolocation_lng"]), report, t,
               "coordinates_outside_brazil", "outlier")
    df = _drop_invalid_states(df, "geolocation_state", report, t)

    city = normalize_city(df["geolocation_city"])
    report.log(t, "city_name_format", "format", int((city != df["geolocation_city"]).sum()), "corrected")
    return df.assign(geolocation_city=city)


def zip_city_lookup(geolocation: pd.DataFrame) -> pd.Series:
    """Most frequent (modal) valid city name per zip prefix in the geolocation data."""
    geo = geolocation[~invalid_city_mask(geolocation["geolocation_city"])]
    counts = geo.groupby(["geolocation_zip_code_prefix", "geolocation_city"]).size()
    modal = counts.sort_values(ascending=False).reset_index().drop_duplicates("geolocation_zip_code_prefix")
    return modal.set_index("geolocation_zip_code_prefix")["geolocation_city"]


def clean_customers(raw: pd.DataFrame, zip_to_city: pd.Series, report: QualityReport) -> pd.DataFrame:
    t = "customers"
    df = _drop_invalid_uids(raw, ["customer_id", "customer_unique_id"], report, t)
    df = _drop_duplicates(df, ["customer_id"], report, t)
    df = _pad_zip(df, "customer_zip_code_prefix", report, t)
    df = _drop_invalid_states(df, "customer_state", report, t)
    return _clean_city(df, "customer_city", "customer_zip_code_prefix", zip_to_city, report, t)


def clean_sellers(raw: pd.DataFrame, zip_to_city: pd.Series, report: QualityReport) -> pd.DataFrame:
    t = "sellers"
    df = _drop_invalid_uids(raw, ["seller_id"], report, t)
    df = _drop_duplicates(df, ["seller_id"], report, t)
    df = _pad_zip(df, "seller_zip_code_prefix", report, t)
    df = _drop_invalid_states(df, "seller_state", report, t)
    return _clean_city(df, "seller_city", "seller_zip_code_prefix", zip_to_city, report, t)


def clean_category_translation(raw: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
    t = "category_translation"
    df = _drop_duplicates(raw, ["product_category_name"], report, t)
    english = df["product_category_name_english"].str.strip()
    fixed = english
    for wrong, right in CATEGORY_NAME_FIXES.items():
        fixed = fixed.str.replace(wrong, right, regex=False)
    report.log(t, "english_name_typos", "format", int((fixed != english).sum()), "corrected",
               examples=english[fixed != english].tolist())
    return df.assign(product_category_name=df["product_category_name"].str.strip(),
                     product_category_name_english=fixed)


def clean_products(raw: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
    t = "products"
    df = _drop_invalid_uids(raw, ["product_id"], report, t)
    df = _drop_duplicates(df, ["product_id"], report, t)
    df = df.rename(columns={"product_name_lenght": "product_name_length",
                            "product_description_lenght": "product_description_length"})

    numeric = ["product_name_length", "product_description_length", "product_photos_qty",
               "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]
    df = df.copy()
    for col in numeric:
        values, errors = parse_numbers(df[col])
        report.log(t, f"{col}_unparseable", "format", errors, "nulled")
        # Physical measures and description sizes must be strictly positive.
        # (photos_qty = 0 would be legitimate, so it is not checked here.)
        if col != "product_photos_qty":
            non_positive = values <= 0
            report.log(t, f"{col}_not_positive", "invalid_value", int(non_positive.sum()), "nulled")
            values = values.mask(non_positive)
        df[col] = values.round().astype("Int64")

    report.log(t, "physical_attributes_missing", "missing",
               int(df[["product_weight_g", "product_length_cm"]].isna().any(axis=1).sum()), "kept")

    no_category = df["product_category_name"].isna()
    report.log(t, "category_missing", "missing", int(no_category.sum()), "imputed", value=UNCATEGORIZED)
    df["product_category_name"] = df["product_category_name"].fillna(UNCATEGORIZED).str.strip()
    return df


def clean_orders(raw: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
    t = "orders"
    df = _drop_invalid_uids(raw, ["order_id", "customer_id"], report, t)
    df = _drop_duplicates(df, ["order_id"], report, t)
    df = _drop(df, ~df["order_status"].isin(ORDER_STATUSES), report, t, "invalid_status", "invalid_value")

    df = df.copy()
    ts_cols = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
               "order_delivered_customer_date", "order_estimated_delivery_date"]
    for col in ts_cols:
        parsed, errors = parse_timestamps(df[col])
        report.log(t, f"{col}_unparseable", "format", errors, "nulled")
        df[col] = parsed
    df["order_estimated_delivery_date"] = df["order_estimated_delivery_date"].dt.normalize()

    purchase = df["order_purchase_timestamp"]
    df = _drop(df, purchase.isna(), report, t, "purchase_timestamp_missing", "missing", "error")
    df = _drop(df, df["order_estimated_delivery_date"].isna()
               | (df["order_estimated_delivery_date"] < purchase.dt.normalize()),
               report, t, "estimated_delivery_invalid", "invalid_date")
    purchase = df["order_purchase_timestamp"]

    # Events cannot happen before the purchase: the true value is unknown, so null it.
    for col in ["order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date"]:
        before = df[col] < purchase
        report.log(t, f"{col}_before_purchase", "invalid_date", int(before.sum()), "nulled")
        df.loc[before, col] = pd.NaT

    # Plausible operational lags or data-entry order issues: keep but report.
    report.log(t, "carrier_pickup_before_approval", "invalid_date",
               int((df["order_delivered_carrier_date"] < df["order_approved_at"]).sum()), "flagged")
    report.log(t, "customer_delivery_before_carrier_pickup", "invalid_date",
               int((df["order_delivered_customer_date"] < df["order_delivered_carrier_date"]).sum()),
               "flagged")

    delivered = df["order_status"] == "delivered"
    has_date = df["order_delivered_customer_date"].notna()
    report.log(t, "delivered_without_delivery_date", "inconsistency", int((delivered & ~has_date).sum()),
               "flagged")
    report.log(t, "delivery_date_but_not_delivered", "inconsistency", int((~delivered & has_date).sum()),
               "flagged", statuses=df.loc[~delivered & has_date, "order_status"].value_counts().to_dict())

    no_approval = df["order_approved_at"].isna()
    report.log(t, "approval_timestamp_missing", "missing", int(no_approval.sum()), "kept",
               statuses=df.loc[no_approval, "order_status"].value_counts().to_dict())
    return df


def clean_order_items(raw: pd.DataFrame, products: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
    t = "order_items"
    df = _drop_invalid_uids(raw, ["order_id", "product_id", "seller_id"], report, t)
    df = _drop_duplicates(df, ["order_id", "order_item_id"], report, t)

    df = df.copy()
    for col in ["order_item_id", "price", "freight_value"]:
        values, errors = parse_numbers(df[col])
        df[col] = values
        df = _drop(df, df[col].isna(), report, t, f"{col}_unparseable", "format", unparseable=errors)
    df["order_item_id"] = df["order_item_id"].astype("Int64")
    df["shipping_limit_date"], errors = parse_timestamps(df["shipping_limit_date"])
    df = _drop(df, df["shipping_limit_date"].isna(), report, t, "shipping_limit_unparseable", "format",
               unparseable=errors)

    df = _drop(df, df["order_item_id"] < 1, report, t, "line_number_invalid", "invalid_value")
    df = _drop(df, df["price"] <= 0, report, t, "price_not_positive", "invalid_value")
    df = _drop(df, df["freight_value"] < 0, report, t, "freight_negative", "invalid_value")

    # Expensive items are legitimate sales, so outliers are flagged, not removed.
    category = df["product_id"].map(products.set_index("product_id")["product_category_name"])
    price_out = robust_outlier_mask(df["price"], category)
    freight_out = robust_outlier_mask(df["freight_value"], category)
    report.log(t, "price_outlier_within_category", "outlier", int(price_out.sum()), "flagged",
               method="modified z-score on log(price) by category, |z| > 3.5")
    report.log(t, "freight_outlier_within_category", "outlier", int(freight_out.sum()), "flagged",
               method="modified z-score on log(freight) by category, |z| > 3.5")
    return df


def clean_payments(raw: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
    t = "payments"
    df = _drop_invalid_uids(raw, ["order_id"], report, t)
    df = _drop_duplicates(df, ["order_id", "payment_sequential"], report, t)
    df = _drop(df, ~df["payment_type"].isin(PAYMENT_TYPES), report, t, "invalid_payment_type",
               "invalid_value")

    df = df.copy()
    for col in ["payment_sequential", "payment_installments", "payment_value"]:
        values, errors = parse_numbers(df[col])
        df[col] = values
        df = _drop(df, df[col].isna(), report, t, f"{col}_unparseable", "format", unparseable=errors)
    df = _drop(df, df["payment_value"] < 0, report, t, "payment_value_negative", "invalid_value")

    # A payment always has at least one installment.
    zero = df["payment_installments"] == 0
    report.log(t, "zero_installments", "invalid_value", int(zero.sum()), "corrected", value=1)
    df.loc[zero, "payment_installments"] = 1
    df = _drop(df, df["payment_installments"] > 24, report, t, "installments_above_24", "invalid_value")

    report.log(t, "payment_type_not_defined", "inconsistency",
               int((df["payment_type"] == "not_defined").sum()), "flagged")
    report.log(t, "zero_value_payment", "invalid_value", int((df["payment_value"] == 0).sum()), "kept",
               types=df.loc[df["payment_value"] == 0, "payment_type"].value_counts().to_dict())

    df["payment_sequential"] = df["payment_sequential"].astype("Int64")
    df["payment_installments"] = df["payment_installments"].astype("Int64")
    return df


def clean_reviews(raw: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
    t = "reviews"
    df = _drop_invalid_uids(raw, ["review_id", "order_id"], report, t)
    df = _drop_duplicates(df, ["review_id", "order_id"], report, t)

    df = df.copy()
    score, errors = parse_numbers(df["review_score"])
    df["review_score"] = score
    df = _drop(df, ~df["review_score"].between(1, 5), report, t, "score_out_of_range", "invalid_value",
               unparseable=errors)
    df["review_score"] = df["review_score"].astype("Int64")

    for col in ["review_creation_date", "review_answer_timestamp"]:
        parsed, errors = parse_timestamps(df[col])
        report.log(t, f"{col}_unparseable", "format", errors, "nulled")
        df[col] = parsed
    df = _drop(df, df["review_creation_date"].isna(), report, t, "creation_date_missing", "missing")
    answered_before = df["review_answer_timestamp"] < df["review_creation_date"]
    report.log(t, "answered_before_created", "invalid_date", int(answered_before.sum()), "nulled")
    df.loc[answered_before, "review_answer_timestamp"] = pd.NaT

    for col in ["review_comment_title", "review_comment_message"]:
        stripped = df[col].str.strip()
        blank = stripped.eq("").fillna(False)
        report.log(t, f"{col}_blank", "format", int(blank.sum()), "corrected", value="NULL")
        df[col] = stripped.mask(blank)
    return df


# -----------------------------------------------------------------------------
# Cross-table rules
# -----------------------------------------------------------------------------

def _enforce_references(c: dict[str, pd.DataFrame], report: QualityReport) -> None:
    """Remove orphan rows, cascading from parents to children."""
    def keep_if(table: str, column: str, parents: pd.Series, parent_name: str) -> None:
        orphan = ~c[table][column].isin(parents)
        c[table] = _drop(c[table], orphan, report, table, f"orphan_{column}", "referential",
                         "error" if orphan.any() else "info", parent=parent_name)

    keep_if("orders", "customer_id", c["customers"]["customer_id"], "customers")
    keep_if("order_items", "order_id", c["orders"]["order_id"], "orders")
    keep_if("order_items", "product_id", c["products"]["product_id"], "products")
    keep_if("order_items", "seller_id", c["sellers"]["seller_id"], "sellers")
    keep_if("payments", "order_id", c["orders"]["order_id"], "orders")
    keep_if("reviews", "order_id", c["orders"]["order_id"], "orders")


def _cross_table_checks(c: dict[str, pd.DataFrame], report: QualityReport) -> None:
    orders, items, payments = c["orders"], c["order_items"], c["payments"]

    no_items = ~orders["order_id"].isin(items["order_id"])
    report.log("orders", "order_without_items", "inconsistency", int(no_items.sum()), "kept",
               statuses=orders.loc[no_items, "order_status"].value_counts().to_dict())
    report.log("orders", "order_without_payments", "inconsistency",
               int((~orders["order_id"].isin(payments["order_id"])).sum()), "kept")

    item_total = items.groupby("order_id")[["price", "freight_value"]].sum().sum(axis=1)
    paid = payments.groupby("order_id")["payment_value"].sum()
    gap = (paid - item_total).dropna().abs()
    report.log("payments", "paid_differs_from_items_plus_freight", "inconsistency",
               int((gap > 1.0).sum()), "flagged", tolerance_brl=1.0)

    purchase_day = c["reviews"]["order_id"].map(
        orders.set_index("order_id")["order_purchase_timestamp"]).dt.normalize()
    report.log("reviews", "review_created_before_purchase", "invalid_date",
               int((c["reviews"]["review_creation_date"] < purchase_day).sum()), "flagged")


def clean_all(frames: dict[str, pd.DataFrame], report: QualityReport) -> dict[str, pd.DataFrame]:
    geolocation = clean_geolocation(frames["geolocation"], report)
    zip_to_city = zip_city_lookup(geolocation)
    products = clean_products(frames["products"], report)
    cleaned = {
        "geolocation": geolocation,
        "customers": clean_customers(frames["customers"], zip_to_city, report),
        "sellers": clean_sellers(frames["sellers"], zip_to_city, report),
        "category_translation": clean_category_translation(frames["category_translation"], report),
        "products": products,
        "orders": clean_orders(frames["orders"], report),
        "order_items": clean_order_items(frames["order_items"], products, report),
        "payments": clean_payments(frames["payments"], report),
        "reviews": clean_reviews(frames["reviews"], report),
    }
    _enforce_references(cleaned, report)
    _cross_table_checks(cleaned, report)
    return cleaned
