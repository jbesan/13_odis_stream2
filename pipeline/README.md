# ODIS Ingestion & Build Pipeline - Execution Guide

This document describes how to configure and execute the offline ETL pipeline for ODIS. For the design specifications, data contracts, and optimizations, see [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md).

---

## 🚀 Quick Start

### 1. Setup Environment
Configure the Odace credentials in `pipeline/.env`:
```env
ODACE_API_URL=https://odace.services.d4g.fr
ODACE_API_KEY=sk_live_...
```

### 2. Execution
All pipeline modules must be executed from the project root directory:

```bash
# Run the full pipeline (Ingest + Build + Prescoring + Deploy)
uv run python -m pipeline.etl --step all

# Run specific phase (ingest, build, prescoring, deploy)
uv run python -m pipeline.etl --step ingest

# Target a specific table / dataset across steps
uv run python -m pipeline.etl --step all --table communes
uv run python -m pipeline.etl --step ingest --table population,caf
```

---

## 📋 Execution Steps Catalog

* **`ingest`**: Queries the Odace API and external endpoints (France Travail, Les emplois de l'inclusion, BigQuery RNA RAG) to download and clean raw staging datasets.
* **`build`**: Resolves geographical PLM hierarchies, consolidates arrondissement rates, and dissolves geometries.
* **`prescoring`**: Pre-calculates static indicators and performs quantile rank normalizations.
* **`deploy`**: Copies only the bootstrap files locally, publishes the generated Parquets (except `odis_referentiels.parquet`) to an immutable GCS release, then updates `datasets/current.json` as the atomic release pointer. Cloud Run downloads the active release on first use and caches it in `/tmp`.
