"""NEXUS BI API application.

    uvicorn backend.main:app --reload            # development
    http://localhost:8000/docs                   # interactive documentation
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import PoolTimeout
from starlette.exceptions import HTTPException

from .api import api_router
from .config import PROJECT_ROOT, get_settings
from .models.database import close_pool, open_pool

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s")
log = logging.getLogger("nexus_bi.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    open_pool()
    yield
    close_pool()


app = FastAPI(
    title="NEXUS BI API",
    version="1.0.0",
    summary="AI-Powered Business Intelligence Platform: read-only analytics API over PostgreSQL.",
    description=(
        "Every number is computed from the Olist dataset stored in PostgreSQL. "
        "Profit and margin are **estimates** based on a synthetic cost model; all other metrics are real. "
        "Analytics endpoints accept the global filters `start`, `end`, `state` and `category`."
    ),
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Access-Token"],
    max_age=600,
)


@app.middleware("http")
async def timing_and_security_headers(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path.startswith("/api/"):
        log.info("%s %s -> %d (%.0f ms)", request.method, request.url.path, response.status_code, elapsed_ms)
    else:
        # Static assets: always revalidate (cheap 304 via ETag), so a deploy never mixes old and new ES modules.
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# --- Error handling: one JSON shape for every error, no internals leaked ---------------

def _error(status: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return _error(422, "invalid_request", "Some request parameters are invalid.", details)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    codes = {404: "not_found", 422: "invalid_request", 503: "service_unavailable"}
    return _error(exc.status_code, codes.get(exc.status_code, "error"), str(exc.detail))


@app.exception_handler(PoolTimeout)
@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(_: Request, exc: Exception):
    log.error("Database unavailable: %s", exc)
    return _error(503, "database_unavailable", "The database is temporarily unavailable.")


@app.exception_handler(psycopg.Error)
async def database_error(_: Request, exc: psycopg.Error):
    log.exception("Database error")
    return _error(500, "database_error", "The query could not be completed.")


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception):
    log.exception("Unexpected error")
    return _error(500, "internal_error", "An unexpected error occurred.")


app.include_router(api_router)

# The dashboard is served by the same app (one origin, no CORS needed in production).
# Mounted last so /api/v1 and /docs take precedence.
FRONTEND_DIR = PROJECT_ROOT / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
