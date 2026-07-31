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
# Build a full candidate (Ingest + Build + Prescoring). Deploy is explicit.
uv run python -m pipeline.etl --step all

# Run specific phase (ingest, build, prescoring, deploy)
uv run python -m pipeline.etl --step ingest

# Target a specific table / dataset across steps
uv run python -m pipeline.etl --step all --table communes
uv run python -m pipeline.etl --step ingest --table population,caf
```

### Run-scoped candidates and deployment

Each non-deployment invocation creates (or resumes with `--run-id`) an isolated
candidate under `pipeline/cache/runs/<run_id>/`. Its `run.json`, quality report,
manifest, and generated datasets must all belong to the same run. A failed run
is never deployable and does not change the active GCS release.

```bash
# Build a complete candidate. The command prints its generated run ID.
uv run python -m pipeline.etl --step all

# Continue an incomplete candidate with the printed ID.
uv run python -m pipeline.etl --step prescoring --run-id run-20260731T120000Z-ab12cd34

# Deploy only a candidate whose run record and quality gate both passed.
uv run python -m pipeline.etl --step deploy --run-id run-20260731T120000Z-ab12cd34
```

Do not deploy from the legacy `pipeline/cache/output` directory. It is no longer
a release source of truth.

---

## 📋 Execution Steps Catalog

* **`ingest`**: Queries the Odace API and external endpoints (France Travail, Les emplois de l'inclusion, BigQuery RNA RAG) to download and clean raw staging datasets.
* **`build`**: Resolves geographical PLM hierarchies, consolidates arrondissement rates, and dissolves geometries.
* **`prescoring`**: Pre-calculates static indicators and performs quantile rank normalizations.
* **`deploy`**: Requires a passed `--run-id`, publishes that candidate's generated Parquets (except `odis_referentiels.parquet`) to a GCS release, then updates `datasets/current.json` as the atomic release pointer. Cloud Run downloads the active release on first use and caches it in `/tmp`.
