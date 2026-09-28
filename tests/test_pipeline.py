"""Unit tests for the data pipeline: detectors, cleaning rules and transformations.

Each test uses a handful of rows that reproduce a problem found in the real Olist
files (see docs/architecture.md) and checks both the fix and that the fix is
recorded in the data-quality report.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from data_pipeline import cleaning, transformation, validation
from data_pipeline.validation import QualityReport


def issue(report: QualityReport, check_name: str):
    return next(i for i in report.issues if i.check_name == check_name)


# ---------------------------------------------------------------- detectors

def test_parse_timestamps_counts_malformed_but_not_missing():
    values = pd.Series(["2018-01-02 10:00:00", "not a date", None, "2018-02-30 00:00:00"])
    parsed, errors = validation.parse_timestamps(values)
    assert errors == 2                        # 'not a date' and the impossible Feb 30
    assert parsed.notna().sum() == 1


def test_invalid_uid_mask():
    ids = pd.Series(["0" * 32, "ABCDEF" + "0" * 26, "123", None])
    assert validation.invalid_uid_mask(ids).tolist() == [False, True, True, True]


def test_outside_brazil_mask():
    lat = pd.Series([-23.55, 45.06, -3.1])
    lng = pd.Series([-46.63, 121.1, -60.0])
    assert validation.outside_brazil_mask(lat, lng).tolist() == [False, True, False]


def test_robust_outliers_are_judged_within_their_group():
    prices = pd.Series([10, 11, 12, 10, 11, 900, 1000, 1100, 1050, 1000, 5000.0])
    groups = pd.Series(["a"] * 5 + ["b"] * 6)
    flags = validation.robust_outlier_mask(prices, groups)
    # 900-1100 are normal for group b; only 5000 is extreme within its own group.
    assert flags.tolist() == [False] * 10 + [True]


def test_required_columns_fail_fast():
    frames = {name: pd.DataFrame(columns=cols) for name, cols in validation.REQUIRED_COLUMNS.items()}
    validation.check_required_columns(frames)                      # complete: no error
    frames["orders"] = frames["orders"].drop(columns=["order_status"])
    with pytest.raises(ValueError, match="order_status"):
        validation.check_required_columns(frames)


def test_quality_report_summary_adds_up_by_category():
    r = QualityReport()
    r.rows_read = {"a": 100, "b": 50}
    r.log("a", "dups", "duplicate", 7, "removed")
    r.log("a", "blank", "missing", 3, "imputed")
    r.log("b", "late", "invalid_date", 2, "flagged")
    r.log("b", "clean", "format", 0, "corrected")
    s = r.summary()
    assert (s["rows_processed"], s["duplicates_removed"], s["missing_values_handled"], s["invalid_dates"]) == (150, 7, 3, 2)
    assert issue(r, "clean").severity == "info" and issue(r, "dups").severity == "warning"


# ---------------------------------------------------------------- cleaning

@pytest.mark.parametrize("raw, expected", [
    ("São Paulo", "sao paulo"),
    ("lages - sc", "lages"),
    ("auriflama/sp", "auriflama"),
    ("rio de janeiro, rio de janeiro, brasil", "rio de janeiro"),
    ("santa barbara d´oeste", "santa barbara d'oeste"),
    ("andira-pr", "andira"),
    ("mogi-guacu", "mogi-guacu"),                 # real hyphenated name is preserved
    ("sao  jose dos pinhais", "sao jose dos pinhais"),
])
def test_normalize_city(raw, expected):
    assert cleaning.normalize_city(pd.Series([raw])).iloc[0] == expected


def test_invalid_city_mask_keeps_real_names_with_digits():
    cities = pd.Series(["quilometro 14 do mutum", "4482255", "vendas@creditparts.com.br", "sp", "recife"])
    assert cleaning.invalid_city_mask(cities).tolist() == [False, True, True, True, False]


def test_zip_codes_recover_leading_zeros():
    report = QualityReport()
    df = cleaning._pad_zip(pd.DataFrame({"zip": ["9790", "14409", "1003", "abc"]}), "zip", report, "customers")
    assert df["zip"].tolist() == ["09790", "14409", "01003"]
    assert issue(report, "zip_leading_zeros_lost").rows_affected == 2
    assert issue(report, "invalid_zip_code").rows_affected == 1


def test_clean_geolocation_removes_duplicates_and_points_outside_brazil():
    raw = pd.DataFrame({
        "geolocation_zip_code_prefix": ["01037", "01037", "01037", "1046"],
        "geolocation_lat": ["-23.5", "-23.5", "45.06", "-23.54"],
        "geolocation_lng": ["-46.6", "-46.6", "121.1", "-46.64"],
        "geolocation_city": ["são paulo", "são paulo", "sao paulo", "sao paulo"],
        "geolocation_state": ["SP", "SP", "SP", "SP"],
    })
    report = QualityReport()
    out = cleaning.clean_geolocation(raw, report)
    assert len(out) == 2
    assert issue(report, "duplicate_rows").rows_affected == 1
    assert issue(report, "coordinates_outside_brazil").rows_affected == 1
    assert set(out["geolocation_city"]) == {"sao paulo"}


def _orders(**overrides):
    row = {
        "order_id": "a" * 32, "customer_id": "b" * 32, "order_status": "delivered",
        "order_purchase_timestamp": "2018-01-10 10:00:00", "order_approved_at": "2018-01-10 11:00:00",
        "order_delivered_carrier_date": "2018-01-11 09:00:00", "order_delivered_customer_date": "2018-01-15 12:00:00",
        "order_estimated_delivery_date": "2018-01-25 00:00:00",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_carrier_pickup_before_purchase_is_nulled_and_logged():
    report = QualityReport()
    out = cleaning.clean_orders(_orders(order_delivered_carrier_date="2018-01-09 08:00:00"), report)
    assert pd.isna(out["order_delivered_carrier_date"].iloc[0])
    assert issue(report, "order_delivered_carrier_date_before_purchase").rows_affected == 1


def test_plausible_but_odd_dates_are_flagged_not_changed():
    report = QualityReport()
    out = cleaning.clean_orders(_orders(order_delivered_carrier_date="2018-01-10 10:30:00"), report)  # before approval
    assert out["order_delivered_carrier_date"].notna().iloc[0]
    assert issue(report, "carrier_pickup_before_approval").rows_affected == 1


def test_invalid_status_is_removed():
    report = QualityReport()
    out = cleaning.clean_orders(_orders(order_status="lost_in_space"), report)
    assert out.empty and issue(report, "invalid_status").rows_affected == 1


def test_zero_installments_are_corrected_to_one():
    raw = pd.DataFrame({"order_id": ["a" * 32, "b" * 32], "payment_sequential": ["1", "1"],
                        "payment_type": ["credit_card", "voucher"], "payment_installments": ["0", "1"],
                        "payment_value": ["10.5", "0"]})
    report = QualityReport()
    out = cleaning.clean_payments(raw, report)
    assert out["payment_installments"].tolist() == [1, 1]
    assert issue(report, "zero_installments").rows_affected == 1
    assert issue(report, "zero_value_payment").rows_affected == 1       # kept: a 100% voucher is legitimate


def test_products_missing_category_and_zero_weight():
    raw = pd.DataFrame({
        "product_id": ["c" * 32, "d" * 32], "product_category_name": [None, "perfumaria"],
        "product_name_lenght": ["40", "50"], "product_description_lenght": ["300", "200"],
        "product_photos_qty": ["1", "0"], "product_weight_g": ["0", "500"],
        "product_length_cm": ["10", "20"], "product_height_cm": ["5", "5"], "product_width_cm": ["10", "10"],
    })
    report = QualityReport()
    out = cleaning.clean_products(raw, report)
    assert out["product_category_name"].tolist() == [cleaning.UNCATEGORIZED, "perfumaria"]
    assert pd.isna(out["product_weight_g"].iloc[0])
    assert out["product_photos_qty"].iloc[1] == 0                     # zero photos is valid, not nulled


# ---------------------------------------------------------------- transformation

@pytest.mark.parametrize("year, easter", [(2017, date(2017, 4, 16)), (2018, date(2018, 4, 1)), (2024, date(2024, 3, 31))])
def test_easter_sunday(year, easter):
    assert transformation.easter_sunday(year) == easter


def test_brazil_holidays_2017():
    h = transformation.brazil_holidays(2017)
    assert h[date(2017, 2, 28)] == "Carnival Tuesday"
    assert h[date(2017, 4, 14)] == "Good Friday"
    assert h[date(2017, 6, 15)] == "Corpus Christi"
    assert h[date(2017, 11, 15)] == "Proclamation of the Republic"


def test_calendar_dimension():
    cal = transformation.build_calendar(date(2017, 11, 20), date(2017, 11, 26))
    friday = cal[cal["date_key"] == "2017-11-24"].iloc[0]
    assert friday["day_of_week"] == 5 and not friday["is_weekend"]
    assert cal["is_weekend"].sum() == 2 and len(cal) == 7


def test_synthetic_cost_model_is_deterministic_and_bounded():
    assert transformation.category_cost_ratio("computers_accessories") == 0.80
    assert transformation.category_cost_ratio("fashion_shoes") == 0.48
    assert transformation.category_cost_ratio("something_new") == transformation.DEFAULT_COST_RATIO
    products = pd.DataFrame({"product_id": [f"{i:032x}" for i in range(200)], "product_category_name": "x",
                             **{c: pd.array([1] * 200, dtype="Int64") for c in [
                                 "product_name_length", "product_description_length", "product_photos_qty",
                                 "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]}})
    cats = pd.DataFrame({"category_name_pt": ["x"], "category_name_en": ["x"], "estimated_cost_ratio": [0.6]})
    a = transformation.build_products(products, cats)["estimated_cost_ratio"]
    b = transformation.build_products(products, cats)["estimated_cost_ratio"]
    assert a.equals(b)                                                # same product id -> same cost ratio
    eps = 1e-9   # values are rounded to 3 decimals; 0.6 + 0.06 is 0.65999... in floating point
    assert a.between(0.6 - transformation.PRODUCT_COST_JITTER - eps, 0.6 + transformation.PRODUCT_COST_JITTER + eps).all()
    assert a.nunique() > 50                                           # products really differ


def test_referential_integrity_detects_orphans():
    model = {
        "locations": pd.DataFrame({"zip_code_prefix": ["01001"], "city": ["sao paulo"], "state_code": ["SP"]}),
        "categories": pd.DataFrame({"category_name_pt": ["x"]}),
        "products": pd.DataFrame({"product_uid": ["p"], "category_name_pt": ["x"]}),
        "sellers": pd.DataFrame({"seller_uid": ["s"], "zip_code_prefix": ["01001"], "city": ["sao paulo"], "state_code": ["SP"]}),
        "customers": pd.DataFrame({"customer_uid": ["c"], "zip_code_prefix": ["01001"], "city": ["sao paulo"], "state_code": ["SP"]}),
        "calendar": pd.DataFrame({"date_key": pd.to_datetime(["2018-01-01"])}),
        "orders": pd.DataFrame({"order_uid": ["o"], "customer_uid": ["c"], "purchased_at": pd.to_datetime(["2018-01-01 10:00"]),
                                "delivery_zip_code_prefix": ["01001"], "delivery_city": ["sao paulo"], "delivery_state_code": ["SP"]}),
        "order_items": pd.DataFrame({"order_uid": ["o"], "product_uid": ["p"], "seller_uid": ["MISSING"]}),
        "order_payments": pd.DataFrame({"order_uid": ["o"]}),
        "order_reviews": pd.DataFrame({"order_uid": ["o"]}),
    }
    problems = validation.check_referential_integrity(model)
    assert problems == ["order_items.seller_uid: 1 values without parent"]
