import json
import os
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from pipeline.odace_client import OdaceClient, OdasClient, get_odas_client
from pipeline.ingest import (
    clean_bpe,
    clean_caf,
    clean_codes_postaux,
    clean_communes,
    clean_departements,
    clean_housing_occupation,
    clean_nomenclature_waldec,
    clean_political,
    clean_regions,
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
# 1. OdasClient Tests & Cold Start Warmup
# =====================================================================


@patch("requests.get")
def test_odace_client_fetch_table_success(mock_get, temp_cache_dirs):
    """Tests that OdasClient.fetch_table correctly requests and parses API response."""
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
        os.environ,
        {"ODAS_API_KEY": "test-key", "ODAS_API_URL": "https://api-test"},
        clear=True,
    ):
        client = OdasClient(warmup=False)
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


def test_odas_client_missing_env_vars():
    """Tests that OdasClient fails immediately when ODAS_API_KEY or ODAS_API_URL is missing."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="ODAS_API_KEY and ODAS_API_URL"):
            OdasClient()


@patch("requests.get")
def test_odas_client_warmup_instant_success(mock_get):
    """Tests that wait_for_ready marks client as warm when /health responds 200 immediately."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_get.return_value = mock_resp

    with patch.dict(
        os.environ,
        {"ODAS_API_KEY": "test-key", "ODAS_API_URL": "https://api-test"},
        clear=True,
    ):
        client = OdasClient(warmup=True)
        assert client._is_warm is False

        is_ready = client.wait_for_ready()
        assert is_ready is True
        assert client._is_warm is True
        mock_get.assert_called_once_with("https://api-test/health", timeout=5)

        # Subsequent call should be a no-op
        assert client.wait_for_ready() is True
        assert mock_get.call_count == 1


@patch("time.sleep")
@patch("requests.get")
def test_odas_client_warmup_cold_start_retry(mock_get, mock_sleep):
    """Tests that wait_for_ready retries on cold start errors (503 / timeout) until healthy."""
    resp_503 = MagicMock()
    resp_503.status_code = 503

    resp_200 = MagicMock()
    resp_200.status_code = 200

    # First call: 503, second call: RequestException, third call: 200 OK
    import requests

    mock_get.side_effect = [
        resp_503,
        requests.RequestException("Container starting"),
        resp_200,
    ]

    with patch.dict(
        os.environ,
        {"ODAS_API_KEY": "test-key", "ODAS_API_URL": "https://api-test"},
        clear=True,
    ):
        client = OdasClient(warmup=True, max_warmup_seconds=30)
        is_ready = client.wait_for_ready(poll_interval=1.0)

        assert is_ready is True
        assert client._is_warm is True
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2


@patch("time.sleep")
@patch("requests.get")
def test_odas_client_warmup_timeout(mock_get, mock_sleep):
    """Tests that wait_for_ready raises TimeoutError when the max warmup limit is exceeded."""
    resp_503 = MagicMock()
    resp_503.status_code = 503
    mock_get.return_value = resp_503

    with patch.dict(
        os.environ,
        {"ODAS_API_KEY": "test-key", "ODAS_API_URL": "https://api-test"},
        clear=True,
    ):
        client = OdasClient(warmup=True, max_warmup_seconds=5)
        # Force timeout after iterations
        with patch("time.time", side_effect=[0.0, 1.0, 2.0, 6.0, 7.0]):
            with pytest.raises(TimeoutError, match="failed to respond healthy"):
                client.wait_for_ready(max_wait_seconds=5, poll_interval=1.0)


def test_odas_client_aliases():
    """Tests backward-compatibility aliases OdaceClient and get_odace_client."""
    from pipeline.odace_client import get_odace_client

    with patch.dict(
        os.environ,
        {"ODAS_API_KEY": "test-key", "ODAS_API_URL": "https://api-test"},
        clear=True,
    ):
        c1 = OdaceClient(warmup=False)
        assert isinstance(c1, OdasClient)
        assert c1.api_key == "test-key"

        c2 = get_odas_client(warmup=False)
        assert isinstance(c2, OdasClient)

        c3 = get_odace_client(warmup=False)
        assert isinstance(c3, OdasClient)


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
        for offset, indicator in enumerate(
            housing_occupation_contract["required_indicators"]
        ):
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
        "pipeline.ingest._load_source_contract",
        return_value=housing_occupation_contract,
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

    with (
        patch(
            "pipeline.ingest._load_source_contract",
            return_value=housing_occupation_contract,
        ),
        pytest.raises(PipelineRunError, match="Odace housing_occupation"),
    ):
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


# =====================================================================
# 4. Palier 1: Static Referentials Migration Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_codes_postaux_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_codes_postaux with OdasClient."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"commune_insee_code": "1001", "code_postal": "1000"},
            {"commune_insee_code": "75001", "code_postal": "75001"},
            {"commune_insee_code": "75001", "code_postal": "75001"},  # duplicate
        ]
    )

    config = {
        "sources": {
            "codes_postaux": {
                "use_odace": True,
                "odace_table": "ref_code_postal",
            }
        }
    }

    clean_codes_postaux(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "codes_postaux.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res.columns) == {"code_postal", "codgeo"}
    assert res.loc[res["codgeo"] == "01001", "code_postal"].iloc[0] == "01000"
    assert res.loc[res["codgeo"] == "75001", "code_postal"].iloc[0] == "75001"
    mock_client.fetch_table.assert_called_once_with("ref_code_postal")


@patch("pipeline.ingest.get_odace_client")
def test_clean_codes_postaux_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_codes_postaux fail-closed on Odace error."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API error")

    config = {
        "sources": {
            "codes_postaux": {
                "use_odace": True,
                "odace_table": "ref_code_postal",
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace codes_postaux"):
        clean_codes_postaux(config, MagicMock(spec=PipelineLogger))


@patch("pipeline.ingest.get_odace_client")
def test_clean_departements_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_departements with OdasClient."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {
                "code_departement": "1",
                "libelle_departement": " Ain ",
                "code_region": "84",
            },
            {
                "code_departement": "75",
                "libelle_departement": "Paris",
                "code_region": "11",
            },
        ]
    )

    config = {
        "sources": {
            "departements_ref": {
                "use_odace": True,
                "odace_table": "ref_departement",
            }
        }
    }

    clean_departements(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "departements.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res.columns) == {"code", "label", "reg_code"}
    assert res.loc[res["code"] == "01", "label"].iloc[0] == "Ain"
    assert res.loc[res["code"] == "01", "reg_code"].iloc[0] == "84"
    assert res.loc[res["code"] == "75", "label"].iloc[0] == "Paris"
    mock_client.fetch_table.assert_called_once_with("ref_departement")


@patch("pipeline.ingest.get_odace_client")
def test_clean_departements_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_departements fail-closed on Odace error."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API error")

    config = {
        "sources": {
            "departements_ref": {
                "use_odace": True,
                "odace_table": "ref_departement",
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace departements"):
        clean_departements(config, MagicMock(spec=PipelineLogger))


@patch("pipeline.ingest.get_odace_client")
def test_clean_regions_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_regions with OdasClient."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"code_region": "1", "libelle_region": " Guadeloupe "},
            {"code_region": "11", "libelle_region": " Île-de-France "},
            {"code_region": "84", "libelle_region": "Auvergne-Rhône-Alpes"},
        ]
    )

    config = {
        "sources": {
            "regions_ref": {
                "use_odace": True,
                "odace_table": "ref_region",
            }
        }
    }

    clean_regions(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "regions.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 3
    assert set(res.columns) == {"code", "label"}
    assert res.loc[res["code"] == "01", "label"].iloc[0] == "Guadeloupe"
    assert res.loc[res["code"] == "11", "label"].iloc[0] == "Île-de-France"
    assert res.loc[res["code"] == "84", "label"].iloc[0] == "Auvergne-Rhône-Alpes"
    mock_client.fetch_table.assert_called_once_with("ref_region")


@patch("pipeline.ingest.get_odace_client")
def test_clean_regions_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_regions fail-closed on Odace error."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API error")

    config = {
        "sources": {
            "regions_ref": {
                "use_odace": True,
                "odace_table": "ref_region",
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace regions"):
        clean_regions(config, MagicMock(spec=PipelineLogger))


def test_clean_regions_legacy_fallback(temp_cache_dirs):
    """Tests clean_regions fallback when use_odace is false."""
    raw_dir, clean_dir = temp_cache_dirs

    legacy_file = raw_dir / "referentiel_regions.json"
    legacy_file.write_text(
        json.dumps(
            [
                {"REG": "01", "LIBELLE": "Guadeloupe"},
                {"REG": "11", "LIBELLE": "Île-de-France"},
            ]
        ),
        encoding="utf-8",
    )

    config = {
        "sources": {
            "regions_ref": {
                "use_odace": False,
                "format": "json",
                "local_name": "referentiel_regions.json",
            }
        }
    }

    clean_regions(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "regions.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res.columns) == {"code", "label"}
    assert res.loc[res["code"] == "01", "label"].iloc[0] == "Guadeloupe"


@patch("pipeline.ingest.get_odace_client")
def test_clean_nomenclature_waldec_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_nomenclature_waldec with OdasClient."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"code_waldec": "100", "libelle": " Culture "},
            {"code_waldec": "012345", "libelle": "Santé"},
        ]
    )

    config = {
        "sources": {
            "nomenclature_waldec": {
                "use_odace": True,
                "odace_table": "ref_waldec",
            }
        }
    }

    clean_nomenclature_waldec(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "referentiel_waldec.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res.columns) == {"code", "label"}
    assert res.loc[res["code"] == "000100", "label"].iloc[0] == "Culture"
    assert res.loc[res["code"] == "012345", "label"].iloc[0] == "Santé"
    mock_client.fetch_table.assert_called_once_with("ref_waldec")


@patch("pipeline.ingest.get_odace_client")
def test_clean_nomenclature_waldec_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_nomenclature_waldec fail-closed on Odace error."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API error")

    config = {
        "sources": {
            "nomenclature_waldec": {
                "use_odace": True,
                "odace_table": "ref_waldec",
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace nomenclature_waldec"):
        clean_nomenclature_waldec(config, MagicMock(spec=PipelineLogger))


@patch("pipeline.ingest.get_odace_client")
def test_clean_political_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_political with OdasClient and domain contracts."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {
                "commune_insee_code": "1001",
                "code_nuance_politique": "RN",
                "famille_nuance_politique": "Extrême droite",
            },
            {
                "commune_insee_code": "1002",
                "code_nuance_politique": "SOC",
                "famille_nuance_politique": "Gauche",
            },
            {
                "commune_insee_code": "1003",
                "code_nuance_politique": "REC",
                "famille_nuance_politique": "Autre",
            },
        ]
    )

    config = {
        "sources": {
            "political_nuance": {
                "use_odace": True,
                "odace_table": "fact_nuance_politique",
            }
        }
    }

    clean_political(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "political.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 3
    assert set(res.columns) == {"codgeo", "pol_num", "maire_extreme_droite"}
    # RN -> far right -> pol_num 0.0
    rn_row = res.loc[res["codgeo"] == "01001"]
    assert rn_row["maire_extreme_droite"].iloc[0] is True or rn_row["maire_extreme_droite"].iloc[0] == 1
    assert rn_row["pol_num"].iloc[0] == 0.0
    # SOC -> not far right -> pol_num 1.0
    soc_row = res.loc[res["codgeo"] == "01002"]
    assert soc_row["maire_extreme_droite"].iloc[0] is False or soc_row["maire_extreme_droite"].iloc[0] == 0
    assert soc_row["pol_num"].iloc[0] == 1.0
    # REC -> far right nuance -> pol_num 0.0
    rec_row = res.loc[res["codgeo"] == "01003"]
    assert rec_row["maire_extreme_droite"].iloc[0] is True or rec_row["maire_extreme_droite"].iloc[0] == 1
    assert rec_row["pol_num"].iloc[0] == 0.0
    mock_client.fetch_table.assert_called_once_with("fact_nuance_politique")


@patch("pipeline.ingest.get_odace_client")
def test_clean_political_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_political fail-closed on Odace error."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API error")

    config = {
        "sources": {
            "political_nuance": {
                "use_odace": True,
                "odace_table": "fact_nuance_politique",
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace political"):
        clean_political(config, MagicMock(spec=PipelineLogger))


def test_main_cli_regions_step_registered():
    """Verify that main.py registers clean_regions and can trigger it via --steps regions."""
    from unittest.mock import patch
    from pipeline.ingest.main import main

    with (
        patch("pipeline.ingest.main.run_clean_step_safely") as mock_run,
        patch("pipeline.ingest.main.load_config", return_value={"sources": {}}),
        patch("pipeline.ingest.main.PipelineLogger"),
    ):
        main(["--steps", "regions"])
        assert mock_run.called
        assert mock_run.call_args[0][0] == "regions"


def test_build_generate_referentiels_uses_clean_regions(tmp_path, monkeypatch):
    """Verify that generate_referentiels uses regions.parquet from CLEAN_DIR."""
    import pipeline.build as build_module

    clean_dir = tmp_path / "clean"
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "output"
    clean_dir.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    pd.DataFrame({"codgeo": ["75056"], "nom": ["Paris"]}).to_parquet(
        clean_dir / "communes.parquet"
    )
    pd.DataFrame(
        {
            "code": ["75"],
            "label": ["Paris"],
            "reg_code": ["11"],
        }
    ).to_parquet(clean_dir / "departements.parquet")
    pd.DataFrame(
        {
            "code": ["11"],
            "label": ["Île-de-France"],
        }
    ).to_parquet(clean_dir / "regions.parquet")
    pd.DataFrame(
        {
            "geo_level": ["commune"],
            "region_code": ["11"],
            "departement_code": ["75"],
        }
    ).to_parquet(raw_dir / "odace_dim_geo.parquet")
    pd.DataFrame(
        {
            "code": ["M1805"],
            "label": ["Développement informatique"],
        }
    ).to_parquet(raw_dir / "rome_referential_api.parquet")

    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build_module, "CACHE_DIR", raw_dir)
    monkeypatch.setattr(build_module, "OUTPUT_DIR", output_dir)

    build_module.generate_referentiels(
        {"sources": {"regions_ref": {}}},
        MagicMock(),
    )

    refs = pd.read_parquet(output_dir / "odis_referentiels.parquet")
    regions = refs.loc[refs["key"] == "regions"].set_index("code")["label"].to_dict()
    assert regions == {"11": "Île-de-France"}


@patch("pipeline.ingest.get_odace_client")
def test_clean_codes_postaux_edge_cases(mock_get_client, temp_cache_dirs):
    """Verify float INSEE codes, null rows, and duplicates in codes_postaux."""
    _, clean_dir = temp_cache_dirs
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"commune_insee_code": 1001.0, "code_postal": 1000.0},
            {"commune_insee_code": "2A004", "code_postal": "20000"},
            {"commune_insee_code": None, "code_postal": "75001"},
            {"commune_insee_code": "75001", "code_postal": np.nan},
            {"commune_insee_code": "75001", "code_postal": "75001"},
            {"commune_insee_code": "75001", "code_postal": "75001"},  # duplicate
        ]
    )

    config = {
        "sources": {
            "codes_postaux": {
                "use_odace": True,
                "odace_table": "ref_code_postal",
            }
        }
    }

    clean_codes_postaux(config, MagicMock(spec=PipelineLogger))
    out_file = clean_dir / "codes_postaux.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 3
    # Verify no NaN or '00nan' produced
    assert not res["codgeo"].str.contains("nan", case=False).any()
    assert not res["code_postal"].str.contains("nan", case=False).any()
    # Check normalized codes
    assert res.loc[res["codgeo"] == "01001", "code_postal"].iloc[0] == "01000"
    assert res.loc[res["codgeo"] == "2A004", "code_postal"].iloc[0] == "20000"
    assert res.loc[res["codgeo"] == "75001", "code_postal"].iloc[0] == "75001"


@patch("pipeline.ingest.get_odace_client")
def test_clean_departements_edge_cases(mock_get_client, temp_cache_dirs):
    """Verify numeric float codes, Corsica 2A/2B, DOM 971, duplicates, and null handling."""
    _, clean_dir = temp_cache_dirs
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"code_departement": 1.0, "libelle_departement": " Ain ", "code_region": 84.0},
            {"code_departement": "2A", "libelle_departement": "Corse-du-Sud", "code_region": "94"},
            {"code_departement": 971, "libelle_departement": "Guadeloupe", "code_region": 1.0},
            {"code_departement": None, "libelle_departement": "Unknown", "code_region": "00"},
            {"code_departement": "01", "libelle_departement": "Ain Duplicate", "code_region": "84"},
        ]
    )

    config = {
        "sources": {
            "departements_ref": {
                "use_odace": True,
                "odace_table": "ref_departement",
            }
        }
    }

    clean_departements(config, MagicMock(spec=PipelineLogger))
    out_file = clean_dir / "departements.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 3
    assert set(res["code"]) == {"01", "2A", "971"}
    assert res.loc[res["code"] == "01", "reg_code"].iloc[0] == "84"
    assert res.loc[res["code"] == "971", "reg_code"].iloc[0] == "01"


@patch("pipeline.ingest.get_odace_client")
def test_clean_regions_edge_cases(mock_get_client, temp_cache_dirs):
    """Verify numeric float codes, duplicate deduplication, and null handling in regions."""
    _, clean_dir = temp_cache_dirs
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"code_region": 1.0, "libelle_region": " Guadeloupe "},
            {"code_region": "11", "libelle_region": " Île-de-France "},
            {"code_region": 11.0, "libelle_region": "Île-de-France Dup"},
            {"code_region": None, "libelle_region": "Null Region"},
        ]
    )

    config = {
        "sources": {
            "regions_ref": {
                "use_odace": True,
                "odace_table": "ref_region",
            }
        }
    }

    clean_regions(config, MagicMock(spec=PipelineLogger))
    out_file = clean_dir / "regions.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res["code"]) == {"01", "11"}
    assert res.loc[res["code"] == "01", "label"].iloc[0] == "Guadeloupe"
    assert res.loc[res["code"] == "11", "label"].iloc[0] == "Île-de-France"


@patch("pipeline.ingest.get_odace_client")
def test_clean_nomenclature_waldec_edge_cases(mock_get_client, temp_cache_dirs):
    """Verify 6-digit padding with float/numeric inputs, duplicates, and null handling."""
    _, clean_dir = temp_cache_dirs
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"code_waldec": 100.0, "libelle": " Culture "},
            {"code_waldec": "012345", "libelle": "Santé"},
            {"code_waldec": "100", "libelle": "Culture duplicate"},
            {"code_waldec": None, "libelle": "Missing"},
        ]
    )

    config = {
        "sources": {
            "nomenclature_waldec": {
                "use_odace": True,
                "odace_table": "ref_waldec",
            }
        }
    }

    clean_nomenclature_waldec(config, MagicMock(spec=PipelineLogger))
    out_file = clean_dir / "referentiel_waldec.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res["code"]) == {"000100", "012345"}
    assert res.loc[res["code"] == "000100", "label"].iloc[0] == "Culture"


@patch("pipeline.ingest.get_odace_client")
def test_clean_political_edge_cases(mock_get_client, temp_cache_dirs):
    """Verify float codgeo, duplicates with far-right precedence, and null handling."""
    _, clean_dir = temp_cache_dirs
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            # Float code 1001.0 -> 01001
            {"commune_insee_code": 1001.0, "code_nuance_politique": "RN", "famille_nuance_politique": "Extrême droite"},
            # Duplicate for 01002: one non-far-right, one far-right -> far-right wins
            {"commune_insee_code": "01002", "code_nuance_politique": "SOC", "famille_nuance_politique": "Gauche"},
            {"commune_insee_code": "01002", "code_nuance_politique": "EXD", "famille_nuance_politique": "Extrême droite"},
            # Null codgeo row
            {"commune_insee_code": None, "code_nuance_politique": "RN", "famille_nuance_politique": "Extrême droite"},
        ]
    )

    config = {
        "sources": {
            "political_nuance": {
                "use_odace": True,
                "odace_table": "fact_nuance_politique",
            }
        }
    }

    clean_political(config, MagicMock(spec=PipelineLogger))
    out_file = clean_dir / "political.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res["codgeo"]) == {"01001", "01002"}
    # 01001 -> far-right -> pol_num 0.0
    assert res.loc[res["codgeo"] == "01001", "maire_extreme_droite"].iloc[0] is True or res.loc[res["codgeo"] == "01001", "maire_extreme_droite"].iloc[0] == 1
    assert res.loc[res["codgeo"] == "01001", "pol_num"].iloc[0] == 0.0
    # 01002 -> far-right took precedence -> pol_num 0.0
    assert res.loc[res["codgeo"] == "01002", "maire_extreme_droite"].iloc[0] is True or res.loc[res["codgeo"] == "01002", "maire_extreme_droite"].iloc[0] == 1
    assert res.loc[res["codgeo"] == "01002", "pol_num"].iloc[0] == 0.0


def test_main_cli_step_to_source_resolution():
    """Verify that main CLI with --steps regions,departements invokes fetch_source with proper source keys."""
    from unittest.mock import patch
    from pipeline.ingest.main import main

    sources_seen = []

    def fake_fetch_source(name, cfg, logger):
        sources_seen.append(name)
        return None

    config = {
        "sources": {
            "departements_ref": {"use_odace": True},
            "regions_ref": {"use_odace": True},
            "political_nuance": {"use_odace": True},
            "caf": {"use_odace": True},
        }
    }

    with (
        patch("pipeline.ingest.main.run_clean_step_safely"),
        patch("pipeline.ingest.main.load_config", return_value=config),
        patch("pipeline.ingest.main.PipelineLogger"),
        patch("pipeline.ingest.main.fetch_source", side_effect=fake_fetch_source),
    ):
        main(["--steps", "regions,departements"])
        assert "regions_ref" in sources_seen
        assert "departements_ref" in sources_seen
        assert "political_nuance" not in sources_seen
        assert "caf" not in sources_seen



