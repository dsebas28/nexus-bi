"""RFM segmentation with thresholds derived from observed purchase behaviour.

Scores (1-5, higher is better)
  R  quintiles of recency_days (most recent fifth = 5).
  M  quintiles of total revenue (top fifth = 5).
  F  quintiles are impossible: ~97% of customers have exactly one order, so F is
     bucketed on the actual counts: 1 order -> 1, 2 -> 3, 3 -> 4, 4+ -> 5.

Segments (evaluated in order; the first matching rule wins)
  The recency cut-offs are not chosen by hand. They are the 75th and 90th
  percentiles of the real gap between consecutive purchases of repeat customers
  (same-day orders excluded, as they are split baskets rather than returns):

  Lost       recency > P90 gap      beyond the window of 90% of repeat purchases
  At Risk    recency > P75 gap      past the window of 75% of repeat purchases
  VIP        2+ orders and M = 5    repeat buyer in the top fifth of spend
  Loyal      2+ orders
  Potential  everyone else          recent one-time buyer, still inside the window
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..common import ModelRun, query, reporting_period

log = logging.getLogger(__name__)

SEGMENT_ORDER = ["VIP", "Loyal", "Potential", "At Risk", "Lost"]


def load_customers() -> pd.DataFrame:
    return query("""
        SELECT customer_key, recency_days, orders AS frequency, total_revenue AS monetary
        FROM analytics.v_customer_summary
    """)


def repurchase_gap_percentiles() -> dict[str, float]:
    gaps = query("""
        WITH p AS (
            SELECT purchased_at::date - lag(purchased_at::date)
                       OVER (PARTITION BY customer_key ORDER BY purchased_at) AS gap
            FROM analytics.v_orders
            WHERE is_valid_sale
              AND purchase_date < (SELECT reference_date FROM analytics.v_reporting_period))
        SELECT gap FROM p WHERE gap > 0
    """)["gap"]
    return {"p50": float(gaps.quantile(0.50)), "p75": float(gaps.quantile(0.75)),
            "p90": float(gaps.quantile(0.90)), "n_gaps": int(len(gaps))}


def _quintile_score(values: pd.Series, ascending: bool = True) -> pd.Series:
    """1-5 score by quintile of rank (ties broken by order so bins are equal-sized)."""
    ranks = values.rank(method="first", ascending=ascending)
    return pd.qcut(ranks, 5, labels=[1, 2, 3, 4, 5]).astype(int)


def score(customers: pd.DataFrame) -> pd.DataFrame:
    df = customers.copy()
    df["r_score"] = _quintile_score(df["recency_days"], ascending=False)
    df["m_score"] = _quintile_score(df["monetary"])
    df["f_score"] = np.select([df["frequency"] >= 4, df["frequency"] == 3, df["frequency"] == 2],
                              [5, 4, 3], default=1)
    return df


def assign_segments(df: pd.DataFrame, gap: dict[str, float]) -> pd.Series:
    repeat = df["frequency"] >= 2
    conditions = [
        df["recency_days"] > gap["p90"],
        df["recency_days"] > gap["p75"],
        repeat & (df["m_score"] == 5),
        repeat,
    ]
    return pd.Series(np.select(conditions, ["Lost", "At Risk", "VIP", "Loyal"], default="Potential"),
                     index=df.index)


def segment_definitions(gap: dict[str, float]) -> pd.DataFrame:
    p75, p90 = round(gap["p75"]), round(gap["p90"])
    rows = [
        ("VIP", f"2+ orders, top-20% spend (M = 5) and last purchase within {p75} days",
         "Repeat buyers with the highest spend who are still active.",
         "Protect them: priority support, early access and loyalty rewards. Monitor their delivery "
         "experience closely, since late deliveries strongly reduce satisfaction."),
        ("Loyal", f"2+ orders and last purchase within {p75} days",
         "Customers who have already come back at least once and are still active.",
         "Grow the basket: cross-sell complementary categories and offer incentives on the next purchase."),
        ("Potential", f"1 order, last purchase within {p75} days",
         f"Recent one-time buyers still inside the window in which 75% of repeat purchases happen ({p75} days).",
         f"Convert to a second purchase: post-delivery follow-up and a targeted voucher before day {p75}."),
        ("At Risk", f"Last purchase between {p75} and {p90} days ago",
         "Customers past the window of 75% of observed repeat purchases.",
         "Win back now with a personalised offer based on the last category bought. Check whether their "
         "last order had a delivery or review problem."),
        ("Lost", f"No purchase for more than {p90} days",
         "Customers beyond the window in which 90% of observed repeat purchases happen.",
         "Use only low-cost reactivation (e-mail campaigns). Do not spend acquisition-level budget on them."),
    ]
    out = pd.DataFrame(rows, columns=["segment", "rule", "description", "recommendation"])
    out.insert(1, "display_order", range(1, len(out) + 1))
    return out


def run() -> dict:
    log.info("RFM segmentation")
    _, _, reference_date = reporting_period()
    gap = repurchase_gap_percentiles()
    df = score(load_customers())
    df["segment"] = assign_segments(df, gap)

    summary = (df.groupby("segment")
                 .agg(customers=("customer_key", "size"), revenue=("monetary", "sum"))
                 .reindex(SEGMENT_ORDER))
    summary["customer_share"] = summary["customers"] / summary["customers"].sum()
    summary["revenue_share"] = summary["revenue"] / summary["revenue"].sum()
    log.info("  gap percentiles (days): p75=%.0f p90=%.0f (n=%d)", gap["p75"], gap["p90"], gap["n_gaps"])

    with ModelRun("rfm_segmentation", "Rule-based RFM (data-derived thresholds)", reference_date) as run:
        run.params = {"repurchase_gap_days": gap, "f_score_buckets": {"1": 1, "2": 3, "3": 4, "4+": 5}}
        run.metrics = {"customers": int(len(df)), "segments": summary.round(4).reset_index().to_dict("records")}
        run.write("ml.segment_definitions", segment_definitions(gap))
        run.write("ml.customer_segments", df[["customer_key", "recency_days", "frequency", "monetary",
                                              "r_score", "f_score", "m_score", "segment"]])
    return {"summary": summary, "gap": gap}
