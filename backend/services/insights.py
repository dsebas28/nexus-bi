"""Data-backed insights for the selected period.

Every insight is computed from query results and carries the numbers behind
it, so the dashboard never shows a claim that the data does not support.
Rules skip themselves when the data is too thin to say something meaningful.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..models.database import fetch_all, fetch_one
from . import customers, geo, sales
from .cache import cached

MIN_SHARE_FOR_RANKING = 0.01   # ignore categories/states below 1% of revenue (noisy growth rates)
MIN_ORDERS_FOR_RANKING = 30    # ...and those with too few orders for a growth rate to mean anything


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _brl(x: float) -> str:
    return f"{'−' if x < 0 else ''}R$ {abs(x):,.0f}"


def _signed_brl(x: float) -> str:
    return f"+{_brl(x)}" if x > 0 else _brl(x)


def nice(name: str) -> str:
    """Readable category name: 'sports_leisure' -> 'Sports leisure'."""
    return name.replace("_", " ").capitalize()


def _insight(kind: str, tone: str, title: str, text: str, source: str, **values: Any) -> dict[str, Any]:
    return {"kind": kind, "tone": tone, "title": title, "text": text, "source": source, "values": values}


def _revenue(start, end, state, category) -> dict | None:
    rows = {r["metric"]: r for r in sales.kpis(start, end, state, category)}
    rev, orders, aov = rows.get("revenue"), rows.get("orders"), rows.get("avg_order_value")
    if not rev or rev["previous_value"] in (None, 0):
        return None
    change = rev["change_pct"] / 100
    orders_chg, aov_chg = (orders["change_pct"] or 0) / 100, (aov["change_pct"] or 0) / 100
    if abs(orders_chg) >= abs(aov_chg):
        driver = "more orders" if orders_chg > 0 else "fewer orders"
    else:
        driver = "higher order values" if aov_chg > 0 else "lower order values"
    return _insight(
        "revenue", "positive" if change > 0.005 else "negative" if change < -0.005 else "neutral",
        f"Revenue {'grew' if change >= 0 else 'fell'} {abs(change):.1%}",
        (f"{_brl(rev['current_value'])} vs {_brl(rev['previous_value'])} in the previous period. "
         f"Orders changed {_pct((orders['change_pct'] or 0) / 100)} and the average order value "
         f"{_pct((aov['change_pct'] or 0) / 100)}, so the change came mainly from {driver}."),
        "kpi_summary", revenue=rev["current_value"], previous=rev["previous_value"],
        orders_change_pct=orders["change_pct"], aov_change_pct=aov["change_pct"])


def _categories(start, end, prev_start, state, category) -> list[dict]:
    if category is not None:
        return []
    rows = [r for r in sales.by_category(start, end, prev_start, state, None)
            if (r["revenue_share"] or 0) >= MIN_SHARE_FOR_RANKING and r["revenue_previous"] > 0
            and r["orders"] >= MIN_ORDERS_FOR_RANKING and r["category"] != "uncategorized"]
    if len(rows) < 3:
        return []
    for r in rows:
        r["delta"] = r["revenue"] - r["revenue_previous"]
    up, down = max(rows, key=lambda r: r["delta"]), min(rows, key=lambda r: r["delta"])
    out = []
    if up["delta"] > 0:
        out.append(_insight(
            "category_growth", "positive", f"{nice(up['category'])} added the most revenue",
            (f"{_signed_brl(up['delta'])} vs the previous period ({_pct(up['revenue_growth'])}), now "
             f"{up['revenue_share']:.1%} of revenue. Protect its stock and delivery performance."),
            "sales/categories", category=up["category"], delta=up["delta"], growth=up["revenue_growth"]))
    if down["delta"] < 0:
        out.append(_insight(
            "category_decline", "negative", f"{nice(down['category'])} lost the most revenue",
            (f"{_signed_brl(down['delta'])} vs the previous period ({_pct(down['revenue_growth'])}). "
             "Check pricing, availability and seller activity in this category."),
            "sales/categories", category=down["category"], delta=down["delta"], growth=down["revenue_growth"]))
    return out


def _states(start, end, prev_start, state, category) -> list[dict]:
    if state is not None:
        return []
    rows = geo.states(start, end, prev_start, category)
    total = sum(r["revenue"] for r in rows) or 1
    eligible = [r for r in rows if r["revenue"] / total >= MIN_SHARE_FOR_RANKING and r["revenue_growth"] is not None
                and r["orders"] >= MIN_ORDERS_FOR_RANKING]
    if len(eligible) < 3:
        return []
    best = max(eligible, key=lambda r: r["revenue_growth"])
    worst_delivery = max((r for r in eligible if r["late_delivery_rate"] is not None),
                         key=lambda r: r["late_delivery_rate"], default=None)
    out = [_insight(
        "state_growth", "positive" if best["revenue_growth"] > 0 else "neutral",
        f"{best['state_name']} is growing fastest",
        (f"Revenue {_pct(best['revenue_growth'])} vs the previous period "
         f"({_brl(best['revenue_previous'])} → {_brl(best['revenue'])}), among states with at least 1% of sales and 30 orders."),
        "geo/states", state=best["state_code"], growth=best["revenue_growth"])]
    if worst_delivery and worst_delivery["late_delivery_rate"] > 0.08:
        out.append(_insight(
            "delivery_risk", "negative", f"Late deliveries are highest in {worst_delivery['state_name']}",
            (f"{worst_delivery['late_delivery_rate']:.1%} of orders arrived after the promised date "
             f"(average review {worst_delivery['avg_review_score'] or 0:.2f}★). Late orders are rated far lower, "
             "so review carriers and promised dates for this state."),
            "geo/states", state=worst_delivery["state_code"], late_rate=worst_delivery["late_delivery_rate"]))
    return out


@cached
def _delivery_effect(start: date, end: date, state: str | None) -> dict | None:
    return fetch_one("""
        SELECT round(avg(review_score) FILTER (WHERE is_late), 2)      AS late_review,
               round(avg(review_score) FILTER (WHERE NOT is_late), 2)  AS on_time_review,
               round(avg(is_late::int), 4)                             AS late_rate,
               count(*) FILTER (WHERE is_late)                         AS late_orders
        FROM analytics.v_orders
        WHERE is_valid_sale AND is_late IS NOT NULL AND review_score IS NOT NULL
          AND purchase_date BETWEEN %(start)s AND %(end)s
          AND (%(state)s::text IS NULL OR state_code = %(state)s)
    """, {"start": start, "end": end, "state": state})


def _delivery(start, end, state) -> dict | None:
    d = _delivery_effect(start, end, state)
    if not d or not d["late_orders"] or d["late_orders"] < 30:
        return None
    return _insight(
        "delivery_reviews", "negative" if d["late_rate"] > 0.05 else "neutral",
        f"{d['late_rate']:.1%} of orders arrived late",
        (f"Late orders averaged {d['late_review']:.2f}★ vs {d['on_time_review']:.2f}★ on time "
         f"({d['late_orders']:,} late orders). Delivery reliability is the clearest driver of satisfaction."),
        "v_orders", late_rate=d["late_rate"], late_review=d["late_review"], on_time_review=d["on_time_review"])


@cached
def _anomalies_in_period(start: date, end: date) -> list[dict]:
    return fetch_all("""
        SELECT period_start, metric, direction, score, description
        FROM analytics.v_anomalies
        WHERE entity_type = 'day' AND method = 'robust_z_same_weekday'
          AND period_start BETWEEN %(start)s AND %(end)s
        ORDER BY score DESC
    """, {"start": start, "end": end})


def _anomalies(start, end) -> dict | None:
    rows = _anomalies_in_period(start, end)
    if not rows:
        return None
    days = len({r["period_start"] for r in rows})
    top = rows[0]
    return _insight(
        "anomalies", "negative" if top["direction"] == "down" else "neutral",
        f"{days} day{'s' if days != 1 else ''} broke the usual pattern",
        f"Strongest: {top['description']}", "anomalies", days=days)


def _customers(start, end, state) -> dict | None:
    o = customers.overview(start, end, state)
    if not o or not o["active_customers"]:
        return None
    base = customers.base_metrics()
    new_share = o["new_customers"] / o["active_customers"]
    return _insight(
        "retention", "negative" if new_share > 0.9 else "neutral",
        f"{new_share:.1%} of buyers in the period were first-time customers",
        (f"Only {base['repeat_rate']:.1%} of all customers have bought twice. A repeat buyer's lifetime revenue is "
         f"{base['avg_clv_repeat'] / base['avg_clv_one_time']:.1f}× a one-time buyer's, so converting recent buyers "
         "(the Potential segment) to a second purchase is the largest growth lever."),
        "customers/overview", new_share=new_share, repeat_rate=base["repeat_rate"])


def build(start: date, end: date, prev_start: date, state: str | None, category: int | None) -> list[dict]:
    insights: list[dict | None] = [_revenue(start, end, state, category)]
    insights += _categories(start, end, prev_start, state, category)
    insights += _states(start, end, prev_start, state, category)
    insights += [_delivery(start, end, state), _anomalies(start, end), _customers(start, end, state)]
    return [i for i in insights if i is not None]
