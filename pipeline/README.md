# ODIS Data Pipeline

This directory contains the "offline" ETL (Extract, Transform, Load) pipeline for the ODIS application. Its purpose is to fetch, clean, and aggregate data from various sources into optimized Parquet files that the Streamlit app can load instantly.

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Virtual environment set up

### Installation

```bash
# Activate your virtual environment
source .venv/bin/activate

# Install pipeline dependencies
pip install -r pipeline/requirements.txt
```

### Usage

Run the pipeline using the `etl.py` script from the project root:

```bash
# Run the full pipeline (Ingest + Build + Prescoring + Deploy)
python -m pipeline.etl --step all

# Run only the Ingest step (Fetch & Clean)
python -m pipeline.etl --step ingest

# Run only the Build step (Aggregate & Export)
python -m pipeline.etl --step build

# Run only the Prescoring step (Ratios & Scaling)
python -m pipeline.etl --step prescoring

# Run only the Deploy step (Copy to app data dir)
python -m pipeline.etl --step deploy
```

Optional flags:
- `--skip-live-jobs`: Skip fetching live job offers from France Travail.
- `--skip-inclusion-jobs`: Skip fetching jobs from Les emplois de l'inclusion.

## 📂 Architecture

The pipeline is split into several main stages:

1.  **Ingest (`ingest.py`)**: Fetches raw data and cleans it into intermediate Parquet files. Includes `fetch_rna_rag_stats` for BigQuery RAG metrics.
2.  **Build (`build.py`)**: Aggregates cleaned data into final ODIS artifacts (joins, geometry operations).
3.  **Prescoring (`prescoring.py`)**: Calculates ratios, densities, and pre-scales scores for performance.
4.  **Live Ingest (`ft_live_ingest.py`)**: Fetches real-time job offers from France Travail API and aggregates them.
5.  **Inclusion Ingest (`emplois_inclusion_ingest.py`)**: Authenticates via API Token (using `EMPLOIS_INCLUSION_LOGIN` and `EMPLOIS_INCLUSION_PWD` in `.env`) to fetch granular job openings from Les emplois de l'inclusion API.
6.  **RAG Enrichment**: The `fetch_rna_rag_stats` step queries BigQuery to find associations relevant to social inclusion (using the `is_inclusion_relevant` flag) and groups them by RAG categories.
7.  **J'Accueille Upload (`upload_jaccueille_bq.py`)**: A utility script to upload sensitive host counts to BigQuery (`odis-stream2.jaccueille.jaccueille_accueillants_bdv`). This data is then fetched dynamically by the app.
8.  **Deploy (`etl.py`)**: Copies final artifacts to the application's data directory.

### File Structure

- **`etl.py`**: CLI entry point and deployment logic.
- **`ingest.py`**: Handles downloading and cleaning.
- **`build.py`**: Handles joining, aggregation, and initial export.
- **`prescoring.py`**: Handles scoring logic, ratios, and scaling.
- **`common.py`**: Shared utilities (logging, config, IO).
- **`sources.yaml`**: Configuration file defining all data sources.
- **`prescoring_config.yaml`**: Configuration for metrics and inputs (e.g. socle admin).
- **`cache/`**:
  - `raw/`: Raw downloaded files (zips, excel, etc.).
  - `clean/`: Intermediate cleaned Parquet files.
  - `output/`: Final Parquet files for the app.
- **`status.json`**: Execution logs.

## 🛠 Configuration (`sources.yaml`)

Data sources are defined in `pipeline/sources.yaml`. Each entry specifies:

- `url`: Download URL.
- `format`: File format (csv, xlsx, zip, etc.).
- `local_name`: Filename for the cached file.
- `archive_file`: (Optional) Specific file to extract if the source is a Zip archive.
- `sheet_name`: (Optional) Sheet to read for Excel files.

## 📦 Outputs

The pipeline generates the following Parquet files in `pipeline/cache/output/` and deploys them to `data/`:

| File                                    | Description                                | Key Columns                                                                                                                                |
| --------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **`odis_communes.parquet`**             | Main dataset at Commune level.             | `codgeo`, `population`, `pop_active`, `met_ratio`, `met_tension_ratio`, `pop_chomage_ratio`, `bassin_de_vie`, `polygon`, `*_scaled` scores |
| **`odis_bassins_de_vie.parquet`**       | Aggregated dataset at Bassin de Vie level. | `bassin_de_vie`, `libelle_bassin_de_vie`, `population_bv`, `pop_active`, `pop_chomage_ratio`, `geometry` (dissolved)                       |
| **`odis_associations_agg.parquet`**     | Aggregated counts from RNA.                | `codgeo`, `inc_rna_{category}_count`                                                                                                       |
| **`odis_pois.parquet`**                 | Points of Interest for map layers.         | `id`, `type`, `lat`, `lon`, `metadata`                                                                                                     |
| **`odis_referentiels.parquet`**         | Reference tables for UI dropdowns.         | `type`, `code`, `label`                                                                                                                    |
| **`odis_formations_agg.parquet`**       | Aggregated training centers.               | `codgeo`, `form_count`                                                                                                                     |
| **`odis_ccas.parquet`**                 | CCAS locations.                            | `codgeo`, `name`, `address`                                                                                                                |
| **`odis_refugee_associations.parquet`** | Detailed Refugee Associations List.        | `id`, `codgeo`, `bassin_de_vie`, `name`, `description`, `waldec_code`                                                                      |
| **`odis_ft_jobs_agg.parquet`**          | Live Employment counts (France Travail).   | `commune`, `romeCode`, `romeLibelle`, `total_postes`, `nb_offres_tension`                                                                  |
| **`odis_inclusion_jobs.parquet`**       | Granular Inclusion Job offers.             | `codgeo`, `siae_siret`, `siae_name`, `siae_type`, `rome`, `postes`                                                                         |

## 🔄 Data Flow

1.  **Ingest (`ingest.py`)**:
    - **Fetch**: Downloads raw files to `pipeline/cache/raw/`.
    - **Clean**: Reads raw files, fixes types/columns, and saves to `pipeline/cache/clean/*.parquet`.
2.  **Build (`build.py`)**:
    - **Load**: Reads cleaned files.
    - **Join**: Merges all indicators onto the Communes base.
    - **Aggregate**: Dissolves geometries for Bassins de Vie.
    - **Export**: Saves initial files to `pipeline/cache/output/`.
3.  **Prescoring (`prescoring.py`)**:
    - **Load**: Reads `odis_communes.parquet` from output.
    - **Calculate**: Computes ratios (e.g., `met_ratio`, `log_vac_struct_ratio`) and densities.
    - **Scale**: Min-max scales scores (e.g., `met_scaled`, `inc_lien_social_score`).
    - **Update**: Overwrites `odis_communes.parquet` with enriched data.
4.  **Deploy (`etl.py`)**:
    - **Check Freshness**: Interactively evaluates the TTL (7 days) for live data APIs like France Travail and Les emplois de l'inclusion, prompting to refresh if needed.
    - **Copy**: Moves generated files from `pipeline/cache/output/` to `data/` for the application to use.
