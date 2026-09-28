"""Guards around the language model.

1. ``validate_sql`` parses every query the model writes (sqlglot, PostgreSQL
   dialect) and accepts it only if it is a single read-only SELECT over an
   allow-list of relations, without dangerous functions. The accepted query is
   wrapped in an outer LIMIT. This is one layer of three: the query also runs in
   a READ ONLY transaction with a statement timeout, under a database role that
   only has SELECT privileges (see sql/roles.sql).

2. ``unverified_numbers`` checks the final answer: every figure it states must
   match a value returned by one of the executed queries (within the rounding the
   text implies). Figures that cannot be matched are reported to the user instead
   of being silently trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

MAX_SQL_CHARS = 6_000
MAX_ROWS = 200   # rows fetched per query (one extra is fetched to detect truncation)

# Relations the analyst may read. Everything else, including system catalogs, is rejected.
ALLOWED_RELATIONS = frozenset({
    "analytics.v_reporting_period", "analytics.v_month_coverage", "analytics.v_daily_coverage",
    "analytics.v_sales_items", "analytics.v_orders", "analytics.v_monthly_kpis",
    "analytics.v_customer_summary", "analytics.v_product_performance", "analytics.v_category_performance",
    "analytics.v_geo_state", "analytics.v_geo_city",
    "analytics.v_customer_segments", "analytics.v_segment_summary", "analytics.v_churn_scores",
    "analytics.v_sales_forecast", "analytics.v_anomalies", "analytics.v_model_evaluations",
    "analytics.v_data_quality_latest",
    "core.states", "core.categories", "core.calendar", "core.order_reviews",
})
ALLOWED_TABLE_FUNCTIONS = frozenset({"analytics.kpi_summary", "analytics.previous_period"})

# Functions that could read files, sleep, change settings or reach other servers.
FORBIDDEN_FUNCTIONS = frozenset({
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_stat_file", "lo_import", "lo_export", "lo_get", "lo_put", "dblink", "dblink_exec", "set_config",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile", "query_to_xml",
    "query_to_json", "table_to_xml", "xpath", "pg_advisory_lock", "pg_advisory_xact_lock", "txid_current",
    "pg_notify", "copy",
})

FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
    exp.Into, exp.Lock, exp.Set, exp.Transaction, exp.Commit, exp.Rollback, exp.Copy, exp.Grant,
    exp.TruncateTable, exp.Use, exp.Pragma,
)


class SQLValidationError(ValueError):
    """The query was rejected; the message is safe to show to the model and the user."""


@dataclass(frozen=True)
class ValidatedSQL:
    original: str
    executable: str          # wrapped with the outer LIMIT
    relations: tuple[str, ...]


def _relation_name(table: exp.Table) -> str:
    return f"{table.db}.{table.name}".lower() if table.db else table.name.lower()


def validate_sql(sql: str) -> ValidatedSQL:
    if not sql or not sql.strip():
        raise SQLValidationError("The query is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise SQLValidationError(f"The query is longer than {MAX_SQL_CHARS} characters.")
    cleaned = sql.strip().rstrip(";").strip()

    try:
        statements = [s for s in sqlglot.parse(cleaned, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise SQLValidationError(f"The query could not be parsed: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLValidationError("Only SELECT queries are allowed.")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLValidationError(f"'{node.key.upper()}' is not allowed: the analyst is read-only.")

    for fn in tree.find_all(exp.Func):
        name = (fn.sql_name() if not isinstance(fn, exp.Anonymous) else fn.name).lower()
        if name in FORBIDDEN_FUNCTIONS:
            raise SQLValidationError(f"The function {name}() is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    relations = set()
    for table in tree.find_all(exp.Table):
        # Table functions such as analytics.kpi_summary(...) parse as a Table wrapping a call.
        if isinstance(table.this, (exp.Anonymous, exp.Func)):
            fname = f"{table.db}.{table.this.name}".lower() if table.db else str(table.this.name).lower()
            if fname not in ALLOWED_TABLE_FUNCTIONS:
                raise SQLValidationError(f"The function {fname}() cannot be used as a table.")
            relations.add(fname)
            continue
        name = _relation_name(table)
        if not table.db and name in cte_names:
            continue
        if not table.db:
            raise SQLValidationError(f"Use schema-qualified names (e.g. analytics.v_orders), not '{name}'.")
        if name not in ALLOWED_RELATIONS:
            raise SQLValidationError(f"'{name}' is not available to the analyst. Use the documented views.")
        relations.add(name)
    if not relations:
        raise SQLValidationError("The query does not read any of the documented views.")

    executable = f"SELECT * FROM (\n{cleaned}\n) AS nexus_query LIMIT {MAX_ROWS + 1}"
    return ValidatedSQL(original=cleaned, executable=executable, relations=tuple(sorted(relations)))


# -----------------------------------------------------------------------------
# Numeric grounding of the final answer
# -----------------------------------------------------------------------------

_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
# Numbers not glued to letters, so ids such as "d6160fb7" or "0a0a9211" are not read as figures.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])(?P<neg>[-−])?\s*(?:R\$\s*)?(?P<num>\d{1,3}(?:[,.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![0-9A-JLN-Za-jln-z])"
    r"\s*(?P<suffix>%|pp|pts?|K\b|M\b|mil\b|million\b|millones\b|thousand\b)?", re.IGNORECASE)
_SCALE = {"k": 1e3, "thousand": 1e3, "mil": 1e3, "m": 1e6, "million": 1e6, "millones": 1e6}


def _parse_number(raw: str) -> tuple[float, int]:
    """Parse '1,234.56', '1.234,56' or '12,5'. Returns (value, decimals shown)."""
    if "," in raw and "." in raw:
        decimal_sep = "," if raw.rfind(",") > raw.rfind(".") else "."
    elif "," in raw:
        decimal_sep = "," if len(raw.split(",")[-1]) != 3 else None
    elif "." in raw:
        decimal_sep = "." if len(raw.split(".")[-1]) != 3 or raw.count(".") == 1 and len(raw) <= 5 else None
    else:
        decimal_sep = None
    if decimal_sep:
        integer, frac = raw.rsplit(decimal_sep, 1)
        integer = re.sub(r"[.,]", "", integer)
        return float(f"{integer}.{frac}"), len(frac)
    return float(re.sub(r"[.,]", "", raw)), 0


def extract_numbers(text: str) -> list[dict]:
    text = _DATE.sub(" ", text)
    found = []
    for m in _NUMBER.finditer(text):
        value, decimals = _parse_number(m.group("num"))
        suffix = (m.group("suffix") or "").lower()
        scale = _SCALE.get(suffix, 1.0)
        found.append({
            "text": m.group(0).strip(),
            "value": (-1 if m.group("neg") else 1) * value * scale,
            "tolerance": 0.5 * 10 ** (-decimals) * scale,
            "is_percent": suffix in {"%", "pp", "pt", "pts"},
        })
    return found


def _numeric_cells(results: list[list[list]]) -> list[float]:
    cells = []
    for rows in results:
        for row in rows:
            for v in row:
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    cells.append(float(v))
    return cells


def unverified_numbers(answer: str, question: str, results: list[list[list]], sql_texts: list[str] = ()) -> list[str]:
    """Figures in ``answer`` that do not match any value returned by the queries.

    Constants written in the executed SQL (thresholds such as 20000, horizons such as 180) also count
    as grounded: they are visible to the user next to the answer.
    """
    cells = _numeric_cells(results) + [n["value"] for sql in sql_texts for n in extract_numbers(sql)]
    question_values = {round(n["value"], 6) for n in extract_numbers(question)}
    unverified = []
    for n in extract_numbers(answer):
        v, tol = n["value"], n["tolerance"]
        if round(v, 6) in question_values:
            continue
        if not n["is_percent"] and float(v).is_integer() and (abs(v) <= 12 or 2000 <= v <= 2030):
            continue   # ordinals, small counts ("top 5"), months and years are structural, not data
        candidates = [c * 100 for c in cells] + cells if n["is_percent"] else cells
        # Allow the rounding implied by the text, plus 0.5% relative slack for compact figures (R$ 1.2M).
        if not any(abs(abs(c) - abs(v)) <= max(tol, abs(v) * 0.005 if tol >= 1 else tol) for c in candidates):
            unverified.append(n["text"])
    return sorted(set(unverified), key=unverified.index)
