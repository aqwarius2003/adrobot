# Build stage
FROM python:3.11-slim AS builder

WORKDIR /app

# Copy uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:0.6.10 /uv /uvx /bin/

# Copy dependency definition and lockfile
COPY pyproject.toml uv.lock .python-version ./

# Install runtime production dependencies into /app/.venv
RUN uv sync --frozen --no-dev --no-install-project


# Runtime stage
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/false -m appuser

# Copy virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV APP_ENV=production
ENV DATABASE_URL=sqlite:////app/data/app.db

# Ensure persistent data dir
RUN mkdir -p /app/data && chown -R appuser:appgroup /app/data

# Copy project files
COPY alembic.ini .
COPY alembic ./alembic
COPY src ./src

RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
