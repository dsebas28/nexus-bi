"""Free local AI through Ollama (https://ollama.com): an open model on this machine, no API costs.

Small local models follow multi-turn tool loops poorly, so this path is a fixed
two-call workflow with JSON output constrained by a schema:

    1. plan:   question -> {answerable, reason, queries: [{purpose, sql}]}   (1-3 queries)
               each query -> validation.validate_sql -> PostgreSQL (read-only)
               a failing query gets ONE repair attempt with the error message
    2. answer: question + query results -> {status, answer}

The same SQL validator, read-only execution and figure check as the Claude path apply.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import asdict

from backend.config import get_settings

from . import sql_generator
from .analyst import MAX_QUESTION_CHARS, AnalystResult
from .validation import unverified_numbers

log = logging.getLogger(__name__)

MAX_QUERIES = 3
_availability: dict = {"checked": 0.0, "ok": False, "models": []}
# Set when Ollama's GPU backend crashes (typically an outdated NVIDIA driver); later calls go straight to CPU.
_gpu_failed = False


class LocalModelError(RuntimeError):
    """Ollama answered, but with an error (message is safe to show)."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "reason": {"type": "string"},
        "queries": {"type": "array", "maxItems": MAX_QUERIES, "items": {
            "type": "object", "properties": {"purpose": {"type": "string"}, "sql": {"type": "string"}},
            "required": ["purpose", "sql"]}},
    },
    "required": ["answerable", "reason", "queries"],
}
QUERY_SCHEMA = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string", "enum": ["answered", "cannot_answer"]}, "answer": {"type": "string"}},
    "required": ["status", "answer"],
}


def _post(path: str, payload: dict, timeout: float) -> dict:
    url = get_settings().ollama_url.rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def available() -> bool:
    """True when Ollama answers and the configured model is downloaded (cached for 30 s)."""
    now = time.monotonic()
    if now - _availability["checked"] < 30:
        return _availability["ok"]
    settings = get_settings()
    try:
        with urllib.request.urlopen(settings.ollama_url.rstrip("/") + "/api/tags", timeout=1.5) as resp:
            models = [m["name"] for m in json.loads(resp.read().decode()).get("models", [])]
        ok = any(m == settings.ollama_model or m.split(":")[0] == settings.ollama_model for m in models)
    except (urllib.error.URLError, OSError, ValueError):
        models, ok = [], False
    _availability.update(checked=now, ok=ok, models=models)
    return ok


def _chat(messages: list[dict], schema: dict) -> dict:
    global _gpu_failed
    settings = get_settings()
    options = {"temperature": 0, "num_ctx": 8192}
    if settings.ollama_num_gpu is not None:
        options["num_gpu"] = settings.ollama_num_gpu
    elif _gpu_failed:
        options["num_gpu"] = 0
    # keep_alive keeps the model in memory between questions (loading it takes ~20 s on CPU).
    payload = {"model": settings.ollama_model, "messages": messages, "stream": False, "format": schema,
               "options": options, "keep_alive": "30m"}
    try:
        data = _post("/api/chat", payload, timeout=900)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if "CUDA" in detail and options.get("num_gpu") != 0:
            # The GPU runner crashed: retry on CPU and remember it for this session.
            _gpu_failed = True
            payload["options"] = {**options, "num_gpu": 0}
            try:
                data = _post("/api/chat", payload, timeout=900)
            except urllib.error.HTTPError as retry_exc:
                raise LocalModelError(f"Ollama error {retry_exc.code}: {retry_exc.read().decode('utf-8', 'replace')[:200]}") from retry_exc
        else:
            raise LocalModelError(f"Ollama error {exc.code}: {detail[:200]}") from exc
    log.info("ollama: prompt %s tokens in %.1fs, output %s tokens in %.1fs", data.get("prompt_eval_count"),
             (data.get("prompt_eval_duration") or 0) / 1e9, data.get("eval_count"), (data.get("eval_duration") or 0) / 1e9)
    return json.loads(data["message"]["content"])


def running_on_cpu() -> bool:
    settings = get_settings()
    return settings.ollama_num_gpu == 0 or (settings.ollama_num_gpu is None and _gpu_failed)


PLAN_PROMPT = """You translate business questions into PostgreSQL for the NEXUS BI analytics database.

Rules:
- Write read-only SELECT queries (CTEs allowed) using ONLY the views listed below, schema-qualified (analytics.v_orders).
- Compute every derived number (percent change, difference, share, rank) inside SQL. Round money to 2 decimals.
- Keep results small: aggregate, ORDER BY and LIMIT 20 or fewer rows.
- "This month" / "last month" means the last complete month in analytics.v_reporting_period, compared with the month before.
- Ratios in the views are fractions (0.05 = 5%). Profit and margin are ESTIMATES.
- If the data cannot answer the question (marketing spend, product names, competitors, anything outside this database),
  set answerable=false, explain why in reason, and return no queries.
- Use 1 to 3 queries.
- Only use columns listed for each view. Growth columns: growth_6m in geo views, growth_3m in v_category_performance.
- When ranking with ORDER BY ... DESC, add NULLS LAST. For growth rankings require a meaningful base
  (e.g. revenue_prev_6m >= 20000) so tiny cities with huge percentages do not dominate.
- "Most profitable" means highest estimated_profit; also return estimated_margin for context.

Return JSON: {"answerable": bool, "reason": str, "queries": [{"purpose": str, "sql": str}]}.
"""

ANSWER_PROMPT = """You are a business data analyst. Answer the user's question using ONLY the query results provided.

Rules:
- Every number you write must appear in the results. Never estimate or invent figures. Do not do arithmetic yourself.
- Answer in the same language as the question. Start with the direct answer in 1-2 sentences, then 2-4 short bullet points.
- Mention the period covered. Say "estimated" for profit or margin. Under 150 words. Plain Markdown, no tables, no SQL.
- If the results do not answer the question, set status to "cannot_answer" and say what is missing.

Return JSON: {"status": "answered" | "cannot_answer", "answer": str}.
"""


def ask(question: str) -> AnalystResult:
    settings = get_settings()
    question = " ".join((question or "").split())
    if not question or len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"Ask a question under {MAX_QUESTION_CHARS} characters.")
    started = time.perf_counter()
    result = AnalystResult(question=question, status="error", answer="", model=f"{settings.ollama_model} (local, Ollama)",
                           db_role=sql_generator.db_role())
    try:
        plan = _chat([{"role": "system", "content": PLAN_PROMPT + "\n\n" + sql_generator.compact_schema_context()},
                      {"role": "user", "content": question}], PLAN_SCHEMA)
        if not plan.get("answerable") or not plan.get("queries"):
            result.status = "cannot_answer"
            result.answer = plan.get("reason") or "The data in this platform cannot answer that question."
            return result

        for q in plan["queries"][:MAX_QUERIES]:
            step = sql_generator.run_sql(q.get("sql", ""), q.get("purpose", ""))
            result.steps.append(asdict(step))
            if step.error:   # one repair attempt, shown to the user as its own step
                fix = _chat([{"role": "system", "content": PLAN_PROMPT + "\n\n" + sql_generator.compact_schema_context()},
                             {"role": "user", "content": question},
                             {"role": "user", "content": f"This query failed:\n{step.sql}\n\nError: {step.error}\n"
                                                         "Return a corrected query as JSON {\"sql\": ...}."}], QUERY_SCHEMA)
                retry = sql_generator.run_sql(fix.get("sql", ""), f"{q.get('purpose', '')} (corrected)")
                result.steps.append(asdict(retry))

        ok = [s for s in result.steps if not s["error"]]
        if not ok:
            result.answer = "None of the generated queries could run. Try rephrasing the question more specifically."
            return result
        payload = [{"purpose": s["purpose"], "columns": s["columns"], "rows": s["rows"][:30]} for s in ok]
        final = _chat([{"role": "system", "content": ANSWER_PROMPT},
                       {"role": "user", "content": f"Question: {question}\n\nQuery results (JSON):\n{json.dumps(payload, default=str)}"}],
                      ANSWER_SCHEMA)
        result.status = final.get("status", "answered")
        result.answer = (final.get("answer") or "").strip() or "No answer was produced."
        result.unverified_numbers = unverified_numbers(result.answer, question, [s["rows"] for s in ok],
                                                       [s["sql"] for s in ok])
    except LocalModelError as exc:
        result.answer = f"The local model returned an error: {exc}"
    except (urllib.error.URLError, OSError) as exc:
        result.answer = f"The local model could not be reached at {settings.ollama_url} ({exc}). Is Ollama running?"
    except (ValueError, KeyError) as exc:
        result.answer = f"The local model returned an unexpected response ({exc}). Try again."
    finally:
        result.elapsed_ms = round((time.perf_counter() - started) * 1000)
        if running_on_cpu():
            result.model = f"{settings.ollama_model} (local, Ollama, CPU)"
    return result
