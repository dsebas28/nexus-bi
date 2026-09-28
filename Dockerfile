# NEXUS BI: API + dashboard + data pipeline + models (one image, different commands).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp1: OpenMP runtime needed by XGBoost and scikit-learn.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY data_pipeline ./data_pipeline
COPY machine_learning ./machine_learning
COPY ai_analyst ./ai_analyst
COPY sql ./sql

# Run as an unprivileged user; data/ (raw downloads) and model artifacts must be writable.
RUN useradd --create-home --uid 1000 nexus \
    && mkdir -p data/raw \
    && chown -R nexus:nexus /app
USER nexus

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
