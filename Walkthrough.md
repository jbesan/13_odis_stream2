# Walkthrough — Optimisation Boot Cold-Start & Résilience Contrat Datasets

## 1. Modifications réalisées

### A. Performance au premier chargement (CPU=2 conservé)
- **Skip de l'instrumentation Logfire en production** ([`app/utils/logger.py`](file:///Users/jacques/dev/13_odis_stream2/app/utils/logger.py)) : `setup_logfire()` retourne après `logfire.configure(..., send_to_logfire=False)` sans appeler `instrument_pydantic_ai()` ni `instrument_httpx()`. Évite l'import synchrone de ~1 200 modules (`pydantic_ai`, `fastmcp`, `mcp`) sur `main.py`. Test mis à jour dans [`tests/unit/test_logfire_env.py`](file:///Users/jacques/dev/13_odis_stream2/tests/unit/test_logfire_env.py).
- **Pré-chauffe in-process au boot** ([`app/server_boot.py`](file:///Users/jacques/dev/13_odis_stream2/app/server_boot.py), [`app/Dockerfile`](file:///Users/jacques/dev/13_odis_stream2/app/Dockerfile)) : remplace `python3 -m utils.data_sync && streamlit run main.py ...` par `python3 server_boot.py main.py ...`. Pré-importe les modules backend lourds, synchronise GCS et pré-charge `@st.cache_resource` (`get_app_data()`, `get_tag_search_index()`) + `mcp_server.DATA_CONTEXT` pendant le `--cpu-boost` de démarrage, tout en évitant d'importer `ui.idle_sleep`/`ui.page_shell` avant la création du `Runtime` Streamlit. Tests ajoutés dans [`tests/unit/test_server_boot.py`](file:///Users/jacques/dev/13_odis_stream2/tests/unit/test_server_boot.py).

### B. Backfill `odis_associations_agg.parquet` & garde-fou de contrat
- **Émission duale temporaire dans le pipeline** ([`pipeline/build.py`](file:///Users/jacques/dev/13_odis_stream2/pipeline/build.py), [`pipeline/etl.py`](file:///Users/jacques/dev/13_odis_stream2/pipeline/etl.py), [`pipeline/data_contracts.yaml`](file:///Users/jacques/dev/13_odis_stream2/pipeline/data_contracts.yaml), [`pipeline/tests/test_plm_consolidation.py`](file:///Users/jacques/dev/13_odis_stream2/pipeline/tests/test_plm_consolidation.py)) : régénération de `odis_associations_agg.parquet` aux côtés des 3 fichiers RNA V6 tant que la production reste sur `c645c7c`.
- **Contrôle du contrat dans `data_sync.py`** ([`app/utils/data_sync.py`](file:///Users/jacques/dev/13_odis_stream2/app/utils/data_sync.py), [`tests/unit/test_sync_datasets.py`](file:///Users/jacques/dev/13_odis_stream2/tests/unit/test_sync_datasets.py)) : `download_and_verify_release()` vérifie `RUNTIME_DATASET_FILENAMES` sur `manifest["outputs"]` avant tout téléchargement afin de déclencher automatiquement le rollback vers `previous_version` en cas de rupture de contrat.

## 2. Validation & Tests
- `uv run ruff check app tests pipeline` : **All checks passed**
- `uv run ty check app` : **All checks passed**
- `uv run pytest tests/unit pipeline/tests --tb=line` : **890 passed, 1 skipped** (`tests/unit/test_refiner_agent.py`)
- `uv run pytest tests/integration --tb=line` : **9 passed**
