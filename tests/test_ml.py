"""Unit tests for machine-learning building blocks: calendars, metrics, RFM rules and churn features."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from machine_learning.churn import model as churn
from machine_learning.forecasting import forecast
from machine_learning.segmentation import rfm


@pytest.mark.parametrize("year, day", [(2017, "2017-11-24"), (2018, "2018-11-23"), (2016, "2016-11-25")])
def test_black_friday(year, day):
    assert forecast.black_friday(year) == pd.Timestamp(day)


def test_forecast_metrics():
    m = forecast.metrics(np.array([100.0, 200.0, 0.0]), np.array([110.0, 190.0, 5.0]))
    assert m["MAE"] == pytest.approx(25 / 3)
    assert m["MAPE"] == pytest.approx(7.5)            # zero-revenue days are excluded from MAPE
    assert m["Bias %"] == pytest.approx(305 / 300 * 100 - 100)


def test_seasonal_naive_baseline_repeats_last_week():
    days = pd.date_range("2018-01-01", periods=21, freq="D")
    y = pd.Series(np.tile([1.0, 2, 3, 4, 5, 6, 7], 3), index=days)
    pred = forecast.baseline_forecast(y, pd.Timestamp("2018-01-22"), "Seasonal naive (last week)", horizon=7)
    assert pred.tolist() == [1, 2, 3, 4, 5, 6, 7]


def test_rfm_rules_follow_the_documented_order():
    gap = {"p75": 170.0, "p90": 280.0}
    # Ten customers, so the top fifth of spend (M = 5) is exactly the two biggest spenders (1 and 5).
    df = rfm.score(pd.DataFrame({
        "customer_key": range(1, 11),
        "recency_days": [30, 30, 40, 200, 400, 10, 20, 25, 35, 45],
        "frequency": [3, 2, 1, 2, 5, 1, 1, 1, 1, 1],
        "monetary": [5000.0, 50.0, 80.0, 900.0, 9000.0, 60.0, 70.0, 90.0, 100.0, 110.0],
    }))
    segments = rfm.assign_segments(df, gap).tolist()
    # 1: repeat + top spend + recent -> VIP; 2: repeat -> Loyal; 3: one order, recent -> Potential;
    # 4: past P75 -> At Risk even though repeat; 5: past P90 -> Lost even with the highest spend.
    assert segments[:5] == ["VIP", "Loyal", "Potential", "At Risk", "Lost"]
    assert df.loc[df["customer_key"].isin([1, 5]), "m_score"].eq(5).all()
    assert set(df["r_score"]) <= {1, 2, 3, 4, 5} and df.loc[df["frequency"] == 1, "f_score"].eq(1).all()


def _history():
    t = pd.Timestamp
    return pd.DataFrame({
        "customer_key": [1, 1, 2, 2],
        "order_key": [10, 11, 20, 21],
        "purchased_at": [t("2018-01-05"), t("2018-03-10"), t("2018-01-20"), t("2018-01-25")],
        "delivered_customer_at": [t("2018-01-30"), pd.NaT, t("2018-02-15"), t("2018-01-29")],
        "estimated_delivery_date": [t("2018-01-20"), t("2018-03-30"), t("2018-02-20"), t("2018-02-10")],
        "revenue": [100.0, 50.0, 80.0, 20.0], "freight": [10.0, 5.0, 8.0, 2.0], "items": [1, 1, 2, 1],
        "installments": [1, 1, 3, 1], "main_payment_type": ["credit_card", "boleto", "credit_card", "voucher"],
        "region": ["Sudeste", "Sudeste", "Sul", "Sul"],
        "review_score": [2.0, 5.0, 5.0, 4.0],
        "first_review_at": [t("2018-01-31"), t("2018-04-01"), t("2018-02-16"), t("2018-02-20")],
    })


def test_churn_features_use_only_information_known_at_the_cutoff():
    f = churn.build_features(_history(), pd.Timestamp("2018-02-01"))
    assert list(f.index) == [1, 2]                       # the March order is after the cut-off
    c1, c2 = f.loc[1], f.loc[2]
    assert c1["frequency"] == 1 and c1["recency_days"] == 27
    assert c1["last_order_late"] == 1.0                  # delivered Jan 30, promised Jan 20
    assert c1["last_review"] == 2.0                      # review written Jan 31, before the cut-off
    # Customer 2: both reviews were written after the cut-off, so none is known yet; the order
    # delivered on Feb 15 is not late at the cut-off because its promised date (Feb 20) has not passed.
    assert c2["frequency"] == 2
    assert np.isnan(c2["last_review"]) and c2["has_review"] == 0.0
    assert c2["late_share"] == 0.0


def test_churn_labels_look_only_at_the_following_horizon():
    h = _history()
    customers = pd.Index([1, 2])
    labels = churn.build_labels(h, customers, pd.Timestamp("2018-02-01"))
    assert labels.to_dict() == {1: 1, 2: 0}              # customer 1 bought again on Mar 10


def test_churn_threshold_metrics_treat_churn_as_positive():
    y_returned = np.array([1, 0, 0, 0])
    p_return = np.array([0.9, 0.1, 0.2, 0.8])
    m = churn.threshold_metrics(y_returned, p_return, threshold=0.5)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 0, 1, 1)
    assert m["recall_returning"] == 1.0 and m["precision_returning"] == 0.5
