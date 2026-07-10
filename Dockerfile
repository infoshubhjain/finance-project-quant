# Alpha Engine — Multi-stage Docker build
# Usage:
#   docker build -t alpha-engine .
#   docker run --rm alpha-engine scan BTC
#   docker run --rm -p 8000:8000 alpha-engine dashboard

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /app

# Install system dependencies for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first (layer caching)
COPY pyproject.toml README.md ./
COPY src/ src/

# Runtime deps only: dev tools (pytest, ruff) have no business in the image,
# and a non-editable install is what the runtime stage's site-packages expects
RUN pip install --no-cache-dir .

# --- Stage 2: Runtime ---
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed package from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ src/
COPY web/ web/
COPY portfolio.json ./
COPY .env.example ./

# Create data directories
RUN mkdir -p data/cache/price data/cache/macro data/cache/chain data/signals

# Non-root user for security
RUN groupadd -r alpha && useradd -r -g alpha -d /app alpha \
    && chown -R alpha:alpha /app
USER alpha

# Default environment (no keys required)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check — verify the package imports
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import alpha_engine" || exit 1

# Default: show help
ENTRYPOINT ["python", "-m", "alpha_engine.cli.main"]
CMD ["--help"]
