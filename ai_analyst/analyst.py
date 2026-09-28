"""AI Data Analyst: question -> Claude writes SQL -> validation -> PostgreSQL -> grounded answer.

Flow (a bounded tool-use loop, not an open-ended agent):

    question
      -> Claude plans and calls run_sql(sql, purpose)          (up to MAX_QUERIES times)
         -> validation.validate_sql  (AST allow-list, read-only, LIMIT)
         -> PostgreSQL (READ ONLY transaction, statement timeout, read-only role)
         -> result rows returned to Claude
      -> Claude calls submit_answer(status, answer)
      -> validation.unverified_numbers  (every figure must appear in a result)
      -> response with the answer, every SQL query, its result and the verification status

Refusals and API errors are reported, never replaced by an invented answer.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

import anthropic

from backend.config import get_settings

from . import sql_generator
from .validation import unverified_numbers

log = logging.getLogger(__name__)

MAX_QUERIES = 6
MAX_TURNS = 10
MAX_QUESTION_CHARS = 500
FALLBACK_BETA = "server-side-fallback-2026-07-01"

TOOLS = [
    {
        "name": "run_sql",
        "description": (
            "Run one read-only PostgreSQL SELECT query against the NEXUS BI analytics views and get the rows back "
            "as JSON (at most 50 rows are shown to you). Use schema-qualified names (analytics.v_orders). "
            "Compute every derived number (changes, shares, differences) inside the query."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT (CTEs allowed). No DML/DDL."},
                "purpose": {"type": "string", "description": "One short sentence: what this query finds out."},
            },
            "required": ["sql", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_answer",
        "description": (
            "Submit the final answer to the user. Call exactly once, at the end. Use status 'cannot_answer' when the "
            "data cannot answer the question, and explain why in the answer."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["answered", "cannot_answer"]},
                "answer": {"type": "string", "description": "Markdown answer in the user's language, grounded in query results."},
            },
            "required": ["status", "answer"],
            "additionalProperties": False,
        },
    },
]


class AnalystUnavailable(RuntimeError):
    """The analyst is not configured (no API key)."""


@dataclass
class AnalystResult:
    question: str
    status: str                      # answered | cannot_answer | error
    answer: str
    steps: list[dict] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    db_role: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def is_configured() -> bool:
    key = get_settings().anthropic_api_key
    return key is not None and bool(key.get_secret_value())


def _client() -> anthropic.Anthropic:
    key = get_settings().anthropic_api_key
    if not key:
        raise AnalystUnavailable("Set ANTHROPIC_API_KEY in .env to enable the AI analyst.")
    return anthropic.Anthropic(api_key=key.get_secret_value(), max_retries=2, timeout=120.0)


def ask(question: str, client: anthropic.Anthropic | None = None) -> AnalystResult:
    settings = get_settings()
    question = " ".join((question or "").split())
    if not question:
        raise ValueError("Ask a question about the data.")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"Keep the question under {MAX_QUESTION_CHARS} characters.")
    client = client or _client()
    started = time.perf_counter()

    result = AnalystResult(question=question, status="error", answer="", model=settings.ai_model,
                           db_role=sql_generator.db_role())
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    system = [{"type": "text", "text": sql_generator.system_prompt(), "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": question}]
    queries = 0

    try:
        for _turn in range(MAX_TURNS):
            response = client.beta.messages.create(
                model=settings.ai_model,
                max_tokens=16000,
                system=system,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": settings.ai_effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            for key in usage:
                usage[key] += getattr(response.usage, key, 0) or 0
            result.model = getattr(response, "model", settings.ai_model)

            if response.stop_reason == "refusal":
                result.answer = "The model declined to answer this question. Try rephrasing it as a question about the sales data."
                break
            if response.stop_reason == "max_tokens":
                result.answer = "The analysis was cut off before it finished. Try a narrower question."
                break

            # Keep the full content (including thinking blocks) so the next turn continues correctly.
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                result.status, result.answer = ("answered", text) if text else ("error", "No answer was produced.")
                break

            submitted = next((t for t in tool_uses if t.name == "submit_answer"), None)
            if submitted is not None:
                result.status = submitted.input.get("status", "answered")
                result.answer = submitted.input.get("answer", "").strip()
                break

            tool_results = []
            for tool in tool_uses:
                if tool.name != "run_sql":
                    content, is_error = f"Unknown tool {tool.name}.", True
                elif queries >= MAX_QUERIES:
                    content, is_error = "Query budget used up. Call submit_answer now with what you have.", True
                else:
                    queries += 1
                    step = sql_generator.run_sql(tool.input.get("sql", ""), tool.input.get("purpose", ""))
                    result.steps.append(asdict(step))
                    content, is_error = step.for_model(), step.error is not None
                    log.info("AI query %d (%s ms, %s rows)%s", queries, step.duration_ms, step.row_count,
                             f" error: {step.error}" if step.error else "")
                tool_results.append({"type": "tool_result", "tool_use_id": tool.id, "content": content,
                                     **({"is_error": True} if is_error else {})})
            messages.append({"role": "user", "content": tool_results})
        else:
            result.answer = "The analysis took too many steps. Try a more specific question."
    except anthropic.AuthenticationError:
        result.answer = "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env."
    except anthropic.RateLimitError:
        result.answer = "The AI service is rate-limited right now. Try again in a minute."
    except anthropic.APIConnectionError:
        result.answer = "The AI service could not be reached. Check the internet connection."
    except anthropic.APIStatusError as exc:
        log.error("Anthropic API error %s: %s", exc.status_code, exc.message)
        result.answer = f"The AI service returned an error ({exc.status_code})."

    if result.status in ("answered", "cannot_answer") and result.answer:
        successful = [s["rows"] for s in result.steps if not s["error"]]
        result.unverified_numbers = unverified_numbers(result.answer, question, successful,
                                                       [s["sql"] for s in result.steps if not s["error"]])
    result.usage = usage
    result.elapsed_ms = round((time.perf_counter() - started) * 1000)
    return result
