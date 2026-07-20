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
    clean_bpe,
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


def test_clean_maternites_skipped():
    """Tests that clean_maternites logs SKIPPED as it is bypassed in favor of BPE25."""
    config = {}
    logger = MagicMock(spec=PipelineLogger)
    clean_maternites(config, logger)
    logger.log_step.assert_called_once_with("clean_maternites", "SKIPPED")


# =====================================================================
# 5. Ingestion Cleaner: clean_bpe Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_bpe_odace_success(mock_get_client, temp_cache_dirs):
    """Tests that clean_bpe maps Odace columns, filters, reprojects coordinates, and writes clean parquets."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # Mock data representing dim_equipement_territoire from Odace
    mock_data = pd.DataFrame(
        [
            # Ecole Maternelle C107
            {
                "equipement_sk": "eq1",
                "commune_sk": "comm1",
                "commune_insee_code": "13054",
                "departement_code": "13",
                "commune_label": "MARIGNANE",
                "type_equipement_code": "C107",
                "equipement_label": "ECOLE MATERNELLE PUBLIQUE ABBE",
                "capacite_hebergement": None,
                "coord_x_lambert": 879374.24,
                "coord_y_lambert": 6260251.11,
            },
            # Gare E107
            {
                "equipement_sk": "eq2",
                "commune_sk": "comm1",
                "commune_insee_code": "13054",
                "departement_code": "13",
                "commune_label": "MARIGNANE",
                "type_equipement_code": "E107",
                "equipement_label": "GARE DE MARIGNANE",
                "capacite_hebergement": None,
                "coord_x_lambert": 879374.24,
                "coord_y_lambert": 6260251.11,
            },
            # Untargeted equipment (should be filtered out)
            {
                "equipement_sk": "eq3",
                "commune_sk": "comm1",
                "commune_insee_code": "13054",
                "departement_code": "13",
                "commune_label": "MARIGNANE",
                "type_equipement_code": "Z999",
                "equipement_label": "SOMETHING ELSE",
                "capacite_hebergement": None,
                "coord_x_lambert": 879374.24,
                "coord_y_lambert": 6260251.11,
            }
        ]
    )
    mock_client.fetch_table.return_value = mock_data

    config = {
        "sources": {
            "bpe": {
                "use_odace": True,
                "odace_table": "dim_equipement_territoire",
                "local_name": "BPE25.parquet",
                "ttl_days": 365,
                "used_columns": ["DEPCOM", "TYPEQU", "NOMRS", "LAMBERT_X", "LAMBERT_Y"],
            }
        }
    }

    logger = MagicMock(spec=PipelineLogger)
    clean_bpe(config, logger)

    # Verify output parquet files are created
    assert (clean_dir / "bpe_education_cols.parquet").exists()
    assert (clean_dir / "bpe_gares_cols.parquet").exists()
    assert (clean_dir / "bpe_pois.parquet").exists()

    # Read output POIs to check reprojection and filtering
    pois_df = pd.read_parquet(clean_dir / "bpe_pois.parquet")
    assert len(pois_df) == 2  # C107 and E107 kept, Z999 filtered out
    assert "lat" in pois_df.columns
    assert "lon" in pois_df.columns
    # Check that coordinates are not NaN and represent reprojected Lambert coords (near Marignane/Marseille ~43.4, ~5.2)
    assert not np.isnan(pois_df.iloc[0]["lat"])
    assert not np.isnan(pois_df.iloc[0]["lon"])
    assert 43.0 < pois_df.iloc[0]["lat"] < 44.0
    assert 5.0 < pois_df.iloc[0]["lon"] < 6.5


@patch("pipeline.ingest.get_odace_client")
def test_clean_bpe_odace_fallback(mock_get_client, temp_cache_dirs):
    """Tests that clean_bpe falls back to local cache file if Odace API raises an exception."""
    raw_dir, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API error")

    # Create dummy local raw parquet file in raw cache directory
    local_raw_path = raw_dir / "BPE25.parquet"
    df_raw = pd.DataFrame(
        [
            {
                "DEPCOM": "13054",
                "TYPEQU": "C107",
                "NOMRS": "LOCAL ECOLE MATERNELLE",
                "LAMBERT_X": 879374.24,
                "LAMBERT_Y": 6260251.11,
                "LONGITUDE": np.nan,
                "LATITUDE": np.nan,
                "SECTEUR": "1",
            }
        ]
    )
    df_raw.to_parquet(local_raw_path, engine="fastparquet")

    config = {
        "sources": {
            "bpe": {
                "use_odace": True,
                "odace_table": "dim_equipement_territoire",
                "local_name": "BPE25.parquet",
                "ttl_days": 365,
                "used_columns": ["DEPCOM", "TYPEQU", "NOMRS", "LAMBERT_X", "LAMBERT_Y", "LONGITUDE", "LATITUDE", "SECTEUR"],
            }
        }
    }

    logger = MagicMock(spec=PipelineLogger)
    clean_bpe(config, logger)

    # Verify fallback executed and wrote outputs from local cache
    assert (clean_dir / "bpe_education_cols.parquet").exists()
    assert (clean_dir / "bpe_pois.parquet").exists()

    pois_df = pd.read_parquet(clean_dir / "bpe_pois.parquet")
    assert len(pois_df) == 1
    assert pois_df.iloc[0]["name"] == "LOCAL ECOLE MATERNELLE"

