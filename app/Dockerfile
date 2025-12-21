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
# Use a minimal Python image
FROM python:3.11-slim-bookworm

# Install system dependencies
# Install system dependencies (minimal)
# We might need libgdal if binary wheels aren't self-contained, but usually they are.
# Keeping it empty for now to minimize size.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy the data directory
# We copy it to /data because config.py expects it at PROJECT_ROOT/data/
# In the container:
# WORKDIR /app
# APP_DIR (where config.py is) = /app
# PROJECT_ROOT = /
# So we need data at /data
COPY data/ /data/

# Copy the application code
COPY app/ .

# Make the virtual environment's Python the default
ENV PATH="/opt/venv/bin:$PATH"

# Expose the Streamlit port
EXPOSE 8080

# The critical command
# We explicitly use the $PORT variable provided by Cloud Run
# We disable CORS and XSRF because Cloud Run's proxy handles the security
CMD streamlit run 1_Accueil.py \
    --server.port=8080 \
    --server.address=0.0.0.0 \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --server.enableWebsocketCompression=false