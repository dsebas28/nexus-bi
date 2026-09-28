"""AI Data Analyst endpoints ("Ask your data").

Three modes, chosen automatically unless AI_PROVIDER forces one:
  anthropic  Claude writes and iterates on SQL (paid API, best quality)
  ollama     a local open model writes the SQL (free, runs on this machine)
  guided     a library of questions with hand-written SQL (free, no AI, always available)
All three share the SQL validator, read-only execution and the figure check.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ai_analyst import analyst, guided, local_llm, sql_generator

from ..config import get_settings

router = APIRouter(prefix="/ai", tags=["AI analyst"])

AI_EXAMPLES = [
    "¿Por qué disminuyeron las ventas este mes?",
    "¿Cuál es la categoría más rentable?",
    "¿Qué ciudades están creciendo más?",
    "¿Qué clientes presentan mayor riesgo de abandono?",
    "¿Qué productos tienen alta venta pero bajo margen?",
    "How much revenue did Black Friday 2017 generate compared with a normal Friday?",
]
MODE_LABELS = {"anthropic": "Claude", "ollama": "Local AI (Ollama)", "guided": "Guided mode (no AI)"}

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def resolve_mode() -> str:
    provider = get_settings().ai_provider
    if provider == "anthropic":
        return "anthropic" if analyst.is_configured() else "guided"
    if provider == "ollama":
        return "ollama" if local_llm.available() else "guided"
    if provider == "guided":
        return "guided"
    if analyst.is_configured():
        return "anthropic"
    return "ollama" if local_llm.available() else "guided"


def _model_label(mode: str) -> str:
    s = get_settings()
    return {"anthropic": s.ai_model, "ollama": f"{s.ollama_model} (local)", "guided": guided.MODE_NAME}[mode]


def _rate_limit(client_id: str) -> None:
    """Sliding one-minute window per client (AI questions cost money or local compute)."""
    limit = get_settings().ai_rate_limit_per_minute
    now = time.monotonic()
    with _lock:
        q = _hits[client_id]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(status_code=429, detail=f"Limit of {limit} questions per minute reached. Wait a moment.")
        q.append(now)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=analyst.MAX_QUESTION_CHARS,
                          examples=["¿Cuál es la categoría más rentable?"])
    mode: Literal["auto", "guided"] = Field("auto", description="'guided' forces the no-AI question library")


class QueryStepOut(BaseModel):
    purpose: str
    sql: str
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    error: str | None
    duration_ms: float


class AskResponse(BaseModel):
    question: str
    mode: str
    status: str = Field(description="answered | cannot_answer | error")
    answer: str
    steps: list[QueryStepOut]
    unverified_numbers: list[str] = Field(description="Figures in the answer that do not appear in any query result")
    model: str
    usage: dict
    elapsed_ms: float
    db_role: str


class StatusResponse(BaseModel):
    mode: Literal["anthropic", "ollama", "guided"]
    mode_label: str
    model: str
    ai_available: bool = Field(description="True when an AI model (Claude or local) answers free-form questions")
    db_role: str
    requires_token: bool
    rate_limit_per_minute: int
    examples: list[str]
    guided_questions: list[str]


@router.get("/status", response_model=StatusResponse, summary="Which analyst mode is active")
def get_status():
    s = get_settings()
    mode = resolve_mode()
    return StatusResponse(
        mode=mode, mode_label=MODE_LABELS[mode], model=_model_label(mode), ai_available=mode != "guided",
        db_role=sql_generator.db_role(), requires_token=s.ai_access_token is not None,
        rate_limit_per_minute=s.ai_rate_limit_per_minute,
        examples=AI_EXAMPLES if mode != "guided" else [g.question for g in guided.LIBRARY],
        guided_questions=[g.question for g in guided.LIBRARY],
    )


@router.post("/ask", response_model=AskResponse,
             summary="Answer a question with validated, read-only SQL (Claude, local model or guided library)")
def ask(body: AskRequest, request: Request, x_access_token: str | None = Header(None)):
    s = get_settings()
    if s.ai_access_token is not None and not secrets.compare_digest(x_access_token or "",
                                                                    s.ai_access_token.get_secret_value()):
        raise HTTPException(status_code=401, detail="A valid X-Access-Token header is required.")
    _rate_limit(request.client.host if request.client else "unknown")
    mode = "guided" if body.mode == "guided" else resolve_mode()
    try:
        if mode == "anthropic":
            result = analyst.ask(body.question)
        elif mode == "ollama":
            result = local_llm.ask(body.question)
        else:
            result = guided.ask(body.question)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result.to_dict(), "mode": mode}
