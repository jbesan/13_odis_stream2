# ---- Builder Stage ----
# Use a full-featured Python image to build dependencies
FROM python:3.11-slim-bookworm as builder

# Install build dependencies for geospatial libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

# Set up a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install Python requirements
RUN pip install --upgrade pip
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Final Stage ----
FROM python:3.11-slim-bookworm

# Environment optimizations
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

# Install system dependencies (minimal)
# We might need libgdal if binary wheels aren't self-contained, but usually they are.
# Keeping it empty for now to minimize size.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r odis && useradd -r -m -g odis odis

# Set the working directory
WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

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
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --server.enableWebsocketCompression=false