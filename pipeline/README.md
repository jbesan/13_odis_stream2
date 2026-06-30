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
python -m pipeline.etl --step all

# Run specific steps (ingest, build, prescoring, deploy)
python -m pipeline.etl --step ingest
```

---

## 📋 Execution Steps Catalog

* **`ingest`**: Queries the Odace API and external endpoints (France Travail, Les emplois de l'inclusion, BigQuery RNA RAG) to download and clean raw staging datasets.
* **`build`**: Resolves geographical PLM hierarchies, consolidates arrondissement rates, and dissolves geometries.
* **`prescoring`**: Pre-calculates static indicators and performs quantile rank normalizations.
* **`deploy`**: Swaps cache targets atomically to update the application's reference parquet files.
