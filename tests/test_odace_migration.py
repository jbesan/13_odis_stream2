import os
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock

from pipeline.odace_client import OdaceClient
from pipeline.ingest import clean_finess_national, PipelineLogger

@pytest.fixture
def temp_cache_dirs(tmp_path):
    """Fixture to mock raw and clean directories in pipeline."""
    raw_dir = tmp_path / "raw"
    clean_dir = tmp_path / "clean"
    raw_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    with patch("pipeline.common.CACHE_DIR", raw_dir), \
         patch("pipeline.common.CLEAN_DIR", clean_dir), \
         patch("pipeline.ingest.CACHE_DIR", raw_dir), \
         patch("pipeline.ingest.CLEAN_DIR", clean_dir), \
         patch("pipeline.odace_client.CACHE_DIR", raw_dir):
        yield raw_dir, clean_dir


# =====================================================================
# 1. OdaceClient Tests
# =====================================================================

@patch("requests.post")
def test_odace_client_fetch_table_success(mock_post, temp_cache_dirs):
    """Tests that OdaceClient.fetch_table correctly requests and parses API response."""
    # Mock response data
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {
                "commune_insee_code": "9353.",
                "departement_code": "93",
                "raison_sociale": "CPMI NOISY LE SEC",
                "categorie_agregat": "PMI",
                "libelle_sph": "nan",
                "finess_etablissement_code": "930060686"
            }
        ],
        "columns": ["commune_insee_code", "departement_code", "raison_sociale", "categorie_agregat", "libelle_sph", "finess_etablissement_code"]
    }
    mock_post.return_value = mock_resp

    with patch.dict(os.environ, {"ODACE_API_KEY": "test-key", "ODACE_API_URL": "https://api-test"}):
        client = OdaceClient()
        df = client.fetch_table("dim_etablissement_sante")
        
        assert not df.empty
        assert len(df) == 1
        assert df.iloc[0]["commune_insee_code"] == "9353."
        
        # Verify post payload has SQL and LIMIT
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "json" in kwargs
        assert "SELECT * FROM silver_dim_etablissement_sante" in kwargs["json"]["sql"]


# =====================================================================
# 2. Ingestion Cleaner: clean_finess_national Tests
# =====================================================================

@patch("pipeline.ingest.get_odace_client")
def test_clean_finess_national_success(mock_get_client, temp_cache_dirs):
    """Tests that clean_finess_national maps Odace columns and writes valid parquet on success."""
    raw_dir, _ = temp_cache_dirs
    
    # Mock client and fetch_table response
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    
    mock_data = pd.DataFrame([
        {
            "commune_insee_code": "9353.",
            "departement_code": "93",
            "raison_sociale": "CPMI NOISY LE SEC",
            "categorie_agregat": "PMI",
            "libelle_sph": "nan",
            "finess_etablissement_code": "930060686"
        }
    ])
    mock_client.fetch_table.return_value = mock_data

    config = {
        "sources": {
            "finess_national": {
                "use_odace": True,
                "odace_table": "dim_etablissement_sante",
                "local_name": "finess_national.parquet",
                "used_columns": [
                    "Departement", "Commune", "LibelleCategorieAgregat", "nofinesset",
                    "LibelleSph", "coordxet", "coordyet", "RaisonSociale"
                ]
            }
        }
    }
    
    logger = MagicMock(spec=PipelineLogger)
    clean_finess_national(config, logger)
    
    # Verify file is written
    out_file = raw_dir / "finess_national.parquet"
    assert out_file.exists()
    
    # Load and verify schema/values
    df_clean = pd.read_parquet(out_file, engine="fastparquet")
    assert list(df_clean.columns) == [
        "Departement", "Commune", "LibelleCategorieAgregat", "nofinesset",
        "LibelleSph", "coordxet", "coordyet", "RaisonSociale", "codgeo"
    ]
    
    assert df_clean.iloc[0]["codgeo"] == "93053"
    assert df_clean.iloc[0]["Departement"] == "93"
    assert df_clean.iloc[0]["Commune"] == "053"
    assert df_clean.iloc[0]["nofinesset"] == "930060686"
    assert df_clean.iloc[0]["RaisonSociale"] == "CPMI NOISY LE SEC"
    assert df_clean.iloc[0]["LibelleCategorieAgregat"] == "PMI"
    assert pd.isna(df_clean.iloc[0]["coordxet"])


@patch("pipeline.ingest.get_odace_client")
def test_clean_finess_national_fallback(mock_get_client, temp_cache_dirs):
    """Tests that clean_finess_national falls back to legacy copy on API exception."""
    raw_dir, _ = temp_cache_dirs
    
    # Mock client fetch to fail
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API connection timed out")
    
    # Write a dummy legacy file to represent cached file
    legacy_file = raw_dir / "finess_national.parquet"
    legacy_df = pd.DataFrame([{"Departement": "01", "Commune": "451", "nofinesset": "1234"}])
    legacy_df.to_parquet(legacy_file, engine="fastparquet")

    config = {
        "sources": {
            "finess_national": {
                "use_odace": True,
                "odace_table": "dim_etablissement_sante",
                "local_name": "finess_national.parquet",
                "used_columns": [
                    "Departement", "Commune", "LibelleCategorieAgregat", "nofinesset",
                    "LibelleSph", "coordxet", "coordyet", "RaisonSociale"
                ]
            }
        }
    }
    
    logger = MagicMock(spec=PipelineLogger)
    
    # Execute step - should log error but complete without raising exception
    clean_finess_national(config, logger)
    
    # Verify legacy file is still there and unmodified
    assert legacy_file.exists()
    df_result = pd.read_parquet(legacy_file, engine="fastparquet")
    assert len(df_result) == 1
    assert df_result.iloc[0]["nofinesset"] == "1234"
