"""Anomaly detection: days, products and customers that break their own pattern.

Detectors
---------
1. Daily revenue / orders  (level-adjusted robust z-score, explainable)
   Each day is expressed as a ratio to the mean of its previous 28 days, so
   underlying growth is not mistaken for anomalies. That ratio is compared with
   the median ratio of the same weekday over the previous 8 weeks, scaled by the
   MAD. Flag when |z| >= 3.5 (Iglewicz & Hoaglin) AND the day deviates at least
   30% from its expected value (statistical and practical significance). This
   catches unexpected drops and unusual transaction spikes.
2. Daily profile  (Isolation Forest, multivariate)
   Finds days whose *combination* of metrics is unusual (for example normal
   revenue with an odd order count, basket size or share of new customers).
   Revenue and orders are divided by their trailing 28-day median, so growth is
   not flagged as anomalous.
3. Products  (Poisson test)
   Units in the last 28 days vs the rate of the 84 days before. Flag when the
   observed count is implausible under the product's own history (p < 0.001).
4. Customers  (Isolation Forest)
   Customers whose purchasing profile (order count, spend, basket, same-day
   orders, installments, freight) is extreme compared with the customer base.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from ..common import RANDOM_STATE, ModelRun, query, reporting_period
from ..forecasting.forecast import black_friday

log = logging.getLogger(__name__)

Z_THRESHOLD = 3.5
MIN_RELATIVE_CHANGE = 0.30      # practical significance for daily anomalies
WEEKDAY_WINDOW = 8                # previous same-weekdays used as baseline
PRODUCT_RECENT_DAYS, PRODUCT_BASELINE_DAYS, PRODUCT_P_VALUE = 28, 84, 0.001
DAY_CONTAMINATION, CUSTOMER_CONTAMINATION = 0.01, 0.001
MAX_PRODUCTS_PER_DIRECTION = 25


def _robust_z(x: pd.Series, median: pd.Series, mad: pd.Series) -> pd.Series:
    return 0.6745 * (x - median) / mad.replace(0, np.nan)


def _day_label(day: pd.Timestamp, holiday: str | None) -> str:
    notes = [holiday] if isinstance(holiday, str) else []
    if day.normalize() == black_friday(day.year):
        notes.append("Black Friday")
    return f"{day:%a %d %b %Y}" + (f" ({', '.join(notes)})" if notes else "")


# -----------------------------------------------------------------------------
# 1 + 2. Days
# -----------------------------------------------------------------------------

def load_daily() -> pd.DataFrame:
    df = query("""
        SELECT c.date_key, c.holiday_name,
               count(o.order_key)                                   AS orders,
               coalesce(sum(o.revenue), 0)                          AS revenue,
               avg(o.items)                                         AS items_per_order,
               avg((o.customer_order_seq = 1)::int)                 AS new_customer_share,
               avg((o.main_payment_type = 'boleto')::int)           AS boleto_share
        FROM core.calendar c
        LEFT JOIN analytics.v_orders o ON o.purchase_date = c.date_key AND o.is_valid_sale
        WHERE c.date_key >= (SELECT first_month FROM analytics.v_reporting_period)
          AND c.date_key <  (SELECT reference_date FROM analytics.v_reporting_period)
        GROUP BY c.date_key, c.holiday_name
        ORDER BY c.date_key
    """)
    df["date_key"] = pd.to_datetime(df["date_key"])
    df = df.set_index("date_key")
    df["avg_order_value"] = df["revenue"] / df["orders"].replace(0, np.nan)
    return df


def detect_daily_spikes(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    labels = {"revenue": ("Revenue", "was", lambda v: f"R$ {v:,.0f}"),
              "orders": ("Orders", "were", lambda v: f"{v:,.0f}")}
    for metric, (label, verb, fmt) in labels.items():
        s = daily[metric].astype(float)
        level = s.shift(1).rolling(28).mean()
        ratio = s / level
        by_dow = ratio.groupby(ratio.index.dayofweek)
        base = by_dow.transform(lambda x: x.shift(1).rolling(WEEKDAY_WINDOW, min_periods=4).median())
        mad = by_dow.transform(lambda x: x.shift(1).rolling(WEEKDAY_WINDOW, min_periods=4)
                               .apply(lambda w: np.median(np.abs(w - np.median(w))), raw=True))
        z = _robust_z(ratio, base, mad)
        expected = base * level
        relative = (s / expected - 1).abs()
        for day in z[(z.abs() >= Z_THRESHOLD) & (relative >= MIN_RELATIVE_CHANGE)].index:
            obs, exp, zz = s[day], expected[day], z[day]
            rows.append({
                "entity_type": "day", "entity_key": None, "period_start": day.date(), "period_end": day.date(),
                "metric": metric, "observed": round(obs, 2), "expected": round(exp, 2), "score": round(abs(zz), 3),
                "direction": "up" if zz > 0 else "down", "method": "robust_z_same_weekday",
                "description": (f"{label} on {_day_label(day, daily.at[day, 'holiday_name'])} {verb} {fmt(obs)}, "
                                f"{obs / exp - 1:+.0%} vs the {fmt(exp)} expected for a {day:%A} at the "
                                f"current sales level (robust z = {zz:+.1f})."),
            })
    return pd.DataFrame(rows)


DAY_PROFILE_LABELS = {
    "revenue_vs_28d": "revenue vs trailing 28-day median",
    "orders_vs_28d": "orders vs trailing 28-day median",
    "avg_order_value": "average order value",
    "items_per_order": "items per order",
    "new_customer_share": "share of first-time customers",
    "boleto_share": "share of boleto payments",
}


def detect_daily_profiles(daily: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame({
        "revenue_vs_28d": daily["revenue"] / daily["revenue"].shift(1).rolling(28).median(),
        "orders_vs_28d": daily["orders"] / daily["orders"].shift(1).rolling(28).median(),
        "avg_order_value": daily["avg_order_value"],
        "items_per_order": daily["items_per_order"],
        "new_customer_share": daily["new_customer_share"],
        "boleto_share": daily["boleto_share"],
    }).dropna()
    forest = IsolationForest(n_estimators=300, contamination=DAY_CONTAMINATION, random_state=RANDOM_STATE)
    scaled = StandardScaler().fit_transform(f)
    flags = forest.fit_predict(scaled) == -1
    scores = -forest.score_samples(scaled)

    median, mad = f.median(), (f - f.median()).abs().median()
    z = 0.6745 * (f - median) / mad
    rows = []
    for i in np.flatnonzero(flags):
        day = f.index[i]
        top = z.loc[day].abs().nlargest(2).index
        parts = [f"{DAY_PROFILE_LABELS[c]} {f.at[day, c]:.2f} (typical {median[c]:.2f})" for c in top]
        rows.append({
            "entity_type": "day", "entity_key": None, "period_start": day.date(), "period_end": day.date(),
            "metric": "daily_profile", "observed": None, "expected": None, "score": round(scores[i], 3),
            "direction": "mixed", "method": "isolation_forest",
            "description": (f"Unusual combination of metrics on {_day_label(day, daily.at[day, 'holiday_name'])}: "
                            + "; ".join(parts) + "."),
        })
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# 3. Products
# -----------------------------------------------------------------------------

def detect_products(reference_date) -> pd.DataFrame:
    ref = pd.Timestamp(reference_date)
    recent_start = ref - timedelta(days=PRODUCT_RECENT_DAYS)
    base_start = recent_start - timedelta(days=PRODUCT_BASELINE_DAYS)
    df = query("""
        SELECT p.product_key, p.product_short_id, p.category,
               count(*) FILTER (WHERE s.purchase_date >= :recent AND s.purchase_date < :ref)  AS recent_units,
               count(*) FILTER (WHERE s.purchase_date >= :base   AND s.purchase_date < :recent) AS baseline_units
        FROM analytics.v_sales_items s
        JOIN analytics.v_product_performance p USING (product_key)
        WHERE s.purchase_date >= :base AND s.purchase_date < :ref
        GROUP BY p.product_key, p.product_short_id, p.category
    """, recent=recent_start.date(), ref=ref.date(), base=base_start.date())
    df["expected"] = df["baseline_units"] * PRODUCT_RECENT_DAYS / PRODUCT_BASELINE_DAYS

    # Require history (>= 2 units a week) for drops and some history for spikes; brand-new products
    # have no pattern to break.
    drops = df[df["baseline_units"] >= 24].copy()
    drops["p"] = stats.poisson.cdf(drops["recent_units"], drops["expected"])
    drops = drops[(drops["p"] < PRODUCT_P_VALUE) & (drops["recent_units"] < drops["expected"])].assign(direction="down")
    spikes = df[(df["baseline_units"] >= 6) & (df["recent_units"] >= 10)].copy()
    spikes["p"] = stats.poisson.sf(spikes["recent_units"] - 1, spikes["expected"])
    spikes = spikes[(spikes["p"] < PRODUCT_P_VALUE) & (spikes["recent_units"] > spikes["expected"])].assign(direction="up")

    out = []
    for flagged in (drops, spikes):
        flagged = flagged.assign(score=-np.log10(flagged["p"].clip(lower=1e-300)))
        out.append(flagged.nlargest(MAX_PRODUCTS_PER_DIRECTION, "score"))
    flagged = pd.concat(out)
    return pd.DataFrame({
        "entity_type": "product",
        "entity_key": flagged["product_key"],
        "period_start": recent_start.date(),
        "period_end": (ref - timedelta(days=1)).date(),
        "metric": "units_sold",
        "observed": flagged["recent_units"],
        "expected": flagged["expected"].round(2),
        "score": flagged["score"].round(3),
        "direction": flagged["direction"],
        "method": "poisson_vs_own_history",
        "description": [
            f"Product {r.product_short_id} ({r.category}) sold {r.recent_units} units in the last "
            f"{PRODUCT_RECENT_DAYS} days vs {r.expected:.1f} expected from its previous {PRODUCT_BASELINE_DAYS} "
            f"days ({(r.recent_units / r.expected - 1):+.0%}; Poisson p = {r.p:.1e})."
            for r in flagged.itertuples()],
    })


# -----------------------------------------------------------------------------
# 4. Customers
# -----------------------------------------------------------------------------

CUSTOMER_LABELS = {
    "orders": ("orders", "{:.0f}"),
    "revenue": ("lifetime spend", "R$ {:,.2f}"),
    "avg_order_value": ("average order", "R$ {:,.2f}"),
    "items_per_order": ("items per order", "{:.1f}"),
    "max_orders_same_day": ("orders on a single day", "{:.0f}"),
    "max_installments": ("installments", "{:.0f}"),
    "freight_ratio": ("freight / order value", "{:.0%}"),
}


def detect_customers() -> pd.DataFrame:
    df = query("""
        WITH o AS (
            SELECT *, count(*) OVER (PARTITION BY customer_key, purchase_date) AS day_orders
            FROM analytics.v_orders
            WHERE is_valid_sale
              AND purchase_date < (SELECT reference_date FROM analytics.v_reporting_period))
        SELECT customer_key,
               min(purchase_date)                          AS first_order,
               max(purchase_date)                          AS last_order,
               count(*)                                    AS orders,
               sum(revenue)                                AS revenue,
               avg(revenue)                                AS avg_order_value,
               sum(items)::numeric / count(*)              AS items_per_order,
               max(day_orders)                             AS max_orders_same_day,
               coalesce(max(installments), 1)              AS max_installments,
               sum(freight) / nullif(sum(revenue), 0)      AS freight_ratio
        FROM o
        GROUP BY customer_key
    """)
    features = list(CUSTOMER_LABELS)
    X = np.log1p(df[features].astype(float).fillna(0))
    forest = IsolationForest(n_estimators=300, contamination=CUSTOMER_CONTAMINATION, random_state=RANDOM_STATE)
    scaled = StandardScaler().fit_transform(X)
    flags = forest.fit_predict(scaled) == -1
    scores = -forest.score_samples(scaled)
    # Share of customers with a strictly lower / higher value (a customer never beats itself).
    below = (df[features].rank(method="min") - 1) / len(df)
    above = (len(df) - df[features].rank(method="max")) / len(df)

    rows = []
    for i in np.flatnonzero(flags):
        # Explain with the two features that are most extreme relative to other customers.
        extremeness = pd.concat([below.iloc[i], above.iloc[i]], axis=1).max(axis=1).sort_values(ascending=False)
        parts = []
        for col in extremeness.index[:2]:
            label, fmt = CUSTOMER_LABELS[col]
            if below.at[i, col] >= above.at[i, col]:
                parts.append(f"{label} {fmt.format(df.at[i, col])} (higher than {below.at[i, col]:.2%} of customers)")
            else:
                parts.append(f"{label} {fmt.format(df.at[i, col])} (lower than {above.at[i, col]:.2%} of customers)")
        rows.append({
            "entity_type": "customer", "entity_key": int(df.at[i, "customer_key"]),
            "period_start": df.at[i, "first_order"], "period_end": df.at[i, "last_order"],
            "metric": "purchase_profile", "observed": round(float(df.at[i, "revenue"]), 2), "expected": None,
            "score": round(scores[i], 3), "direction": "mixed", "method": "isolation_forest",
            "description": "Unusual purchasing profile: " + "; ".join(parts) + ".",
        })
    return pd.DataFrame(rows)


def run() -> dict:
    log.info("Anomaly detection")
    _, _, reference_date = reporting_period()
    daily = load_daily()
    parts = {
        "daily_spikes": detect_daily_spikes(daily),
        "daily_profiles": detect_daily_profiles(daily),
        "products": detect_products(reference_date),
        "customers": detect_customers(),
    }
    for name, df in parts.items():
        log.info("  %-15s %4d anomalies", name, len(df))
    anomalies = pd.concat(parts.values(), ignore_index=True)
    anomalies["entity_key"] = anomalies["entity_key"].astype("Int64")

    with ModelRun("anomaly_detection", "Robust z-score + Isolation Forest + Poisson", reference_date) as run:
        run.params = {"z_threshold": Z_THRESHOLD, "weekday_window_weeks": WEEKDAY_WINDOW,
                      "day_contamination": DAY_CONTAMINATION, "customer_contamination": CUSTOMER_CONTAMINATION,
                      "product_windows_days": [PRODUCT_BASELINE_DAYS, PRODUCT_RECENT_DAYS],
                      "product_p_value": PRODUCT_P_VALUE}
        run.metrics = {name: int(len(df)) for name, df in parts.items()}
        run.write("ml.anomalies", anomalies[["entity_type", "entity_key", "period_start", "period_end", "metric",
                                             "observed", "expected", "score", "direction", "method",
                                             "description"]])
    return {"anomalies": anomalies, "daily": daily}
