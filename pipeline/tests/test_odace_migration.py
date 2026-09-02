import json
import os
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from pipeline.odace_client import OdaceClient
from pipeline.ingest import (
    clean_caf,
    clean_bpe,
    clean_communes,
    clean_housing_occupation,
    transform_housing_occupation_odace,
    PipelineLogger,
)
from pipeline.run_context import PipelineRunError


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
    df_dummy.to_parquet(pq_buffer)
    pq_bytes = pq_buffer.getvalue()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [pq_bytes]
    mock_get.return_value = mock_resp

    with patch.dict(
        os.environ, {"ODACE_API_KEY": "test-key", "ODACE_API_URL": "https://api-test"}
    ):
        client = OdaceClient()
        df = client.fetch_table("dim_etablissement_sante", ttl_days=30)

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
            timeout=300,
        )


# =====================================================================
# 2. Ingestion Cleaner: clean_caf Tests
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

    df_clean = pd.read_parquet(out_file)
    assert list(df_clean.columns) == ["codgeo", "taux_couverture"]
    # Should only contain max year (2023)
    assert len(df_clean) == 1
    assert df_clean.iloc[0]["codgeo"] == "38085"
    assert df_clean.iloc[0]["taux_couverture"] == 51.4


@patch("pipeline.ingest.get_odace_client")
def test_clean_caf_odace_failure_does_not_use_legacy_raw(
    mock_get_client, temp_cache_dirs
):
    """An enabled Odace source fails closed rather than reviving a local file."""
    raw_dir, _ = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API error")

    # A historical raw file must not alter the outcome.
    legacy_file = raw_dir / "caf_taux_couverture.json"
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
    with pytest.raises(PipelineRunError, match="Odace caf"):
        clean_caf(config, logger)


# =====================================================================
# 3. Ingestion Cleaner: Housing occupation
# =====================================================================


@pytest.fixture
def housing_occupation_contract():
    return {
        "version": 1,
        "odace_table": "fact_occupation_logement",
        "reference_year": "2022",
        "required_columns": [
            "commune_insee_code",
            "annee",
            "indicateur_occupation",
            "valeur",
        ],
        "primary_key": ["commune_insee_code", "annee", "indicateur_occupation"],
        "required_indicators": ["MOD_OVER_OCC", "STD_OCC", "SEV_UNDER_OCC"],
        "minimum_communes": 2,
        "minimum_communes_per_indicator": 2,
        "require_complete_indicator_set_per_commune": True,
        "value": {"minimum": 0, "nullable": False},
    }


@pytest.fixture
def housing_occupation_odace_data(housing_occupation_contract):
    rows = []
    for commune, base in [("01001", 1.0), ("2B002", 10.0)]:
        for offset, indicator in enumerate(housing_occupation_contract["required_indicators"]):
            rows.append(
                {
                    "commune_insee_code": commune,
                    "annee": "2022",
                    "indicateur_occupation": indicator,
                    "valeur": base + offset,
                }
            )
    rows.append(
        {
            "commune_insee_code": "01001",
            "annee": "2016",
            "indicateur_occupation": "MOD_OVER_OCC",
            "valeur": 999.0,
        }
    )
    return pd.DataFrame(rows)


def test_transform_housing_occupation_odace_applies_year_and_contract(
    housing_occupation_odace_data, housing_occupation_contract
):
    result = transform_housing_occupation_odace(
        housing_occupation_odace_data, housing_occupation_contract
    )

    assert result.columns.tolist() == [
        "codgeo",
        "MOD_OVER_OCC",
        "STD_OCC",
        "SEV_UNDER_OCC",
    ]
    assert result["codgeo"].tolist() == ["01001", "2B002"]
    assert result.loc[result["codgeo"] == "01001", "MOD_OVER_OCC"].iloc[0] == 1.0


def test_transform_housing_occupation_odace_rejects_incomplete_indicator_family(
    housing_occupation_odace_data, housing_occupation_contract
):
    incomplete = housing_occupation_odace_data.iloc[:-2].copy()

    with pytest.raises(PipelineRunError, match="insufficient commune coverage"):
        transform_housing_occupation_odace(incomplete, housing_occupation_contract)


def test_transform_housing_occupation_odace_rejects_duplicate_business_key(
    housing_occupation_odace_data, housing_occupation_contract
):
    duplicated = pd.concat(
        [housing_occupation_odace_data, housing_occupation_odace_data.iloc[[0]]],
        ignore_index=True,
    )

    with pytest.raises(PipelineRunError, match="violates its primary key"):
        transform_housing_occupation_odace(duplicated, housing_occupation_contract)


@patch("pipeline.ingest.get_odace_client")
def test_clean_housing_occupation_uses_odace_only(
    mock_get_client,
    temp_cache_dirs,
    housing_occupation_odace_data,
    housing_occupation_contract,
):
    _, clean_dir = temp_cache_dirs
    mock_client = MagicMock()
    mock_client.fetch_table.return_value = housing_occupation_odace_data
    mock_get_client.return_value = mock_client
    config = {
        "sources": {
            "housing_occupation": {
                "use_odace": True,
                "odace_table": "fact_occupation_logement",
            }
        }
    }

    with patch(
        "pipeline.ingest._load_source_contract", return_value=housing_occupation_contract
    ):
        clean_housing_occupation(config, MagicMock(spec=PipelineLogger))

    output = pd.read_parquet(clean_dir / "housing_occupation.parquet")
    assert len(output) == 2
    mock_client.fetch_table.assert_called_once_with("fact_occupation_logement")


@patch("pipeline.ingest.get_odace_client")
def test_clean_housing_occupation_fails_without_odace_data(
    mock_get_client, temp_cache_dirs, housing_occupation_contract
):
    mock_client = MagicMock()
    mock_client.fetch_table.return_value = pd.DataFrame()
    mock_get_client.return_value = mock_client
    config = {
        "sources": {
            "housing_occupation": {
                "use_odace": True,
                "odace_table": "fact_occupation_logement",
            }
        }
    }

    with patch(
        "pipeline.ingest._load_source_contract", return_value=housing_occupation_contract
    ), pytest.raises(PipelineRunError, match="Odace housing_occupation"):
        clean_housing_occupation(config, MagicMock(spec=PipelineLogger))


@patch("pipeline.ingest.fetch_source", return_value=None)
@patch("pipeline.ingest.get_odace_client")
def test_clean_communes_keeps_odace_commune_sk_for_candidate_joins(
    mock_get_client, _mock_fetch_source, temp_cache_dirs
):
    """The candidate commune artifact must retain the Odace rent join key."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        {
            "commune_insee_code": ["75001"],
            "geometrie_geojson": [
                json.dumps(
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [2.3, 48.8],
                                [2.31, 48.8],
                                [2.31, 48.81],
                                [2.3, 48.8],
                            ]
                        ],
                    }
                )
            ],
        }
    )
    mock_client.fetch_dim_commune.return_value = pd.DataFrame(
        {
            "commune_sk": ["odace-sk-75001"],
            "commune_insee_code": ["75001"],
            "commune_label": ["Paris 1er"],
            "departement_code": ["75"],
            "region_code": ["11"],
        }
    )

    config = {
        "sources": {
            "communes": {
                "use_odace": True,
                "odace_table": "ref_commune_geo",
                "ttl_days": 30,
            },
            "ref_epci": {},
        }
    }

    clean_communes(config, MagicMock(spec=PipelineLogger))

    result = pd.read_parquet(clean_dir / "communes.parquet")
    assert result.loc[0, "commune_sk"] == "odace-sk-75001"


# =====================================================================
# 3. Ingestion Cleaner: clean_bpe Tests
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
            },
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
def test_clean_bpe_odace_failure_does_not_use_legacy_raw(
    mock_get_client, temp_cache_dirs
):
    """An enabled Odace BPE source fails closed rather than using local cache."""
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
    df_raw.to_parquet(local_raw_path)

    config = {
        "sources": {
            "bpe": {
                "use_odace": True,
                "odace_table": "dim_equipement_territoire",
                "local_name": "BPE25.parquet",
                "ttl_days": 365,
                "used_columns": [
                    "DEPCOM",
                    "TYPEQU",
                    "NOMRS",
                    "LAMBERT_X",
                    "LAMBERT_Y",
                    "LONGITUDE",
                    "LATITUDE",
                    "SECTEUR",
                ],
            }
        }
    }

    logger = MagicMock(spec=PipelineLogger)
    with pytest.raises(PipelineRunError, match="Odace bpe"):
        clean_bpe(config, logger)
    assert not (clean_dir / "bpe_pois.parquet").exists()
