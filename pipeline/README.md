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
# Run the full pipeline (Ingest + Build)
python -m pipeline.etl --step all

# Run only the Ingest step (Fetch & Clean)
python -m pipeline.etl --step ingest

# Run only the Build step (Aggregate & Export)
python -m pipeline.etl --step build
```

## 📂 Architecture

The pipeline is split into two main stages:

1.  **Ingest (`ingest.py`)**: Fetches raw data and cleans it into intermediate Parquet files.
2.  **Build (`build.py`)**: Aggregates cleaned data into final ODIS artifacts.

### File Structure

- **`etl.py`**: CLI entry point.
- **`ingest.py`**: Handles downloading and cleaning.
- **`build.py`**: Handles joining, aggregation, and export.
- **`common.py`**: Shared utilities (logging, config, IO).
- **`sources.yaml`**: Configuration file defining all data sources.
- **`cache/`**:
  - `raw/`: Raw downloaded files (zips, excel, etc.).
  - `clean/`: Intermediate cleaned Parquet files.
- **`output/`**: Final Parquet files for the app.
- **`status.json`**: Execution logs.

## 🛠 Configuration (`sources.yaml`)

Data sources are defined in `pipeline/sources.yaml`. Each entry specifies:

- `url`: Download URL.
- `format`: File format (csv, xlsx, zip, etc.).
- `local_name`: Filename for the cached file.
- `archive_file`: (Optional) Specific file to extract if the source is a Zip archive.
- `sheet_name`: (Optional) Sheet to read for Excel files.

## 📦 Outputs

The pipeline generates the following Parquet files in `pipeline/output/`:

| File                                | Description                                | Key Columns                                                                                                                                                                                                        |
| ----------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`odis_communes.parquet`**         | Main dataset at Commune level.             | `codgeo`, `population`, `pop_active`, `metiers_offres_ratio`, `pop_chomage_ratio`, `bassin_de_vie`, `bassin_emploi`, `geometry`, `centroid`, `codgeo_voisins`, `edu_pe_tx_couverture`, `log_priv_vacant_plus_2ans` |
| **`odis_bassins_de_vie.parquet`**   | Aggregated dataset at Bassin de Vie level. | `bassin_de_vie`, `libelle_bassin_de_vie`, `population_bv`, `pop_active`, `pop_chomage_ratio`, `geometry` (dissolved)                                                                                               |
| **`odis_rel_metiers.parquet`**      | Vertical table for Jobs (BMO).             | `codgeo`, `fap_code`, `count`                                                                                                                                                                                      |
|                                     |
| **`odis_rel_associations.parquet`** | Vertical table for Associations.           | `codgeo`, `id_waldec`                                                                                                                                                                                              |
| **`pois.parquet`**                  | Points of Interest for map layers.         | `id`, `type`, `lat`, `lon`, `metadata`                                                                                                                                                                             |
| **`referentiels.parquet`**          | Reference tables for UI dropdowns.         | `type`, `code`, `label`                                                                                                                                                                                            |

## 🔄 Data Flow

1.  **Ingest (`ingest.py`)**:
    - **Fetch**: Downloads raw files to `pipeline/cache/`.
    - **Clean**: Reads raw files, fixes types/columns, and saves to `pipeline/cache/clean/*.parquet`.
2.  **Build (`build.py`)**:
    - **Load**: Reads cleaned files.
    - **Join**: Merges all indicators onto the Communes base.
    - **Enrich**: Calculates `pop_be` and other derived metrics.
    - **Aggregate**: Dissolves geometries for Bassins de Vie.
    - **Export**: Saves final files to `pipeline/output/`.
