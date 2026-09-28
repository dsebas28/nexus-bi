"""Guided mode: a library of business questions answered WITHOUT a language model.

Free and always available. Each question runs hand-written SQL through exactly
the same safety path as the AI (validator, read-only transaction, row limits),
and the answer text is built from the query results, so every figure is real.
It only answers questions in its library, and it says so when a question is
outside it, instead of guessing.
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import Callable

from . import sql_generator
from .analyst import AnalystResult
from .validation import unverified_numbers

MODE_NAME = "Guided mode (no AI)"


MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
             "octubre", "noviembre", "diciembre"]


def _brl(x: float) -> str:
    return f"{'−' if x < 0 else ''}R$ {abs(x):,.0f}"


def _pct(x: float, signed: bool = True) -> str:
    return f"{x:+.1f}%" if signed else f"{x:.1f}%"


def _nice(name: str) -> str:
    return str(name).replace("_", " ").capitalize()


@dataclass
class Guided:
    id: str
    question: str
    keywords: tuple[str, ...]
    steps: tuple[tuple[str, str], ...]                   # (purpose, sql)
    interpret: Callable[[list[list[dict]]], str]          # rows as dicts, one list per step


def _rows(step: dict) -> list[dict]:
    return [dict(zip(step["columns"], r)) for r in step["rows"]]


LAST_MONTH_BOUNDS = """(SELECT last_month FROM analytics.v_reporting_period),
       (SELECT (last_month + interval '1 month - 1 day')::date FROM analytics.v_reporting_period)"""


# ---------------------------------------------------------------- 1. Why did sales change this month?

def _why_sales(r):
    k = {x["metric"]: x for x in r[0]}
    rev, orders, aov = k["revenue"], k["orders"], k["avg_order_value"]
    period = dict(r[1][0], label=f"{MONTHS_ES[r[1][0]['month'] - 1]} de {r[1][0]['year']}")
    fell = rev["change_pct"] < 0
    lead = (f"En **{period['label']}** los ingresos {'cayeron' if fell else 'no disminuyeron: subieron'} "
            f"**{_pct(rev['change_pct'])}** frente al mes anterior ({_brl(rev['current_value'])} vs {_brl(rev['previous_value'])}).")
    driver = ("el número de pedidos" if abs(orders["change_pct"]) >= abs(aov["change_pct"]) else "el ticket medio")
    cats = r[2]
    losers = [c for c in cats if c["change"] < 0][:3]
    gainers = sorted([c for c in cats if c["change"] > 0], key=lambda c: -c["change"])[:2]
    lines = [lead, "",
             f"- Pedidos: {orders['current_value']:,.0f} ({_pct(orders['change_pct'])}); ticket medio: R$ {aov['current_value']:,.2f} "
             f"({_pct(aov['change_pct'])}). El cambio se explica sobre todo por {driver}."]
    if losers:
        lines.append("- Categorías que más restaron: " + ", ".join(f"{_nice(c['category'])} ({_brl(c['change'])})" for c in losers) + ".")
    if gainers:
        lines.append("- Categorías que más sumaron: " + ", ".join(f"{_nice(c['category'])} (+{_brl(c['change'])})" for c in gainers) + ".")
    late = r[3][0]
    if late["late_pct_current"] is not None:
        lines.append(f"- Entregas tardías: {_pct(late['late_pct_current'], False)} de los pedidos (mes anterior: {_pct(late['late_pct_previous'], False)}).")
    return "\n".join(lines)


WHY_SALES = Guided(
    "why_sales", "¿Por qué disminuyeron las ventas este mes?",
    ("por que", "porque", "disminuyeron", "cayeron", "bajaron", "ventas", "este mes", "why", "sales", "drop", "decrease", "fell", "month"),
    (
        ("KPIs of the last complete month vs the month before",
         f"SELECT metric, round(current_value, 2) AS current_value, round(previous_value, 2) AS previous_value, change_pct\n"
         f"FROM analytics.kpi_summary(\n       {LAST_MONTH_BOUNDS})\nWHERE metric IN ('revenue', 'orders', 'avg_order_value')"),
        ("Which month is the last complete month",
         "SELECT extract(month FROM last_month)::int AS month, extract(year FROM last_month)::int AS year\n"
         "FROM analytics.v_reporting_period"),
        ("Revenue change by category between the two months",
         "WITH p AS (SELECT last_month AS cur, (last_month - interval '1 month')::date AS prev FROM analytics.v_reporting_period)\n"
         "SELECT s.category,\n"
         "       round(coalesce(sum(s.revenue) FILTER (WHERE s.order_month = p.cur), 0)\n"
         "             - coalesce(sum(s.revenue) FILTER (WHERE s.order_month = p.prev), 0), 2) AS change\n"
         "FROM analytics.v_sales_items s CROSS JOIN p\nWHERE s.order_month IN (p.cur, p.prev)\nGROUP BY s.category\nORDER BY change ASC"),
        ("Late-delivery rate in both months",
         "WITH p AS (SELECT last_month AS cur, (last_month - interval '1 month')::date AS prev FROM analytics.v_reporting_period)\n"
         "SELECT round(100 * avg(o.is_late::int) FILTER (WHERE o.order_month = p.cur), 1) AS late_pct_current,\n"
         "       round(100 * avg(o.is_late::int) FILTER (WHERE o.order_month = p.prev), 1) AS late_pct_previous\n"
         "FROM analytics.v_orders o CROSS JOIN p\nWHERE o.is_valid_sale AND o.order_month IN (p.cur, p.prev)"),
    ),
    _why_sales,
)


# ---------------------------------------------------------------- 2. Most profitable category

def _profitable(r):
    top = r[0][0]
    others = r[0][1:3]
    margin = r[1][0]
    return "\n".join([
        f"La categoría con más beneficio estimado es **{_nice(top['category'])}**: {_brl(top['estimated_profit'])} "
        f"sobre {_brl(top['revenue'])} de ingresos (margen estimado {_pct(top['margin_pct'], False)}), en todo el periodo con datos.",
        "",
        *[f"- {_nice(o['category'])}: {_brl(o['estimated_profit'])} de beneficio estimado ({_pct(o['margin_pct'], False)} de margen)." for o in others],
        f"- Mayor margen estimado entre categorías con al menos 1% de las ventas: {_nice(margin['category'])} ({_pct(margin['margin_pct'], False)}).",
        "",
        "Nota: el beneficio y el margen son **estimados** (modelo de costos sintético; Olist no publica costos).",
    ])


PROFITABLE = Guided(
    "profitable_category", "¿Cuál es la categoría más rentable?",
    ("categoria", "rentable", "rentabilidad", "beneficio", "margen", "ganancia", "profitable", "profit", "category", "margin"),
    (
        ("Categories ranked by estimated profit",
         "SELECT category, round(revenue, 2) AS revenue, round(estimated_profit, 2) AS estimated_profit,\n"
         "       round(100 * estimated_margin, 1) AS margin_pct\n"
         "FROM analytics.v_category_performance\nORDER BY estimated_profit DESC\nLIMIT 5"),
        ("Highest estimated margin among categories with at least 1% of revenue",
         "SELECT category, round(100 * estimated_margin, 1) AS margin_pct, round(100 * revenue_share, 1) AS share_pct\n"
         "FROM analytics.v_category_performance\nWHERE revenue_share >= 0.01\nORDER BY estimated_margin DESC\nLIMIT 1"),
    ),
    _profitable,
)


# ---------------------------------------------------------------- 3. Growing cities

def _cities(r):
    rows = r[0]
    return "\n".join([
        f"Las ciudades que más crecen (últimos 6 meses completos frente a los 6 anteriores, con al menos R$ 20,000 de base) "
        f"están encabezadas por **{rows[0]['city'].title()} ({rows[0]['state_code']})**, con {_pct(rows[0]['growth_pct'])}.",
        "",
        *[f"- {c['city'].title()} ({c['state_code']}): {_pct(c['growth_pct'])}, de {_brl(c['revenue_prev_6m'])} a {_brl(c['revenue_last_6m'])}." for c in rows[1:5]],
    ])


CITIES = Guided(
    "growing_cities", "¿Qué ciudades están creciendo más?",
    ("ciudades", "ciudad", "creciendo", "crecen", "crecimiento", "cities", "city", "growing", "growth"),
    (
        ("Cities with the highest revenue growth (last 6 complete months vs previous 6), minimum R$ 20,000 base",
         "SELECT city, state_code, round(revenue_prev_6m, 2) AS revenue_prev_6m, round(revenue_last_6m, 2) AS revenue_last_6m,\n"
         "       round(100 * growth_6m, 1) AS growth_pct\n"
         "FROM analytics.v_geo_city\nWHERE revenue_prev_6m >= 20000\nORDER BY growth_6m DESC\nLIMIT 10"),
    ),
    _cities,
)


# ---------------------------------------------------------------- 4. Customers at risk

def _churn(r):
    bands = {b["risk_band"]: b for b in r[0]}
    top = r[1]
    high = bands["High"]
    return "\n".join([
        f"El modelo marca **{high['customers']:,} clientes** en riesgo alto de no volver a comprar en {high['horizon_days']} días "
        f"(probabilidad media {_pct(high['avg_probability_pct'], False)}), con {_brl(high['lifetime_revenue'])} de ingresos históricos.",
        "",
        "Los de mayor valor dentro del riesgo alto, para priorizar retención:",
        *[f"- Cliente {c['customer_short_id']} ({c['segment']}, {c['city'].title()} {c['state_code']}): {_brl(c['monetary'])} gastados, "
          f"riesgo {_pct(c['churn_probability_pct'], False)}." for c in top[:5]],
        "",
        f"Nota: el modelo tiene un ROC-AUC de {r[2][0]['roc_auc']} en un periodo que no vio al entrenar; úsalo para "
        "priorizar, no como certeza individual.",
    ])


CHURN = Guided(
    "churn_risk", "¿Qué clientes presentan mayor riesgo de abandono?",
    ("clientes", "riesgo", "abandono", "churn", "perder", "se van", "irse", "dejar de comprar", "no vuelven", "volver",
     "customers", "risk", "leave", "retention", "retencion"),
    (
        ("Customers, average probability and lifetime revenue per risk band (churn = no purchase within 180 days)",
         "SELECT risk_band, 180 AS horizon_days, count(*) AS customers,\n"
         "       round(100 * avg(churn_probability), 2) AS avg_probability_pct,\n"
         "       round(sum(monetary), 2) AS lifetime_revenue\n"
         "FROM analytics.v_churn_scores\nGROUP BY risk_band"),
        ("Most valuable high-risk customers",
         "SELECT left(customer_uid, 8) AS customer_short_id, segment, city, state_code, round(monetary, 2) AS monetary,\n"
         "       round(100 * churn_probability, 2) AS churn_probability_pct\n"
         "FROM analytics.v_churn_scores\nWHERE risk_band = 'High'\nORDER BY monetary DESC\nLIMIT 10"),
        ("Out-of-time ROC-AUC of the active churn model",
         "SELECT round(value, 2) AS roc_auc\nFROM analytics.v_model_evaluations\n"
         "WHERE model_name = 'churn' AND is_selected AND split = 'out_of_time_test' AND metric = 'roc_auc'"),
    ),
    _churn,
)


# ---------------------------------------------------------------- 5. High sales, low margin

def _hrlm(r):
    rows = r[0]
    if not rows:
        return "Ningún producto combina ventas por encima de la mediana con margen estimado en el cuartil inferior."
    cats = sorted({x["category"] for x in rows})
    return "\n".join([
        f"**{len(rows)} productos** venden por encima de la mediana pero tienen un margen estimado en el 25% más bajo "
        f"(entre productos con al menos 10 unidades). El mayor es {rows[0]['product_short_id']} ({_nice(rows[0]['category'])}): "
        f"{_brl(rows[0]['revenue'])} con {_pct(rows[0]['margin_pct'], False)} de margen estimado.",
        "",
        *[f"- {x['product_short_id']} ({_nice(x['category'])}): {x['units']:,} unidades, {_brl(x['revenue'])}, margen {_pct(x['margin_pct'], False)}." for x in rows[1:5]],
        f"- Categorías presentes: {', '.join(_nice(c) for c in cats[:6])}.",
        "",
        "Nota: el margen es **estimado** con un modelo de costos sintético; este resultado depende de esos supuestos.",
    ])


HRLM = Guided(
    "high_sales_low_margin", "¿Qué productos tienen alta venta pero bajo margen?",
    ("productos", "producto", "alta venta", "bajo margen", "venden", "margen", "products", "low margin", "high sales"),
    (
        ("Products with above-median revenue and bottom-quartile estimated margin (min 10 units)",
         "WITH e AS (SELECT * FROM analytics.v_product_performance WHERE units >= 10),\n"
         "t AS (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY revenue) AS med_rev,\n"
         "             percentile_cont(0.25) WITHIN GROUP (ORDER BY estimated_margin) AS p25_margin FROM e)\n"
         "SELECT e.product_short_id, e.category, e.units, round(e.revenue, 2) AS revenue, round(100 * e.estimated_margin, 1) AS margin_pct\n"
         "FROM e CROSS JOIN t\nWHERE e.revenue > t.med_rev AND e.estimated_margin < t.p25_margin\nORDER BY e.revenue DESC\nLIMIT 20"),
    ),
    _hrlm,
)


# ---------------------------------------------------------------- Extra questions

def _late(r):
    rows = {x["delivery"]: x for x in r[0]}
    late, ontime = rows["late"], rows["on time"]
    return "\n".join([
        f"Sí: los pedidos entregados con retraso reciben **{late['avg_review']:.2f}★** de media frente a **{ontime['avg_review']:.2f}★** "
        f"de los puntuales.",
        "",
        f"- Reseñas de 1★: {_pct(late['one_star_pct'], False)} en pedidos tardíos vs {_pct(ontime['one_star_pct'], False)} en puntuales.",
        f"- Pedidos tardíos analizados: {late['orders']:,}; puntuales: {ontime['orders']:,}.",
    ])


LATE = Guided(
    "late_reviews", "¿Cómo afectan los retrasos en la entrega a las reseñas?",
    ("retraso", "retrasos", "entrega", "reseñas", "resenas", "valoracion", "late", "delivery", "reviews", "rating"),
    (
        ("Average review and share of 1-star reviews for late vs on-time orders",
         "SELECT CASE WHEN is_late THEN 'late' ELSE 'on time' END AS delivery, count(*) AS orders,\n"
         "       round(avg(review_score), 2) AS avg_review, round(100.0 * avg((review_score <= 1)::int), 1) AS one_star_pct\n"
         "FROM analytics.v_orders\nWHERE is_valid_sale AND is_late IS NOT NULL AND review_score IS NOT NULL\nGROUP BY 1"),
    ),
    _late,
)


DAYS_ES = {"Monday": "lunes", "Tuesday": "martes", "Wednesday": "miércoles", "Thursday": "jueves", "Friday": "viernes",
           "Saturday": "sábado", "Sunday": "domingo"}


def _weekday(r):
    rows = [dict(x, day_name=DAYS_ES.get(x["day_name"], x["day_name"])) for x in r[0]]
    best, worst = rows[0], rows[-1]
    return "\n".join([
        f"El día con más pedidos es el **{best['day_name']}** ({best['orders']:,} pedidos en todo el periodo) y el que menos, "
        f"el **{worst['day_name']}** ({worst['orders']:,}).",
        "",
        *[f"- {x['day_name'].capitalize()}: {x['orders']:,} pedidos, {_brl(x['revenue'])}." for x in rows],
    ])


WEEKDAY = Guided(
    "best_weekday", "¿Qué día de la semana se vende más?",
    ("dia", "semana", "dias", "vende mas", "weekday", "day", "week", "busiest"),
    (
        ("Orders and revenue by weekday",
         "SELECT c.day_name, c.day_of_week, count(*) AS orders, round(sum(o.revenue), 2) AS revenue\n"
         "FROM analytics.v_orders o JOIN core.calendar c ON c.date_key = o.purchase_date\n"
         "WHERE o.is_valid_sale\nGROUP BY c.day_name, c.day_of_week\nORDER BY orders DESC"),
    ),
    _weekday,
)


def _states(r):
    rows = r[0]
    return "\n".join([
        f"**{rows[0]['state_name']}** concentra la mayor parte de las ventas: {_brl(rows[0]['revenue'])} "
        f"({_pct(rows[0]['share_pct'], False)} del total).",
        "",
        *[f"- {x['state_name']}: {_brl(x['revenue'])} ({_pct(x['share_pct'], False)})." for x in rows[1:5]],
    ])


STATES = Guided(
    "top_states", "¿Qué estados generan más ventas?",
    ("estados", "estado", "regiones", "donde", "states", "state", "where", "region"),
    (
        ("Revenue and share by state",
         "SELECT state_name, round(revenue, 2) AS revenue, round(100 * revenue / sum(revenue) OVER (), 1) AS share_pct\n"
         "FROM analytics.v_geo_state\nORDER BY revenue DESC\nLIMIT 10"),
    ),
    _states,
)

LIBRARY: tuple[Guided, ...] = (WHY_SALES, PROFITABLE, CITIES, CHURN, HRLM, LATE, WEEKDAY, STATES)


# ---------------------------------------------------------------- Matching and execution

def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", text)


def match(question: str) -> tuple[Guided | None, float]:
    """Pick the library question whose keywords overlap most with the user's question."""
    q = f" {_normalise(question)} "
    best, best_score = None, 0.0
    for item in LIBRARY:
        if _normalise(question).strip() == _normalise(item.question).strip():
            return item, 1.0
        hits = sum(1 for k in item.keywords if f" {_normalise(k)} " in q or _normalise(k) in q.split())
        score = hits / min(len(item.keywords), 4)
        if score > best_score:
            best, best_score = item, score
    return (best, best_score) if best_score >= 0.5 else (None, best_score)


def ask(question: str) -> AnalystResult:
    started = time.perf_counter()
    question = " ".join((question or "").split())
    result = AnalystResult(question=question, status="cannot_answer", answer="", model=MODE_NAME,
                           db_role=sql_generator.db_role())
    item, _ = match(question)
    if item is None:
        result.answer = ("El modo guiado (sin IA) solo responde preguntas de su biblioteca. Prueba una de estas:\n"
                         + "\n".join(f"- {g.question}" for g in LIBRARY)
                         + "\n\nPara preguntas libres, activa un modelo de IA (Ollama local gratuito o Claude).")
        result.elapsed_ms = round((time.perf_counter() - started) * 1000)
        return result

    steps = [sql_generator.run_sql(sql, purpose) for purpose, sql in item.steps]
    result.steps = [asdict(s) for s in steps]
    failed = [s for s in steps if s.error]
    if failed:
        result.status = "error"
        result.answer = f"Una de las consultas falló: {failed[0].error}"
    else:
        result.status = "answered"
        result.answer = item.interpret([_rows(s) for s in result.steps])
        result.unverified_numbers = unverified_numbers(result.answer, question, [s["rows"] for s in result.steps],
                                                       [s["sql"] for s in result.steps])
    result.elapsed_ms = round((time.perf_counter() - started) * 1000)
    return result
