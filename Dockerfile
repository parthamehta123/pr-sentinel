# Multi-stage build for pr-sentinel
# Stage 1: build dependencies
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: runtime
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages
COPY --from=builder /install /usr/local

# Copy application code
COPY src/ src/
COPY migrations/ migrations/
COPY scripts/ scripts/
COPY pyproject.toml ./

# Install the package in editable mode
RUN pip install --no-cache-dir -e .

# Non-root user
RUN useradd --create-home sentinel
USER sentinel

EXPOSE 8000 8001

# Default: run the webhook ingress
CMD ["uvicorn", "pr_sentinel.ingress.app:app", "--host", "0.0.0.0", "--port", "8000"]
