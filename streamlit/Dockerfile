# ---- Builder Stage ----
# Use a full-featured Python image to build dependencies
FROM python:3.9-slim-bullseye as builder

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
COPY streamlit/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Final Stage ----
# Use a minimal Python image
FROM python:3.9-slim-bullseye

# Install runtime dependencies for geospatial libraries, required by geopandas
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal28 \
    libgeos-c1v5 \
    libproj19 \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy the application code
COPY streamlit/ .

# Make the virtual environment's Python the default
ENV PATH="/opt/venv/bin:$PATH"

# Expose the Streamlit port
EXPOSE 8080

# Run the Streamlit app
CMD ["streamlit", "run", "main.py", "--server.port=8080", "--server.address=0.0.0.0"]
