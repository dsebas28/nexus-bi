"""Transformation: turn cleaned source tables into the normalised NEXUS BI model.

Output tables use natural keys (``*_uid`` and address triples). Surrogate keys
are assigned by PostgreSQL during loading.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

import pandas as pd

from .cleaning import UNCATEGORIZED
from .validation import QualityReport

LOCATION_KEY = ["zip_code_prefix", "city", "state_code"]

# Portuguese names missing from Olist's translation file.
MISSING_TRANSLATIONS = {
    "pc_gamer": "pc_gamer",
    "portateis_cozinha_e_preparadores_de_alimentos": "portable_kitchen_food_preparers",
    UNCATEGORIZED: "uncategorized",
}

# -----------------------------------------------------------------------------
# SYNTHETIC COST MODEL
# Olist does not publish product costs. To demonstrate profit and margin
# analysis, each category receives an ASSUMED cost-of-goods / price ratio, chosen
# in typical retail ranges (electronics are low-margin, fashion and beauty are
# high-margin). These values are NOT Olist data, and every profit or margin in
# NEXUS BI is labelled as estimated. Rules are matched in order on the English
# category name; the first match wins.
# -----------------------------------------------------------------------------
COST_RATIO_RULES: list[tuple[tuple[str, ...], float]] = [
    (("computers", "pc_gamer", "tablets", "telephony", "electronics", "consoles", "audio",
      "cine_photo", "dvds"), 0.80),
    (("appliances", "air_conditioning", "portable_kitchen"), 0.76),
    (("food", "drinks", "la_cuisine"), 0.72),
    (("books", "music", "cds"), 0.68),
    (("auto", "construction", "garden_tools", "home_construction", "signaling", "security",
      "agro_industry", "industry_commerce"), 0.66),
    (("furniture", "bed_bath", "housewares", "home_comfort", "kitchen", "office", "flowers"), 0.60),
    (("toys", "baby", "sports", "party", "christmas", "stationery", "art", "cool_stuff",
      "pet_shop", "market_place"), 0.58),
    (("health_beauty", "diapers", "perfumery"), 0.52),
    (("fashion", "watches", "luggage"), 0.48),
]
DEFAULT_COST_RATIO = 0.62
# Each product deviates from its category baseline by up to +/- this amount,
# deterministically (seeded by the product id), so products differ in margin.
PRODUCT_COST_JITTER = 0.06


def category_cost_ratio(category_name_en: str) -> float:
    for keywords, ratio in COST_RATIO_RULES:
        if any(k in category_name_en for k in keywords):
            return ratio
    return DEFAULT_COST_RATIO


def _stable_unit_interval(uid: str) -> float:
    """Deterministic number in [0, 1] derived from an id (same id -> same value)."""
    return int(hashlib.md5(uid.encode("ascii")).hexdigest()[:8], 16) / 0xFFFFFFFF


# -----------------------------------------------------------------------------
# Brazilian national holidays
# -----------------------------------------------------------------------------

def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Meeus/Jones/Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def brazil_holidays(year: int) -> dict[date, str]:
    """National holidays, plus Carnival and Corpus Christi (optional days observed nationwide)."""
    easter = easter_sunday(year)
    return {
        date(year, 1, 1): "New Year's Day",
        easter - timedelta(days=48): "Carnival Monday",
        easter - timedelta(days=47): "Carnival Tuesday",
        easter - timedelta(days=2): "Good Friday",
        date(year, 4, 21): "Tiradentes Day",
        date(year, 5, 1): "Labour Day",
        easter + timedelta(days=60): "Corpus Christi",
        date(year, 9, 7): "Independence Day",
        date(year, 10, 12): "Our Lady of Aparecida",
        date(year, 11, 2): "All Souls' Day",
        date(year, 11, 15): "Proclamation of the Republic",
        date(year, 12, 25): "Christmas Day",
    }


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------

def build_calendar(start: date, end: date) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    holidays: dict[date, str] = {}
    for year in range(start.year, end.year + 1):
        holidays.update(brazil_holidays(year))
    holiday_name = pd.Series([holidays.get(d.date()) for d in days], index=days)
    iso = days.isocalendar()
    return pd.DataFrame({
        "date_key": days,
        "year": days.year,
        "quarter": days.quarter,
        "month": days.month,
        "month_name": days.month_name(),
        "year_month": days.strftime("%Y-%m"),
        "iso_week": iso["week"].to_numpy(),
        "day_of_month": days.day,
        "day_of_week": days.dayofweek + 1,
        "day_name": days.day_name(),
        "is_weekend": days.dayofweek >= 5,
        "is_holiday": holiday_name.notna().to_numpy(),
        "holiday_name": holiday_name.to_numpy(),
    })


def build_locations(c: dict[str, pd.DataFrame], report: QualityReport) -> pd.DataFrame:
    """Distinct addresses of customers and sellers, with coordinates.

    Coordinates are the median of the geolocation points of the zip prefix. When
    a prefix has no points, the median of the city is used instead.
    """
    addresses = pd.concat([
        c["customers"][["customer_zip_code_prefix", "customer_city", "customer_state"]]
        .set_axis(LOCATION_KEY, axis=1),
        c["sellers"][["seller_zip_code_prefix", "seller_city", "seller_state"]]
        .set_axis(LOCATION_KEY, axis=1),
    ]).drop_duplicates().reset_index(drop=True)

    geo = c["geolocation"]
    by_zip = (geo.groupby("geolocation_zip_code_prefix")[["geolocation_lat", "geolocation_lng"]]
              .median().set_axis(["latitude", "longitude"], axis=1))
    by_city = (geo.groupby(["geolocation_city", "geolocation_state"])[["geolocation_lat", "geolocation_lng"]]
               .median().set_axis(["latitude", "longitude"], axis=1))

    loc = addresses.join(by_zip, on="zip_code_prefix")
    no_zip = loc["latitude"].isna()
    city_coords = loc.loc[no_zip, ["city", "state_code"]].join(by_city, on=["city", "state_code"])
    loc.loc[no_zip, ["latitude", "longitude"]] = city_coords[["latitude", "longitude"]].to_numpy()

    imputed = int(no_zip.sum() - loc["latitude"].isna().sum())
    report.log("locations", "coordinates_from_city_centroid", "missing", imputed, "imputed",
               reason="zip prefix has no geolocation points")
    report.log("locations", "coordinates_unavailable", "missing", int(loc["latitude"].isna().sum()), "kept")
    loc[["latitude", "longitude"]] = loc[["latitude", "longitude"]].round(6)
    return loc


def build_categories(products: pd.DataFrame, translation: pd.DataFrame,
                     report: QualityReport) -> pd.DataFrame:
    names = pd.Series(sorted(products["product_category_name"].unique()), name="category_name_pt")
    english = names.map(translation.set_index("product_category_name")["product_category_name_english"])
    missing = english.isna() & (names != UNCATEGORIZED)
    report.log("categories", "translation_missing", "missing", int(missing.sum()), "imputed",
               categories=names[missing].tolist())
    english = english.fillna(names.map(MISSING_TRANSLATIONS)).fillna(names)
    return pd.DataFrame({
        "category_name_pt": names,
        "category_name_en": english,
        "estimated_cost_ratio": english.map(category_cost_ratio),
    })


def build_products(products: pd.DataFrame, categories: pd.DataFrame) -> pd.DataFrame:
    base = products["product_category_name"].map(
        categories.set_index("category_name_pt")["estimated_cost_ratio"])
    jitter = products["product_id"].map(_stable_unit_interval).mul(2).sub(1).mul(PRODUCT_COST_JITTER)
    return pd.DataFrame({
        "product_uid": products["product_id"],
        "category_name_pt": products["product_category_name"],
        "name_length": products["product_name_length"],
        "description_length": products["product_description_length"],
        "photos_qty": products["product_photos_qty"],
        "weight_g": products["product_weight_g"],
        "length_cm": products["product_length_cm"],
        "height_cm": products["product_height_cm"],
        "width_cm": products["product_width_cm"],
        "estimated_cost_ratio": (base + jitter).clip(0.30, 0.92).round(3),
    }).reset_index(drop=True)


def _order_addresses(c: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per order: the real customer and the address used for that order."""
    customers = c["customers"].rename(columns={
        "customer_unique_id": "customer_uid", "customer_zip_code_prefix": "zip_code_prefix",
        "customer_city": "city", "customer_state": "state_code"})
    return c["orders"].merge(customers, on="customer_id", how="inner", validate="one_to_one")


def build_customers(c: dict[str, pd.DataFrame], report: QualityReport) -> pd.DataFrame:
    """A customer is a ``customer_unique_id``; their address is the one of their latest order."""
    orders = _order_addresses(c)
    n_addresses = orders.drop_duplicates(["customer_uid", *LOCATION_KEY]).groupby("customer_uid").size()
    report.log("customers", "customer_with_multiple_addresses", "inconsistency",
               int((n_addresses > 1).sum()), "kept", severity="info",
               resolution="customer keeps latest address; each order keeps its own")
    latest = (orders.sort_values("order_purchase_timestamp")
              .drop_duplicates("customer_uid", keep="last"))
    return latest[["customer_uid", *LOCATION_KEY]].reset_index(drop=True)


def build_orders(c: dict[str, pd.DataFrame]) -> pd.DataFrame:
    o = _order_addresses(c)
    return pd.DataFrame({
        "order_uid": o["order_id"],
        "customer_uid": o["customer_uid"],
        "delivery_zip_code_prefix": o["zip_code_prefix"],
        "delivery_city": o["city"],
        "delivery_state_code": o["state_code"],
        "order_status": o["order_status"],
        "purchased_at": o["order_purchase_timestamp"],
        "approved_at": o["order_approved_at"],
        "delivered_carrier_at": o["order_delivered_carrier_date"],
        "delivered_customer_at": o["order_delivered_customer_date"],
        "estimated_delivery_date": o["order_estimated_delivery_date"],
    })


def build_model(c: dict[str, pd.DataFrame], report: QualityReport) -> dict[str, pd.DataFrame]:
    """Return the load-ready tables, in foreign-key dependency order."""
    orders = build_orders(c)
    first = orders["purchased_at"].min().date()
    last = max(orders["purchased_at"].max(), orders["estimated_delivery_date"].max()).date()
    categories = build_categories(c["products"], c["category_translation"], report)

    items, payments, reviews = c["order_items"], c["payments"], c["reviews"]
    return {
        "calendar": build_calendar(date(first.year, 1, 1), date(last.year, 12, 31)),
        "locations": build_locations(c, report),
        "categories": categories,
        "products": build_products(c["products"], categories),
        "sellers": c["sellers"][["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"]]
        .set_axis(["seller_uid", *LOCATION_KEY], axis=1).reset_index(drop=True),
        "customers": build_customers(c, report),
        "orders": orders,
        "order_items": pd.DataFrame({
            "order_uid": items["order_id"],
            "line_number": items["order_item_id"],
            "product_uid": items["product_id"],
            "seller_uid": items["seller_id"],
            "shipping_limit_at": items["shipping_limit_date"],
            "unit_price": items["price"].round(2),
            "freight_value": items["freight_value"].round(2),
        }),
        "order_payments": pd.DataFrame({
            "order_uid": payments["order_id"],
            "payment_sequential": payments["payment_sequential"],
            "payment_type": payments["payment_type"],
            "installments": payments["payment_installments"],
            "amount": payments["payment_value"].round(2),
        }),
        "order_reviews": pd.DataFrame({
            "review_uid": reviews["review_id"],
            "order_uid": reviews["order_id"],
            "score": reviews["review_score"],
            "comment_title": reviews["review_comment_title"],
            "comment_message": reviews["review_comment_message"],
            "review_created_at": reviews["review_creation_date"],
            "review_answered_at": reviews["review_answer_timestamp"],
        }),
    }
