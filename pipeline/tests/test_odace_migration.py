import os
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock

from pipeline.odace_client import OdaceClient
from pipeline.ingest import (
    clean_finess_national,
    clean_caf,
    clean_maternites,
    PipelineLogger,
)


@pytest.fixture
def temp_cache_dirs(tmp_path):
    """Fixture to mock raw and clean directories in pipeline."""
    raw_dir = tmp_path / "raw"
    clean_dir = tmp_path / "clean"
    raw_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    with (
        patch("pipeline.common.CACHE_DIR", raw_dir),
        patch("pipeline.common.CLEAN_DIR", clean_dir),
        patch("pipeline.ingest.CACHE_DIR", raw_dir),
        patch("pipeline.ingest.CLEAN_DIR", clean_dir),
        patch("pipeline.odace_client.CACHE_DIR", raw_dir),
    ):
        yield raw_dir, clean_dir


# =====================================================================
# 1. OdaceClient Tests
# =====================================================================


@patch("requests.get")
def test_odace_client_fetch_table_success(mock_get, temp_cache_dirs):
    """Tests that OdaceClient.fetch_table correctly requests and parses API response."""
    import io

    # Mock response data
    df_dummy = pd.DataFrame(
        [
            {
                "commune_insee_code": "9353.",
                "departement_code": "93",
                "raison_sociale": "CPMI NOISY LE SEC",
                "categorie_agregat": "PMI",
                "libelle_sph": "nan",
                "finess_etablissement_code": "930060686",
            }
        ]
    )
    pq_buffer = io.BytesIO()
    df_dummy.to_parquet(pq_buffer, engine="fastparquet")
    pq_bytes = pq_buffer.getvalue()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [pq_bytes]
    mock_get.return_value = mock_resp

    with patch.dict(
        os.environ, {"ODACE_API_KEY": "test-key", "ODACE_API_URL": "https://api-test"}
    ):
        client = OdaceClient()
        df = client.fetch_table("dim_etablissement_sante")

        assert not df.empty
        assert len(df) == 1
        assert df.iloc[0]["commune_insee_code"] == "9353."

        # Verify GET url
        mock_get.assert_called_once_with(
            "https://api-test/api/data/export/dim_etablissement_sante?format=parquet",
            headers={
                "Authorization": "Bearer test-key",
                "Content-Type": "application/json",
            },
            stream=True,
            timeout=120,
        )


# =====================================================================
# 2. Ingestion Cleaner: clean_finess_national Tests
# =====================================================================


def test_clean_finess_national_success():
    """Tests that clean_finess_national logs SKIPPED as health data is handled by BPE25."""
    config = {}
    logger = MagicMock(spec=PipelineLogger)
    clean_finess_national(config, logger)
    logger.log_step.assert_called_once_with("clean_finess_national", "SKIPPED")


# =====================================================================
# 3. Ingestion Cleaner: clean_caf Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_caf_odace_success(mock_get_client, temp_cache_dirs):
    """Tests that clean_caf maps Odace columns and writes valid parquet on success."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_data = pd.DataFrame(
        [
            {
                "commune_insee_code": "38085",
                "annee": "2023",
                "taux_couverture_commune": 51.4,
                "taux_couverture_eaje": 8.5,
                "taux_couverture_prescol": 0.0,
            },
            {
                "commune_insee_code": "38085",
                "annee": "2022",
                "taux_couverture_commune": 49.0,
                "taux_couverture_eaje": 8.0,
                "taux_couverture_prescol": 0.0,
            },
        ]
    )
    mock_client.fetch_table.return_value = mock_data

    config = {
        "sources": {
            "caf": {
                "use_odace": True,
                "odace_table": "fact_couverture_petite_enfance",
                "local_name": "caf_taux_couverture.json",
                "used_columns": ["numcom", "annee", "txcouv_com"],
            }
        }
    }

    logger = MagicMock(spec=PipelineLogger)
    clean_caf(config, logger)

    # Verify file is written in clean/caf.parquet
    out_file = clean_dir / "caf.parquet"
    assert out_file.exists()

    df_clean = pd.read_parquet(out_file, engine="fastparquet")
    assert list(df_clean.columns) == ["codgeo", "taux_couverture"]
    # Should only contain max year (2023)
    assert len(df_clean) == 1
    assert df_clean.iloc[0]["codgeo"] == "38085"
    assert df_clean.iloc[0]["taux_couverture"] == 51.4


@patch("pipeline.ingest.get_odace_client")
def test_clean_caf_odace_fallback(mock_get_client, temp_cache_dirs):
    """Tests that clean_caf falls back to legacy raw file on API exception."""
    raw_dir, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API error")

    # Write dummy legacy JSON file to raw cache
    legacy_file = raw_dir / "caf_taux_couverture.json"
    import json

    legacy_data = [{"numcom": "38085", "annee": 2023, "txcouv_com": 51.4}]
    with open(legacy_file, "w") as f:
        json.dump(legacy_data, f)

    config = {
        "sources": {
            "caf": {
                "use_odace": True,
                "odace_table": "fact_couverture_petite_enfance",
                "local_name": "caf_taux_couverture.json",
                "used_columns": ["numcom", "annee", "txcouv_com"],
            }
        }
    }

    logger = MagicMock(spec=PipelineLogger)
    clean_caf(config, logger)

    # Verify output is written in clean/caf.parquet using legacy loader
    out_file = clean_dir / "caf.parquet"
    assert out_file.exists()
    df_clean = pd.read_parquet(out_file, engine="fastparquet")
    assert len(df_clean) == 1
    assert df_clean.iloc[0]["codgeo"] == "38085"
    assert df_clean.iloc[0]["taux_couverture"] == 51.4


# =====================================================================
# 4. Ingestion Cleaner: clean_maternites Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_maternites_odace_success(mock_get_client, temp_cache_dirs):
    """Tests that clean_maternites maps Odace columns and writes valid JSON on success."""
    raw_dir, _ = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_data = pd.DataFrame(
        [
            {"finess_etablissement_code": "120004569"},
            {"finess_etablissement_code": "930060686"},
        ]
    )
    mock_client.fetch_table.return_value = mock_data

    config = {
        "sources": {
            "maternites": {
                "use_odace": True,
                "odace_table": "dim_maternite",
                "local_name": "maternites_drees.json",
                "used_columns": ["FI_ET", "fi_et"],
            }
        }
    }

    logger = MagicMock(spec=PipelineLogger)
    clean_maternites(config, logger)

    # Verify file is written in raw_dir/maternites_drees.json
    out_file = raw_dir / "maternites_drees.json"
    assert out_file.exists()

    df_clean = pd.read_json(out_file)
    assert list(df_clean.columns) == ["fi_et"]
    assert len(df_clean) == 2
    assert df_clean.iloc[0]["fi_et"] == 120004569


@patch("pipeline.ingest.get_odace_client")
def test_clean_maternites_odace_fallback(mock_get_client, temp_cache_dirs):
    """Tests that clean_maternites does not overwrite legacy JSON file on API exception."""
    raw_dir, _ = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API error")

    # Write dummy legacy JSON file to raw cache
    legacy_file = raw_dir / "maternites_drees.json"
    import json

    legacy_data = [{"fi_et": "12345"}]
    with open(legacy_file, "w") as f:
        json.dump(legacy_data, f)

    config = {
        "sources": {
            "maternites": {
                "use_odace": True,
                "odace_table": "dim_maternite",
                "local_name": "maternites_drees.json",
                "used_columns": ["FI_ET", "fi_et"],
            }
        }
    }

    logger = MagicMock(spec=PipelineLogger)
    clean_maternites(config, logger)

    # Verify legacy file is still there and unmodified
    assert legacy_file.exists()
    df_result = pd.read_json(legacy_file)
    assert len(df_result) == 1
    assert df_result.iloc[0]["fi_et"] == 12345
