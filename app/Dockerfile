# ---- Builder Stage ----
FROM python:3.14-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Install dependencies using uv sync
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- Final Stage ----
FROM python:3.14-slim-bookworm

# Environment optimizations
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Install system dependencies (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r odis && useradd -r -m -g odis odis

# Set the working directory
WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy the application code
COPY --chown=odis:odis app/ .

# Switch to non-root user
USER odis

# Expose the Streamlit port
EXPOSE 8080

# The critical command
# We explicitly use the $PORT variable provided by Cloud Run
# We disable CORS and XSRF because Cloud Run's proxy handles the security
CMD python3 generate_secrets.py && streamlit run main.py \
    --server.port=8080 \
    --server.address=0.0.0.0 \
    --server.enableStaticServing=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --server.enableWebsocketCompression=true
