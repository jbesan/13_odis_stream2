# ODIS Data Ingestion & Build Pipeline (Pipeline v3)

This directory contains the **offline ETL (Extract, Transform, Load) pipeline** for ODIS. It retrieves, sanitizes, and consolidates static and live open datasets into optimized Parquet stores loaded by the Streamlit application.

Pipeline v3 migrates ingestion to the new **Odace Silver API** (`https://odace.services.d4g.fr`) while keeping robust shadow-staging and legacy Open Data fallbacks for maximum resilience.

---

For instructions on configuring and running the pipeline steps, see [README.md](README.md).

---

## 📐 Pipeline v3 Architecture

```mermaid
graph TD
    A[sources.yaml Config] -->|use_odace toggle| B[ingest.py]
    B -->|API/Export| C{Odace Available?}
    C -->|Yes| D[Fetch Silver Data]
    C -->|No / Error| E[Fallback: Legacy Open Data / Local Cache]
    D & E -->|run_clean_step_safely| F[Verify Data Contract]
    F -->|Passed| G[Atomic Swap to Live Cache]
    F -->|Failed| H[Rollback to Last Good Cache]
```

### 1. Shadow Staging & Atomic Swaps
All ingestion tasks run in isolated staging buffers (`staging_*`) wrapping cleaners in `run_clean_step_safely`. If a cleaner fails or verification crashes, the pipeline rolls back and restores active backups (`*.active_bak`), protecting running app processes.

### 2. Declarative Schema Verification
Each dataset specifies `used_columns` in `sources.yaml`. The validation engine checks:
*   DataFrame non-emptiness.
*   Required columns and indices availability.
*   Geographical identifiers null rates (must be $< 5\%$).

---

## 🔄 Odace Integration & Ingestion Flows

### 1. Odace Silver Ingestion (`use_odace: true`)
The pipeline integrates 14 primary datasets directly from the Odace platform. To support large datasets and complex schemas without server timeouts:
*   **Paginated Query API (`/api/data/query`)**: `OdaceClient` auto-paginates queries by looping over `offset` and `has_more` to safely pull tables exceeding the 10,000-row API limit (e.g. `fact_population_municipale` at 34,998 rows).
*   **Parquet Export Streaming (`/api/data/export`)**: Heavy tables like BPE (`dim_equipement_territoire` >2.78M rows) and RNA (`dim_association`) stream pre-compiled Parquet export files directly.
*   **BPE Ingestion & Spatial Reprojection Optimization**: BPE is fetched directly from the Odace silver export API. It is filtered locally in Python to ODIS-relevant equipment codes (reducing rows to ~197k), which optimizes coordinate reprojection from Lambert-93 to WGS-84 to under 0.2 seconds.
*   **PLM Population Alignment**: Arrondissement populations (Paris, Lyon, Marseille) are fully populated in the cleaned population dataset. The build pipeline uses standard population-weighted average consolidation (removing simple mean fallbacks).

### 2. Live & Remote APIs
*   **France Travail Live Jobs**: Fetches real-time jobs and computes territorial stress metrics.
*   **Les emplois de l'inclusion**: Fetches SIAE jobs using token authentication.
*   **BigQuery RNA RAG Semantic Ingestion**: Queries vector-similarity association counts from BigQuery using cosine distance matching on inclusion embeddings.

---

## 💾 Decoupled Data & Spatial Optimization
To prevent Out-Of-Memory (OOM) failures in cloud environments, the pipeline implements a **WKB-until-render** architecture:
*   **`odis_communes.parquet`** contains metadata and scoring ranks. Polygons are stored strictly as **WKB (Well-Known Binary)** bytes.
*   Deserialization into Shapely/GeoPandas geometries occurs **Just-in-Time** (JIT) only when drawing maps in `maps.py` using `gpd.GeoSeries.from_wkb()`, minimizing start-up memory usage.

---

## 📜 Data Manifest Generation (`pipeline/manifest.py`)
At the end of the `prescoring` step (or full pipeline run), `DataManifestBuilder`:
1. Balaye les 36+ sources de `sources.yaml`.
2. Interroge l'API Catalogue Odace (`GET /api/data/catalog/silver/{table_name}`) pour les tables Odace.
3. Récupère les horodatages réels et volumétries depuis `pipeline/status.json` (ou l'horodatage `st_mtime` des fichiers locaux).
4. Calcule la version unique déterministe (`vYYYY.MM.DD-hash`) et écrit `data/processed/data_manifest.json` ainsi que `app/data/data_manifest.json` lors du déploiement.

---

## 🛠 File Roles

*   [etl.py](file:///Users/jacques/dev/13_odis_stream2/pipeline/etl.py): Main orchestrator for steps.
*   [ingest.py](file:///Users/jacques/dev/13_odis_stream2/pipeline/ingest.py): Downloads, page-loops, and cleans API/raw sources in staging.
*   [build.py](file:///Users/jacques/dev/13_odis_stream2/pipeline/build.py): Integrates clean tables, resolves PLM hierarchies, and dissolves spatial enclaves.
*   [prescoring.py](file:///Users/jacques/dev/13_odis_stream2/pipeline/prescoring.py): Scales final metrics using quantile rank scaling.
*   [common.py](file:///Users/jacques/dev/13_odis_stream2/pipeline/common.py): Caching, validation rules, and atomic file swap engines.
*   [sources.yaml](file:///Users/jacques/dev/13_odis_stream2/pipeline/sources.yaml): Configuration catalog for URLs, resource IDs, and schemas.
