# Multi-stage build for pr-sentinel
# Stage 1: build dependencies + wheel
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install ".[openai,langgraph]"

# Stage 2: runtime
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages (includes pr_sentinel + all deps)
COPY --from=builder /install /usr/local

# Copy application code and assets
COPY src/ src/
COPY migrations/ migrations/
COPY entrypoint.sh ./
COPY pyproject.toml ./

RUN chmod +x entrypoint.sh

# Non-root user
RUN useradd --create-home sentinel
USER sentinel

EXPOSE 8000 8001

# SERVICE_TYPE env var selects: webhook | worker | dashboard
ENTRYPOINT ["./entrypoint.sh"]
