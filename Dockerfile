# Multi-stage build: builder installs deps in a venv, final image copies only
# the venv and the application code — no compiler toolchain in production.
FROM python:3.11-slim AS builder

WORKDIR /build

RUN pip install --upgrade pip
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --target /build/packages

# --- final image ---
FROM python:3.11-slim

# Non-root user for the container process.
RUN useradd --uid 1000 --create-home appuser
WORKDIR /app
USER appuser

# Copy installed packages from the builder layer.
COPY --from=builder /build/packages /usr/local/lib/python3.11/site-packages

# Copy application source.
COPY --chown=appuser:appuser backend/ /app/

# Default environment (override at runtime with Docker env / compose env_file).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LLM_PROVIDER=groq \
    PREPR_VALIDATION=lint

EXPOSE 8077

# Health check so the orchestrator (Compose, ECS, Kubernetes) knows when the
# app is ready. The /health endpoint returns {"status":"ok"} once the process
# is up and Postgres is reachable (pool_pre_ping fires on first request).
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8077/health')"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8077"]
