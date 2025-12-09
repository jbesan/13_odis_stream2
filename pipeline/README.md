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

## 📂 Architecture

The pipeline is split into four main stages:

1.  **Ingest (`ingest.py`)**: Fetches raw data and cleans it into intermediate Parquet files.
2.  **Build (`build.py`)**: Aggregates cleaned data into final ODIS artifacts (joins, geometry operations).
3.  **Prescoring (`prescoring.py`)**: Calculates ratios, densities, and pre-scales scores for performance.
4.  **Deploy (`etl.py`)**: Copies final artifacts to the application's data directory.

### File Structure

- **`etl.py`**: CLI entry point and deployment logic.
- **`ingest.py`**: Handles downloading and cleaning.
- **`build.py`**: Handles joining, aggregation, and initial export.
- **`prescoring.py`**: Handles scoring logic, ratios, and scaling.
- **`common.py`**: Shared utilities (logging, config, IO).
- **`sources.yaml`**: Configuration file defining all data sources.
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

| File                                | Description                                | Key Columns                                                                                                                                                                                                                          |
| ----------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`odis_communes.parquet`**         | Main dataset at Commune level.             | `codgeo`, `population`, `pop_active`, `metiers_offres_ratio`, `pop_chomage_ratio`, `bassin_de_vie`, `bassin_emploi`, `polygon`, `centroid`, `codgeo_voisins`, `edu_pe_tx_couverture`, `log_priv_vacant_plus_2ans`, `*_scaled` scores |
| **`odis_bassins_de_vie.parquet`**   | Aggregated dataset at Bassin de Vie level. | `bassin_de_vie`, `libelle_bassin_de_vie`, `population_bv`, `pop_active`, `pop_chomage_ratio`, `geometry` (dissolved)                                                                                                                 |
| **`bmo_vertical.parquet`**          | Vertical table for Jobs (BMO).             | `codgeo`, `fap_code`, `count`                                                                                                                                                                                                        |
| **`associations_vertical.parquet`** | Vertical table for Associations.           | `codgeo`, `id_waldec`, `count`                                                                                                                                                                                                       |
| **`gares.parquet`**                 | Train Stations presence (Odace).           | `codgeo`, `gare_count`, `has_gare`                                                                                                                                                                                                   |
| **`pois.parquet`**                  | Points of Interest for map layers.         | `id`, `type`, `lat`, `lon`, `metadata`                                                                                                                                                                                               |
| **`referentiels.parquet`**          | Reference tables for UI dropdowns.         | `type`, `code`, `label`                                                                                                                                                                                                              |
| **`loyers.parquet`**                | Average Rent data (Appartements).          | `codgeo`, `loyer_app_m2`                                                                                                                                                                                                             |

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
    - **Copy**: Moves generated files from `pipeline/cache/output/` to `data/` for the application to use.
