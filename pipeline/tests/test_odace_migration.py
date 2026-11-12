import json
import os
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from pipeline.odace_client import OdaceClient, OdasClient, get_odas_client
from pipeline.ingest import (
    clean_bassins_de_vie,
    clean_bpe,
    clean_caf,
    clean_codes_postaux,
    clean_communes,
    clean_departements,
    clean_electoral_history,
    clean_formations,
    clean_housing_occupation,
    clean_inclusion_jobs,
    clean_live_jobs,
    clean_loyers,
    clean_nomenclature_waldec,
    clean_political,
    clean_regions,
    clean_rome,
    clean_salesforce_jaccueille,
    clean_school_effectifs,
    clean_services_inclusion,
    clean_structures_inclusion,
    fetch_rome_referential,
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


def test_transform_housing_occupation_odace_resolves_latest_and_ignores_historical_nulls(
    housing_occupation_contract,
):
    """Verifies that reference_year: 'latest' resolves the newest year and ignores historical nulls."""
    contract = housing_occupation_contract.copy()
    contract["reference_year"] = "latest"

    # 2017 has nulls, 2023 is clean and latest
    rows = [
        # 2017 with null
        {
            "commune_insee_code": "01001",
            "annee": "2017",
            "indicateur_occupation": "MOD_OVER_OCC",
            "valeur": None,
        },
        {
            "commune_insee_code": "01001",
            "annee": "2017",
            "indicateur_occupation": "STD_OCC",
            "valeur": 5.0,
        },
        {
            "commune_insee_code": "01001",
            "annee": "2017",
            "indicateur_occupation": "SEV_UNDER_OCC",
            "valeur": 2.0,
        },
        # 2023 (complete and clean)
        {
            "commune_insee_code": "01001",
            "annee": "2023",
            "indicateur_occupation": "MOD_OVER_OCC",
            "valeur": 10.0,
        },
        {
            "commune_insee_code": "01001",
            "annee": "2023",
            "indicateur_occupation": "STD_OCC",
            "valeur": 20.0,
        },
        {
            "commune_insee_code": "01001",
            "annee": "2023",
            "indicateur_occupation": "SEV_UNDER_OCC",
            "valeur": 30.0,
        },
        {
            "commune_insee_code": "2B002",
            "annee": "2023",
            "indicateur_occupation": "MOD_OVER_OCC",
            "valeur": 15.0,
        },
        {
            "commune_insee_code": "2B002",
            "annee": "2023",
            "indicateur_occupation": "STD_OCC",
            "valeur": 25.0,
        },
        {
            "commune_insee_code": "2B002",
            "annee": "2023",
            "indicateur_occupation": "SEV_UNDER_OCC",
            "valeur": 35.0,
        },
    ]
    df = pd.DataFrame(rows)
    result = transform_housing_occupation_odace(df, contract)
    assert len(result) == 2
    assert result.attrs["reference_year"] == "2023"
    assert result.loc[result["codgeo"] == "01001", "MOD_OVER_OCC"].iloc[0] == 10.0


def test_transform_housing_occupation_odace_rejects_missing_explicit_year(
    housing_occupation_odace_data, housing_occupation_contract
):
    """Explicit reference_year must fail closed if absent instead of falling back to latest."""
    contract = housing_occupation_contract.copy()
    contract["reference_year"] = "2099"
    with pytest.raises(
        PipelineRunError,
        match="has no required observations for reference year 2099",
    ):
        transform_housing_occupation_odace(housing_occupation_odace_data, contract)


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
    assert (
        rn_row["maire_extreme_droite"].iloc[0] is True
        or rn_row["maire_extreme_droite"].iloc[0] == 1
    )
    assert rn_row["pol_num"].iloc[0] == 0.0
    # SOC -> not far right -> pol_num 1.0
    soc_row = res.loc[res["codgeo"] == "01002"]
    assert (
        soc_row["maire_extreme_droite"].iloc[0] is False
        or soc_row["maire_extreme_droite"].iloc[0] == 0
    )
    assert soc_row["pol_num"].iloc[0] == 1.0
    # REC -> far right nuance -> pol_num 0.0
    rec_row = res.loc[res["codgeo"] == "01003"]
    assert (
        rec_row["maire_extreme_droite"].iloc[0] is True
        or rec_row["maire_extreme_droite"].iloc[0] == 1
    )
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
    ).to_parquet(clean_dir / "rome.parquet")

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
            {
                "code_departement": 1.0,
                "libelle_departement": " Ain ",
                "code_region": 84.0,
            },
            {
                "code_departement": "2A",
                "libelle_departement": "Corse-du-Sud",
                "code_region": "94",
            },
            {
                "code_departement": 971,
                "libelle_departement": "Guadeloupe",
                "code_region": 1.0,
            },
            {
                "code_departement": None,
                "libelle_departement": "Unknown",
                "code_region": "00",
            },
            {
                "code_departement": "01",
                "libelle_departement": "Ain Duplicate",
                "code_region": "84",
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
            {
                "commune_insee_code": 1001.0,
                "code_nuance_politique": "RN",
                "famille_nuance_politique": "Extrême droite",
            },
            # Duplicate for 01002: one non-far-right, one far-right -> far-right wins
            {
                "commune_insee_code": "01002",
                "code_nuance_politique": "SOC",
                "famille_nuance_politique": "Gauche",
            },
            {
                "commune_insee_code": "01002",
                "code_nuance_politique": "EXD",
                "famille_nuance_politique": "Extrême droite",
            },
            # Null codgeo row
            {
                "commune_insee_code": None,
                "code_nuance_politique": "RN",
                "famille_nuance_politique": "Extrême droite",
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
    assert len(res) == 2
    assert set(res["codgeo"]) == {"01001", "01002"}
    # 01001 -> far-right -> pol_num 0.0
    assert (
        res.loc[res["codgeo"] == "01001", "maire_extreme_droite"].iloc[0] is True
        or res.loc[res["codgeo"] == "01001", "maire_extreme_droite"].iloc[0] == 1
    )
    assert res.loc[res["codgeo"] == "01001", "pol_num"].iloc[0] == 0.0
    # 01002 -> far-right took precedence -> pol_num 0.0
    assert (
        res.loc[res["codgeo"] == "01002", "maire_extreme_droite"].iloc[0] is True
        or res.loc[res["codgeo"] == "01002", "maire_extreme_droite"].iloc[0] == 1
    )
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


# =====================================================================
# 5. Palier 2: Loyers Migration Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_loyers_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_loyers maps Odace fact_loyer_appartement columns and aggregates mean rent."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"commune_insee_code": "01001", "loyer_predit_m2": 10.5},
            {"commune_insee_code": "01001", "loyer_predit_m2": 11.5},
            {"commune_insee_code": "75001", "loyer_predit_m2": 32.0},
        ]
    )

    config = {
        "sources": {
            "loyers_apparts": {
                "use_odace": True,
                "odace_table": "fact_loyer_appartement",
            }
        }
    }

    clean_loyers(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "loyers.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res.columns) == {"codgeo", "loyer_app_m2"}
    assert res.loc[res["codgeo"] == "01001", "loyer_app_m2"].iloc[0] == 11.0
    assert res.loc[res["codgeo"] == "75001", "loyer_app_m2"].iloc[0] == 32.0
    mock_client.fetch_table.assert_called_once_with("fact_loyer_appartement")


@patch("pipeline.ingest.get_odace_client")
def test_clean_loyers_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_loyers fail-closed when ODAS fetch fails."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("Network error")

    config = {
        "sources": {
            "loyers_apparts": {
                "use_odace": True,
                "odace_table": "fact_loyer_appartement",
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace loyers"):
        clean_loyers(config, MagicMock(spec=PipelineLogger))


def test_clean_loyers_legacy_fallback(temp_cache_dirs):
    """Tests clean_loyers fallback when use_odace is false."""
    raw_dir, clean_dir = temp_cache_dirs
    csv_file = raw_dir / "loyers_apparts_2023.csv"
    csv_file.write_text("INSEE_C;loypredm2\n01001;12,5\n75001;35,0\n", encoding="utf-8")

    config = {
        "sources": {
            "loyers_apparts": {
                "use_odace": False,
                "local_name": "loyers_apparts_2023.csv",
                "sep": ";",
                "encoding": "utf-8",
            }
        }
    }

    clean_loyers(config, MagicMock(spec=PipelineLogger))
    out_file = clean_dir / "loyers.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert res.loc[res["codgeo"] == "01001", "loyer_app_m2"].iloc[0] == 12.5


@patch("pipeline.ingest.get_odace_client")
def test_clean_loyers_edge_cases(mock_get_client, temp_cache_dirs):
    """Tests clean_loyers handles float INSEE codes, nulls, and multiple observations."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"commune_insee_code": 1001.0, "loyer_predit_m2": "10.0"},
            {"commune_insee_code": "2A004", "loyer_predit_m2": 15.0},
            {"commune_insee_code": None, "loyer_predit_m2": 20.0},
            {"commune_insee_code": "75001", "loyer_predit_m2": np.nan},
        ]
    )

    config = {
        "sources": {
            "loyers_apparts": {
                "use_odace": True,
                "odace_table": "fact_loyer_appartement",
            }
        }
    }

    clean_loyers(config, MagicMock(spec=PipelineLogger))
    out_file = clean_dir / "loyers.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res["codgeo"]) == {"01001", "2A004"}


# =====================================================================
# 6. Palier 2: Services Inclusion Migration Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_services_inclusion_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_services_inclusion parses thematiques, cleans slugs, and writes parquet."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {
                "service_id": "srv1",
                "service_nom": "Service Logement",
                "thematiques": "['logement' 'insertion']",
                "latitude": 48.85,
                "longitude": 2.35,
                "commune_insee_code": "75001",
            },
            {
                "service_id": "srv2",
                "service_nom": "Service Santé",
                "thematiques": "['sante']",
                "latitude": 45.75,
                "longitude": 4.85,
                "commune_insee_code": "69123",
            },
        ]
    )

    config = {
        "sources": {
            "services_inclusion": {
                "use_odace": True,
                "odace_table": "dim_service_inclusion",
            }
        }
    }

    clean_services_inclusion(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "services_inclusion.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 3  # srv1 exploded into 2 rows, srv2 into 1 row
    assert set(res.columns) == {
        "id_structure",
        "nom",
        "service_slug",
        "latitude",
        "longitude",
        "codgeo",
    }
    assert set(res.loc[res["id_structure"] == "srv1", "service_slug"]) == {
        "logement",
        "insertion",
    }
    assert res.loc[res["id_structure"] == "srv2", "service_slug"].iloc[0] == "sante"


@patch("pipeline.ingest.get_odace_client")
def test_clean_services_inclusion_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_services_inclusion fail-closed when ODAS fetch fails."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("ODAS timeout")

    config = {
        "sources": {
            "services_inclusion": {
                "use_odace": True,
                "odace_table": "dim_service_inclusion",
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace services_inclusion"):
        clean_services_inclusion(config, MagicMock(spec=PipelineLogger))


@patch("pipeline.ingest.get_odace_client")
def test_clean_services_inclusion_edge_cases(mock_get_client, temp_cache_dirs):
    """Tests clean_services_inclusion with newline-separated arrays, null coords, and empty inputs."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            # Newline separated stringified array from ODAS
            {
                "service_id": "srv_nl",
                "service_nom": "Service Multi",
                "thematiques": "['thematique-1'\n 'thematique-2']",
                "latitude": 48.8,
                "longitude": 2.3,
                "commune_insee_code": 75001.0,
            },
            # Missing coordinates -> dropped
            {
                "service_id": "srv_nocoord",
                "service_nom": "Service Sans Coord",
                "thematiques": "['thematique-3']",
                "latitude": None,
                "longitude": 2.3,
                "commune_insee_code": "75001",
            },
        ]
    )

    config = {
        "sources": {
            "services_inclusion": {
                "use_odace": True,
                "odace_table": "dim_service_inclusion",
            }
        }
    }

    clean_services_inclusion(config, MagicMock(spec=PipelineLogger))
    out_file = clean_dir / "services_inclusion.parquet"
    assert out_file.exists()
    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res["service_slug"]) == {"thematique-1", "thematique-2"}
    assert res["codgeo"].iloc[0] == "75001"


# =====================================================================
# 7. Palier 2: Structures Inclusion Migration Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_structures_inclusion_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_structures_inclusion filters CCAS and contacts, writing both parquet files."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            # CCAS match with contact -> kept
            {
                "structure_id": "struct_ccas_1",
                "structure_nom": "CCAS DE LYON",
                "reseaux_porteurs": "['ccas']",
                "adresse": "1 Place de la Comédie",
                "commune_label": "Lyon",
                "courriel": "contact@ccas-lyon.fr",
                "telephone": "0472000000",
                "site_web": "https://ccas-lyon.fr",
                "commune_insee_code": "69123",
            },
            # Non-CCAS -> filtered out
            {
                "structure_id": "struct_other",
                "structure_nom": "Autre Structure",
                "reseaux_porteurs": "['autre_reseau']",
                "adresse": "10 Rue Test",
                "commune_label": "Paris",
                "courriel": "test@test.fr",
                "telephone": "0100000000",
                "site_web": None,
                "commune_insee_code": "75001",
            },
            # CCAS without any contact info -> dropped by contact filter
            {
                "structure_id": "struct_nocontact",
                "structure_nom": "CCAS SANS CONTACT",
                "reseaux_porteurs": "['ccas']",
                "adresse": "Route Inconnue",
                "commune_label": "Nulle Part",
                "courriel": "",
                "telephone": "",
                "site_web": "",
                "commune_insee_code": "01001",
            },
        ]
    )

    config = {
        "sources": {
            "structures_inclusion": {
                "use_odace": True,
                "odace_table": "dim_structure_inclusion",
            }
        }
    }

    clean_structures_inclusion(config, MagicMock(spec=PipelineLogger))

    # Verify both structures_inclusion.parquet and odis_ccas.parquet are written
    struct_file = clean_dir / "structures_inclusion.parquet"
    ccas_file = clean_dir / "odis_ccas.parquet"
    assert struct_file.exists()
    assert ccas_file.exists()

    res = pd.read_parquet(struct_file)
    assert len(res) == 1
    assert res.iloc[0]["id"] == "struct_ccas_1"
    assert res.iloc[0]["nom"] == "CCAS DE LYON"
    assert res.iloc[0]["codgeo"] == "69123"


@patch("pipeline.ingest.get_odace_client")
def test_clean_structures_inclusion_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_structures_inclusion fail-closed when ODAS fetch fails."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("API connection dropped")

    config = {
        "sources": {
            "structures_inclusion": {
                "use_odace": True,
                "odace_table": "dim_structure_inclusion",
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace structures_inclusion"):
        clean_structures_inclusion(config, MagicMock(spec=PipelineLogger))


# =====================================================================
# 8. Palier 2: School Effectifs Migration Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_school_effectifs_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_school_effectifs filters latest school year and aggregates metrics."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    def fake_fetch_table(table_name, **kwargs):
        if table_name == "fact_effectif_scolaire":
            return pd.DataFrame(
                [
                    # Older year (should be ignored)
                    {
                        "numero_ecole": "UAI001",
                        "annee_rentree_scolaire": "2023-01-01",
                        "nombre_total_eleves": 100,
                        "nombre_total_classes": 5,
                    },
                    # Latest year: school 1 in 01001 (15 students, 1 class -> <20 -> risky)
                    {
                        "numero_ecole": "UAI001",
                        "annee_rentree_scolaire": "2024-01-01",
                        "nombre_total_eleves": 15,
                        "nombre_total_classes": 1,
                    },
                    # Latest year: school 2 in 01001 (25 students, 1 class -> >=20 -> not risky)
                    {
                        "numero_ecole": "UAI002",
                        "annee_rentree_scolaire": "2024-01-01",
                        "nombre_total_eleves": 25,
                        "nombre_total_classes": 1,
                    },
                ]
            )
        elif table_name == "dim_etablissement_scolaire":
            return pd.DataFrame(
                [
                    {"uai_code": "UAI001", "commune_insee_code": "01001"},
                    {"uai_code": "UAI002", "commune_insee_code": "01001"},
                ]
            )
        raise ValueError(f"Unexpected table: {table_name}")

    mock_client.fetch_table.side_effect = fake_fetch_table

    config = {
        "sources": {
            "education_effectifs": {
                "use_odace": True,
                "odace_table": "fact_effectif_scolaire",
            },
            "education_annuaire": {
                "use_odace": True,
                "odace_table": "dim_etablissement_scolaire",
            },
        }
    }

    clean_school_effectifs(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "school_effectifs.parquet"
    bpe_file = clean_dir / "bpe_education_effectifs.parquet"
    assert out_file.exists()
    assert bpe_file.exists()

    res = pd.read_parquet(out_file)
    assert len(res) == 1
    assert res.iloc[0]["codgeo"] == "01001"
    assert res.iloc[0]["total_eleves"] == 40
    assert res.iloc[0]["total_classes"] == 2
    assert res.iloc[0]["risky_schools_count"] == 1
    assert res.iloc[0]["ecoles_count"] == 2
    assert res.iloc[0]["avg_students_per_class_commune"] == 20.0


@patch("pipeline.ingest.get_odace_client")
def test_clean_school_effectifs_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_school_effectifs fail-closed when ODAS fetch fails."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("ODAS effectif error")

    config = {
        "sources": {
            "education_effectifs": {
                "use_odace": True,
                "odace_table": "fact_effectif_scolaire",
            },
            "education_annuaire": {
                "use_odace": True,
                "odace_table": "dim_etablissement_scolaire",
            },
        }
    }

    with pytest.raises(PipelineRunError, match="Odace education_effectifs"):
        clean_school_effectifs(config, MagicMock(spec=PipelineLogger))


# =====================================================================
# 9. Palier 2: Formations Referentiel & Annuaire Migration Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_formations_referentiel_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_formations with ODAS ref_formation."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {"code_formation": "100", "libelle_formation": " Formations générales "},
            {"code_formation": "12", "libelle_formation": "Informatique"},
        ]
    )

    # Need dummy codes_postaux to avoid early skip in clean_formations
    pd.DataFrame({"code_postal": ["75001"], "codgeo": ["75001"]}).to_parquet(
        clean_dir / "codes_postaux.parquet"
    )

    config = {
        "sources": {
            "formations_referentiel": {
                "use_odace": True,
                "odace_table": "ref_formation",
            },
            "formations_annuaire": {"use_odace": False, "local_name": "absent.csv"},
        }
    }

    clean_formations(config, MagicMock(spec=PipelineLogger))

    ref_file = clean_dir / "formations_referentiel.parquet"
    assert ref_file.exists()
    res = pd.read_parquet(ref_file)
    assert len(res) == 2
    assert set(res.columns) == {"code", "label"}
    assert res.loc[res["code"] == "100", "label"].iloc[0] == "Formations générales"
    assert res.loc[res["code"] == "012", "label"].iloc[0] == "Informatique"


@patch("pipeline.ingest.get_odace_client")
def test_clean_formations_annuaire_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_formations with ODAS dim_organisme_formation melting specialities."""
    _, clean_dir = temp_cache_dirs

    # Prepare codes_postaux.parquet
    pd.DataFrame(
        [
            {"code_postal": "01000", "codgeo": "01001"},
            {"code_postal": "75001", "codgeo": "75001"},
        ]
    ).to_parquet(clean_dir / "codes_postaux.parquet")

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    def fake_fetch_table(table_name, **kwargs):
        if table_name == "ref_formation":
            return pd.DataFrame(
                [{"code_formation": "100", "libelle_formation": "Général"}]
            )
        elif table_name == "dim_organisme_formation":
            return pd.DataFrame(
                [
                    {
                        "code_postal": 1000.0,
                        "code_specialite_1": "100",
                        "code_specialite_2": "110",
                        "code_specialite_3": "nan",
                    },
                    {
                        "code_postal": "75001",
                        "code_specialite_1": "100",
                        "code_specialite_2": "nan",
                        "code_specialite_3": "nan",
                    },
                ]
            )
        raise ValueError(table_name)

    mock_client.fetch_table.side_effect = fake_fetch_table

    config = {
        "sources": {
            "formations_referentiel": {
                "use_odace": True,
                "odace_table": "ref_formation",
            },
            "formations_annuaire": {
                "use_odace": True,
                "odace_table": "dim_organisme_formation",
            },
        }
    }

    clean_formations(config, MagicMock(spec=PipelineLogger))

    annuaire_file = clean_dir / "formations_annuaire.parquet"
    assert annuaire_file.exists()
    res = pd.read_parquet(annuaire_file)
    assert len(res) == 3
    assert set(res.columns) == {"codgeo", "formation_code"}
    # 01001 has 100 and 110, 75001 has 100
    assert set(res.loc[res["codgeo"] == "01001", "formation_code"]) == {"100", "110"}
    assert set(res.loc[res["codgeo"] == "75001", "formation_code"]) == {"100"}


@patch("pipeline.ingest.get_odace_client")
def test_clean_formations_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_formations fail-closed on ODAS errors."""
    _, clean_dir = temp_cache_dirs
    pd.DataFrame({"code_postal": ["75001"], "codgeo": ["75001"]}).to_parquet(
        clean_dir / "codes_postaux.parquet"
    )

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("ODAS formation error")

    config = {
        "sources": {
            "formations_referentiel": {
                "use_odace": True,
                "odace_table": "ref_formation",
            },
            "formations_annuaire": {"use_odace": False},
        }
    }

    with pytest.raises(PipelineRunError, match="Odace formations_referentiel"):
        clean_formations(config, MagicMock(spec=PipelineLogger))


# =====================================================================
# 10. Palier 2: ROME Referentiel Migration Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_rome_odace_success(mock_get_client, temp_cache_dirs):
    """Tests clean_rome with ODAS ref_rome."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {
                "code_rome": "M1805",
                "libelle_rome": " Études et développement informatique ",
            },
            {"code_rome": "K1101", "libelle_rome": "Accompagnement médicosocial"},
        ]
    )

    config = {
        "sources": {
            "rome": {
                "use_odace": True,
                "odace_table": "ref_rome",
                "ttl_days": 365,
            }
        }
    }

    clean_rome(config, MagicMock(spec=PipelineLogger))
    out_file = clean_dir / "rome.parquet"
    assert out_file.exists()

    res = pd.read_parquet(out_file)
    assert len(res) == 2
    assert set(res.columns) == {"code", "label"}
    assert (
        res.loc[res["code"] == "M1805", "label"].iloc[0]
        == "Études et développement informatique"
    )
    assert (
        res.loc[res["code"] == "K1101", "label"].iloc[0]
        == "Accompagnement médicosocial"
    )
    mock_client.fetch_table.assert_called_once_with("ref_rome", ttl_days=365)


@patch("pipeline.ingest.get_odace_client")
def test_clean_rome_odace_failure(mock_get_client, temp_cache_dirs):
    """Tests clean_rome fail-closed when ODAS fetch fails."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("ODAS ROME error")

    config = {
        "sources": {
            "rome": {
                "use_odace": True,
                "odace_table": "ref_rome",
                "ttl_days": 365,
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace rome"):
        clean_rome(config, MagicMock(spec=PipelineLogger))


@patch("pipeline.ingest.get_odace_client")
def test_fetch_rome_referential_backward_compat(mock_get_client, temp_cache_dirs):
    """Tests deprecated fetch_rome_referential backward compatibility wrapper."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [{"code_rome": "M1805", "libelle_rome": "Informatique"}]
    )

    config = {
        "sources": {
            "rome": {
                "use_odace": True,
                "odace_table": "ref_rome",
                "ttl_days": 365,
            }
        }
    }

    path = fetch_rome_referential(config, MagicMock(spec=PipelineLogger))
    assert path == clean_dir / "rome.parquet"
    assert path.exists()


# =====================================================================
# 11. Palier 2: Build Integration & SIAE Tests
# =====================================================================


def test_build_generate_referentiels_uses_odas_ref_service_inclusion(
    tmp_path, monkeypatch
):
    """Verify generate_referentiels loads ref_service_inclusion from ODAS cache."""
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
    pd.DataFrame({"code": ["75"], "label": ["Paris"], "reg_code": ["11"]}).to_parquet(
        clean_dir / "departements.parquet"
    )
    pd.DataFrame({"code": ["11"], "label": ["Île-de-France"]}).to_parquet(
        clean_dir / "regions.parquet"
    )
    pd.DataFrame(
        {"geo_level": ["commune"], "region_code": ["11"], "departement_code": ["75"]}
    ).to_parquet(raw_dir / "odace_dim_geo.parquet")

    # ODAS cache for ref_service_inclusion
    pd.DataFrame(
        [
            {
                "service_id": "srv_incl_1",
                "nom_service": "Aide au logement",
                "type_service": "accompagnement",
            }
        ]
    ).to_parquet(raw_dir / "odace_ref_service_inclusion.parquet")

    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build_module, "CACHE_DIR", raw_dir)
    monkeypatch.setattr(build_module, "OUTPUT_DIR", output_dir)

    config = {
        "sources": {
            "referentiel_services_inclusion": {
                "use_odace": True,
                "odace_table": "ref_service_inclusion",
            }
        }
    }

    build_module.generate_referentiels(config, MagicMock())

    refs = pd.read_parquet(output_dir / "odis_referentiels.parquet")
    incl_refs = refs[refs["key"] == "inclusion_services"]
    assert len(incl_refs) == 1
    assert incl_refs.iloc[0]["code"] == "srv_incl_1"
    assert incl_refs.iloc[0]["label"] == "Aide au logement"


def test_build_siae_structures_from_odas_with_cached_geo(tmp_path, monkeypatch):
    """Verify merge_siae_structures resolves commune_sk via cached dim_geo and merges inc_siae_count."""
    import pipeline.build as build_module

    raw_dir = tmp_path / "raw"
    clean_dir = tmp_path / "clean"
    output_dir = tmp_path / "output"
    raw_dir.mkdir(parents=True)
    clean_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    # ODAS cache for dim_siae_structure with commune_sk (real ODAS schema)
    pd.DataFrame(
        [
            {
                "siae_structure_sk": "s1",
                "commune_sk": "c_01001",
                "code_postal": "01000",
            },
            {
                "siae_structure_sk": "s2",
                "commune_sk": "c_01001",
                "code_postal": "01000",
            },
            {
                "siae_structure_sk": "s3",
                "commune_sk": "c_75001",
                "code_postal": "75001",
            },
            {"siae_structure_sk": "s4", "commune_sk": None, "code_postal": "01000"},
        ]
    ).to_parquet(raw_dir / "odace_dim_siae_structure.parquet")

    # Cached dim_geo
    pd.DataFrame(
        [
            {
                "geo_level": "commune",
                "commune_sk": "c_01001",
                "commune_insee_code": "01001",
            },
            {
                "geo_level": "commune",
                "commune_sk": "c_75001",
                "commune_insee_code": "75001",
            },
        ]
    ).to_parquet(raw_dir / "odace_dim_geo.parquet")

    # Clean codes_postaux for postal code recovery
    pd.DataFrame(
        [
            {"code_postal": "01000", "codgeo": "01001"},
        ]
    ).to_parquet(clean_dir / "codes_postaux.parquet")

    monkeypatch.setattr(build_module, "CACHE_DIR", raw_dir)
    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build_module, "OUTPUT_DIR", output_dir)

    config = {
        "sources": {
            "siae_structures": {
                "use_odace": True,
                "odace_table": "dim_siae_structure",
            }
        }
    }

    communes_df = pd.DataFrame({"codgeo": ["01001", "75001", "99999"]})
    res = build_module.merge_siae_structures(communes_df, config, MagicMock())

    # s1, s2 resolved via commune_sk + s4 recovered via code_postal = 3 for 01001
    assert res.loc[res["codgeo"] == "01001", "inc_siae_count"].iloc[0] == 3
    # s3 resolved via commune_sk = 1 for 75001
    assert res.loc[res["codgeo"] == "75001", "inc_siae_count"].iloc[0] == 1
    # 99999 has no SIAE structures -> NaN
    assert np.isnan(res.loc[res["codgeo"] == "99999", "inc_siae_count"].iloc[0])


@patch("pipeline.odace_client.get_odace_client")
def test_build_siae_structures_cold_cache_fetches_dim_geo(
    mock_get_client, tmp_path, monkeypatch
):
    """Verify merge_siae_structures fetches dim_geo from ODAS when not cached."""
    import pipeline.build as build_module

    raw_dir = tmp_path / "raw"
    clean_dir = tmp_path / "clean"
    raw_dir.mkdir(parents=True)
    clean_dir.mkdir(parents=True)

    # dim_siae_structure with commune_sk
    pd.DataFrame(
        [
            {"siae_structure_sk": "s1", "commune_sk": "c_33001"},
        ]
    ).to_parquet(raw_dir / "odace_dim_siae_structure.parquet")

    # Note: odace_dim_geo.parquet is NOT in raw_dir (cold cache)
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {
                "geo_level": "commune",
                "commune_sk": "c_33001",
                "commune_insee_code": "33001",
            },
        ]
    )

    monkeypatch.setattr(build_module, "CACHE_DIR", raw_dir)
    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)

    config = {
        "sources": {
            "siae_structures": {
                "use_odace": True,
                "odace_table": "dim_siae_structure",
            }
        }
    }

    communes_df = pd.DataFrame({"codgeo": ["33001", "75056"]})
    res = build_module.merge_siae_structures(communes_df, config, MagicMock())

    assert res.loc[res["codgeo"] == "33001", "inc_siae_count"].iloc[0] == 1
    assert np.isnan(res.loc[res["codgeo"] == "75056", "inc_siae_count"].iloc[0])
    mock_client.fetch_table.assert_called_once_with("dim_geo", ttl_days=365)


def test_build_siae_structures_fallback_to_structures_inclusion(tmp_path, monkeypatch):
    """Verify merge_siae_structures falls back to clean structures_inclusion when ODAS has no valid codgeo."""
    import pipeline.build as build_module

    raw_dir = tmp_path / "raw"
    clean_dir = tmp_path / "clean"
    raw_dir.mkdir(parents=True)
    clean_dir.mkdir(parents=True)

    # Empty/unresolvable ODAS siae
    pd.DataFrame(
        [
            {"siae_structure_sk": "s1", "commune_sk": "unknown_sk"},
        ]
    ).to_parquet(raw_dir / "odace_dim_siae_structure.parquet")

    # Ingest clean structures_inclusion (fallback CCAS/structures)
    pd.DataFrame(
        [
            {"id": "c1", "codgeo": "01001"},
            {"id": "c2", "codgeo": "01001"},
        ]
    ).to_parquet(clean_dir / "structures_inclusion.parquet")

    monkeypatch.setattr(build_module, "CACHE_DIR", raw_dir)
    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)

    config = {
        "sources": {
            "siae_structures": {
                "use_odace": True,
                "odace_table": "dim_siae_structure",
            }
        }
    }

    communes_df = pd.DataFrame({"codgeo": ["01001", "75001"]})
    res = build_module.merge_siae_structures(communes_df, config, MagicMock())

    assert res.loc[res["codgeo"] == "01001", "inc_siae_count"].iloc[0] == 2
    assert np.isnan(res.loc[res["codgeo"] == "75001", "inc_siae_count"].iloc[0])


@patch("pipeline.ingest.get_odace_client")
def test_clean_formations_referentiel_without_codes_postaux(
    mock_get_client, temp_cache_dirs
):
    """Verify clean_formations succeeds for formations_referentiel even when codes_postaux is missing."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {
                "code_formation": "110",
                "libelle_formation": "Spécialités pluriscientifiques",
            },
            {
                "code_formation": "200",
                "libelle_formation": "Technologies industrielles",
            },
        ]
    )

    config = {
        "sources": {
            "formations_referentiel": {
                "use_odace": True,
                "odace_table": "ref_formation",
            },
            "formations_annuaire": {
                "use_odace": True,
                "odace_table": "dim_organisme_formation",
            },
        }
    }

    clean_formations(config, MagicMock(spec=PipelineLogger))

    ref_file = clean_dir / "formations_referentiel.parquet"
    assert ref_file.exists()
    df_ref = pd.read_parquet(ref_file)
    assert len(df_ref) == 2
    assert set(df_ref.columns) == {"code", "label"}
    assert df_ref.iloc[0]["code"] == "110"


# =====================================================================
# 4. Bassins de Vie Tests (Palier 1 Addendum)
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_bassins_de_vie_odace_success(mock_get_client, temp_cache_dirs):
    """Verify clean_bassins_de_vie fetches from ODAS ref_bassin_vie and saves parquet."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {
                "commune_sk": "sk_01001",
                "commune_insee_code": "01001",
                "code_bassin_vie": "01053",
                "libelle_bassin_vie": "Bourg-en-Bresse",
            },
            {
                "commune_sk": "sk_75056",
                "commune_insee_code": "75056",
                "code_bassin_vie": "75056",
                "libelle_bassin_vie": "Paris",
            },
        ]
    )

    config = {
        "sources": {
            "bassins_de_vie": {
                "use_odace": True,
                "odace_table": "ref_bassin_vie",
                "ttl_days": 365,
            }
        }
    }

    clean_bassins_de_vie(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "bassins_de_vie.parquet"
    assert out_file.exists()
    df = pd.read_parquet(out_file)
    assert len(df) == 2
    assert "codgeo" in df.columns
    assert "bassin_de_vie" in df.columns
    assert "libelle_bassin_de_vie" in df.columns
    assert "commune_sk" in df.columns
    assert df.loc[df["codgeo"] == "01001", "bassin_de_vie"].iloc[0] == "01053"
    assert df.loc[df["codgeo"] == "75056", "libelle_bassin_de_vie"].iloc[0] == "Paris"


@patch("pipeline.ingest.get_odace_client")
def test_clean_bassins_de_vie_odace_failure(mock_get_client, temp_cache_dirs):
    """Verify clean_bassins_de_vie raises PipelineRunError on ODAS failure."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = RuntimeError("Network error")

    config = {
        "sources": {
            "bassins_de_vie": {
                "use_odace": True,
                "odace_table": "ref_bassin_vie",
            }
        }
    }

    with pytest.raises(
        PipelineRunError, match="clean_bassins_de_vie failed with Odace"
    ):
        clean_bassins_de_vie(config, MagicMock(spec=PipelineLogger))


def test_clean_bassins_de_vie_legacy_fallback(temp_cache_dirs):
    """Verify clean_bassins_de_vie falls back to raw file when use_odace is false."""
    raw_dir, clean_dir = temp_cache_dirs

    df_raw = pd.DataFrame(
        [
            {
                "Code géographique": "01001",
                "Bassin de vie 2022": "01053",
                "Libellé géographique du bassin de vie 2022": "Bourg-en-Bresse",
            }
        ]
    )

    with patch("pipeline.ingest.load_dataset", return_value=df_raw):
        config = {
            "sources": {
                "bassins_de_vie": {
                    "use_odace": False,
                    "archive_file": "BV2022.xlsx",
                }
            }
        }
        (raw_dir / "BV2022.xlsx").touch()

        clean_bassins_de_vie(config, MagicMock(spec=PipelineLogger))

        out_file = clean_dir / "bassins_de_vie.parquet"
        assert out_file.exists()
        df = pd.read_parquet(out_file)
        assert len(df) == 1
        assert df.iloc[0]["codgeo"] == "01001"
        assert df.iloc[0]["bassin_de_vie"] == "01053"


@patch("pipeline.ingest.get_odace_client")
def test_clean_bassins_de_vie_edge_cases(mock_get_client, temp_cache_dirs):
    """Verify clean_bassins_de_vie handles duplicates, nulls, and padding correctly."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = pd.DataFrame(
        [
            {
                "commune_sk": "sk_1",
                "commune_insee_code": "1001",  # 4 chars -> pads to 01001
                "code_bassin_vie": "1053",  # 4 chars -> pads to 01053
                "libelle_bassin_vie": " Bourg-en-Bresse  ",
            },
            {
                "commune_sk": "sk_dup",
                "commune_insee_code": "01001",  # duplicate
                "code_bassin_vie": "01053",
                "libelle_bassin_vie": "Bourg-en-Bresse",
            },
            {
                "commune_sk": "sk_null",
                "commune_insee_code": None,  # should be dropped
                "code_bassin_vie": "01053",
                "libelle_bassin_vie": "Unknown",
            },
        ]
    )

    config = {
        "sources": {
            "bassins_de_vie": {
                "use_odace": True,
                "odace_table": "ref_bassin_vie",
            }
        }
    }

    clean_bassins_de_vie(config, MagicMock(spec=PipelineLogger))

    out_file = clean_dir / "bassins_de_vie.parquet"
    df = pd.read_parquet(out_file)
    assert len(df) == 1
    assert df.iloc[0]["codgeo"] == "01001"
    assert df.iloc[0]["bassin_de_vie"] == "01053"
    assert df.iloc[0]["libelle_bassin_de_vie"] == "Bourg-en-Bresse"


def test_build_communes_uses_clean_bassins_de_vie(tmp_path, monkeypatch):
    """Verify build_communes loads clean bassins_de_vie.parquet and patches PLM arrondissements."""
    import pipeline.build as build_mod

    clean_dir = tmp_path / "clean"
    output_dir = tmp_path / "output"
    cache_dir = tmp_path / "cache"
    clean_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(build_mod, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build_mod, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(build_mod, "CACHE_DIR", cache_dir)

    # Write required referential files to satisfy generate_referentiels contract
    pd.DataFrame({"codgeo": ["75056"], "nom": ["Paris"]}).to_parquet(
        clean_dir / "communes.parquet"
    )
    pd.DataFrame({"code": ["75"], "label": ["Paris"], "reg_code": ["11"]}).to_parquet(
        clean_dir / "departements.parquet"
    )
    pd.DataFrame({"code": ["11"], "label": ["Île-de-France"]}).to_parquet(
        clean_dir / "regions.parquet"
    )

    # Write clean bassins_de_vie.parquet
    pd.DataFrame(
        [
            {
                "codgeo": "01001",
                "bassin_de_vie": "01053",
                "libelle_bassin_de_vie": "Bourg",
            },
            {
                "codgeo": "75056",
                "bassin_de_vie": "75056",
                "libelle_bassin_de_vie": "Paris",
            },
            {
                "codgeo": "69123",
                "bassin_de_vie": "69123",
                "libelle_bassin_de_vie": "Lyon",
            },
            {
                "codgeo": "13055",
                "bassin_de_vie": "13055",
                "libelle_bassin_de_vie": "Marseille",
            },
        ]
    ).to_parquet(clean_dir / "bassins_de_vie.parquet")

    config = {
        "sources": {
            "bassins_de_vie": {
                "use_odace": True,
                "odace_table": "ref_bassin_vie",
            }
        }
    }

    # Test generate_referentiels loading bassins_de_vie
    build_mod.generate_referentiels(
        config=config,
        logger=MagicMock(spec=PipelineLogger),
    )
    ref_file = output_dir / "odis_referentiels.parquet"
    assert ref_file.exists()
    refs = pd.read_parquet(ref_file)
    bv_refs = refs[refs["key"] == "bassins_de_vie"]
    assert len(bv_refs) == 4
    assert "01053" in bv_refs["code"].values
    assert "75056" in bv_refs["code"].values


def test_main_cli_bassins_de_vie_step_registered():
    """Verify that main CLI registers bassins_de_vie in steps_map and step_to_source_map."""
    from pipeline.ingest.config import get_domain_contracts

    contracts = get_domain_contracts()
    assert "bassins_de_vie" in contracts.runner.step_to_source_map
    assert contracts.runner.step_to_source_map["bassins_de_vie"] == "bassins_de_vie"
    assert "bassins_de_vie" in contracts.runner.clean_filenames
    assert (
        contracts.runner.clean_filenames["bassins_de_vie"] == "bassins_de_vie.parquet"
    )


@patch("pipeline.ingest.get_odace_client")
def test_clean_population_details_success(mock_get_client, temp_cache_dirs):
    """Verifies clean_population_details unstacking and contract validation for 2017/2023."""
    from pipeline.ingest import clean_population_details

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # Generate 30005 communes for 2017 and 2023
    n = 30005
    communes = [f"{i:05d}" for i in range(1, n + 1)]
    records = []
    for yr in ["2017", "2023"]:
        for age in ["Y_LT15", "Y25T39"]:
            for code in communes:
                records.append(
                    {
                        "commune_insee_code": code,
                        "annee": yr,
                        "sexe": "_T",
                        "tranche_age": age,
                        "valeur": 50.0,
                    }
                )

    mock_client.fetch_table.return_value = pd.DataFrame(records)

    config = {
        "sources": {
            "population_details": {
                "use_odace": True,
                "odace_table": "fact_demographie",
            }
        }
    }

    clean_population_details(config, MagicMock(spec=PipelineLogger))

    _raw_dir, clean_dir = temp_cache_dirs
    output_file = clean_dir / "population_details.parquet"
    assert output_file.exists()

    df = pd.read_parquet(output_file)
    assert len(df) == n
    assert "pop_jeune_2017" in df.columns
    assert "pop_jeune_2023" in df.columns
    assert "pop_active_2017" in df.columns
    assert "pop_active_2023" in df.columns
    assert df["pop_jeune_2023"].notna().sum() == n


@patch("pipeline.ingest.get_odace_client")
def test_clean_population_details_insufficient_years(mock_get_client, temp_cache_dirs):
    """Verifies clean_population_details fails closed if fewer than 2 census years exist."""
    from pipeline.ingest import clean_population_details

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    n = 30005
    communes = [f"{i:05d}" for i in range(1, n + 1)]
    records = []
    # Only single year
    for age in ["Y_LT15", "Y25T39"]:
        for code in communes:
            records.append(
                {
                    "commune_insee_code": code,
                    "annee": "2023",
                    "sexe": "_T",
                    "tranche_age": age,
                    "valeur": 50.0,
                }
            )

    mock_client.fetch_table.return_value = pd.DataFrame(records)

    config = {
        "sources": {
            "population_details": {
                "use_odace": True,
                "odace_table": "fact_demographie",
            }
        }
    }

    with pytest.raises(PipelineRunError, match="requires at least 2 census years"):
        clean_population_details(config, MagicMock(spec=PipelineLogger))


# =====================================================================
# ODAS Migration: EPCI & Electoral History Tests
# =====================================================================


@patch("pipeline.ingest.get_odace_client")
def test_clean_communes_epci_odace_success(mock_get_client, temp_cache_dirs):
    """Verifies clean_communes uses ODAS ref_commune_epci to map EPCIs."""
    _, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    def mock_fetch(table, ttl_days=None):
        if table == "ref_commune_geo":
            return pd.DataFrame(
                {
                    "commune_insee_code": ["75001", "13054"],
                    "geometrie_geojson": [
                        json.dumps({"type": "Point", "coordinates": [2.3, 48.8]}),
                        json.dumps({"type": "Point", "coordinates": [5.2, 43.4]}),
                    ],
                }
            )
        elif table == "ref_commune_epci":
            return pd.DataFrame(
                {
                    "commune_insee_code": ["75001", "13054"],
                    "code_epci": ["200054781", "200054807"],
                }
            )
        elif table == "ref_epci":
            return pd.DataFrame(
                {
                    "code_epci": ["200054781", "200054807"],
                    "libelle_epci": ["Métropole du Grand Paris", "Métropole d'Aix-Marseille-Provence"],
                }
            )
        return pd.DataFrame()

    mock_client.fetch_table.side_effect = mock_fetch
    mock_client.fetch_dim_commune.return_value = pd.DataFrame(
        {
            "commune_sk": ["sk-75001", "sk-13054"],
            "commune_insee_code": ["75001", "13054"],
            "commune_label": ["Paris 1er", "Marignane"],
            "departement_code": ["75", "13"],
            "region_code": ["11", "93"],
        }
    )

    config = {
        "sources": {
            "communes": {
                "use_odace": True,
                "odace_table": "ref_commune_geo",
                "ttl_days": 30,
            },
            "ref_epci": {
                "use_odace": True,
                "odace_table": "ref_epci",
                "ttl_days": 90,
            },
            "ref_commune_epci": {
                "use_odace": True,
                "odace_table": "ref_commune_epci",
                "ttl_days": 90,
            },
        }
    }

    clean_communes(config, MagicMock(spec=PipelineLogger))

    result = pd.read_parquet(clean_dir / "communes.parquet")
    assert len(result) == 2
    assert "epci" in result.columns
    assert result.loc[result["codgeo"] == "75001", "epci"].iloc[0] == "200054781"
    assert result.loc[result["codgeo"] == "13054", "epci"].iloc[0] == "200054807"


@patch("pipeline.ingest.get_odace_client")
def test_clean_electoral_history_odace_success(mock_get_client, temp_cache_dirs):
    """Verifies clean_electoral_history ingests and processes ODAS Silver schema."""
    raw_dir, clean_dir = temp_cache_dirs

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # Write ODAS-formatted parquet to raw_dir
    fake_odas_elections = pd.DataFrame(
        [
            {
                "code_election": "2026_muni_t1",
                "commune_insee_code": "17347",
                "code_nuance_politique": "LSOC",
                "libelle_abrege_liste": "",
                "nom_candidat": "DUPONT",
                "nb_voix": 500,
            },
            {
                "code_election": "2026_muni_t1",
                "commune_insee_code": "17347",
                "code_nuance_politique": "RN",
                "libelle_abrege_liste": "",
                "nom_candidat": "MARTIN",
                "nb_voix": 200,
            },
            {
                "code_election": "2022_pres_t2",
                "commune_insee_code": "17347",
                "code_nuance_politique": "",
                "libelle_abrege_liste": "",
                "nom_candidat": "MACRON",
                "nb_voix": 800,
            },
            # Election to filter out (legislatives)
            {
                "code_election": "2024_legi_t2",
                "commune_insee_code": "17347",
                "code_nuance_politique": "UG",
                "libelle_abrege_liste": "",
                "nom_candidat": "DURAND",
                "nb_voix": 600,
            },
        ]
    )

    def mock_fetch_table(table, ttl_days=None):
        out_file = raw_dir / f"odace_{table}.parquet"
        fake_odas_elections.to_parquet(out_file)
        return fake_odas_elections

    mock_client.fetch_table.side_effect = mock_fetch_table

    config = {
        "sources": {
            "electoral_history": {
                "use_odace": True,
                "odace_table": "fact_resultat_election_candidat",
                "ttl_days": 365,
            }
        }
    }

    clean_electoral_history(config, MagicMock(spec=PipelineLogger))

    output_path = clean_dir / "electoral_history.parquet"
    assert output_path.exists()

    df = pd.read_parquet(output_path)
    assert not df.empty
    assert "codgeo" in df.columns
    assert "electoral_history" in df.columns
    assert df.iloc[0]["codgeo"] == "17347"

    hist = json.loads(df.iloc[0]["electoral_history"])
    assert "municipales" in hist
    assert "presidentielles" in hist
    assert "legislatives" not in hist


@patch("pipeline.ingest.get_odace_client")
def test_clean_electoral_history_odace_failure(mock_get_client, temp_cache_dirs):
    """Verifies clean_electoral_history raises PipelineRunError on ODAS fetch failure."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("ODAS network timeout")

    config = {
        "sources": {
            "electoral_history": {
                "use_odace": True,
                "odace_table": "fact_resultat_election_candidat",
                "ttl_days": 365,
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace electoral_history did not produce a usable dataset"):
        clean_electoral_history(config, MagicMock(spec=PipelineLogger))


@patch("pipeline.ingest.get_odace_client")
def test_clean_inclusion_jobs_odace_success(mock_get_client, tmp_path):
    """Verifies clean_inclusion_jobs ingests ODAS fact_siae_poste in Mode A (metro only)."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # Prepare mock data
    df_postes_data = pd.DataFrame(
        [
            # 1. Metro with explicit lieu
            {
                "poste_id": 101,
                "structure_siret": "12345678900010",
                "commune_sk": "sk-75001",
                "lieu_commune_insee_code": "75001",
                "rome_code": "A1203",
                "postes_nombre": 2,
                "intitule_poste": "Ouvrier agricole",
                "description_poste": "Travail de la terre",
                "profil_recherche": "Motivation",
                "contrat_type": "CDD insertion",
            },
            # 2. Metro with missing lieu (resolved via commune_sk)
            {
                "poste_id": 102,
                "structure_siret": "98765432100020",
                "commune_sk": "sk-13054",
                "lieu_commune_insee_code": None,
                "rome_code": "M1601",
                "postes_nombre": 1,
                "intitule_poste": "Agent d'accueil",
                "description_poste": "Accueil du public",
                "profil_recherche": "Sens relationnel",
                "contrat_type": "GEIQ Alternance",
            },
            # 3. DOM-TOM post (should be excluded in Mode A sans DOM-TOM)
            {
                "poste_id": 103,
                "structure_siret": "55555555500010",
                "commune_sk": "sk-97101",
                "lieu_commune_insee_code": "97101",
                "rome_code": "K2205",
                "postes_nombre": 1,
                "intitule_poste": "Aide soignant",
                "description_poste": "Soin",
                "profil_recherche": "Diplôme",
                "contrat_type": "CDD",
            },
            # 4. Duplicate poste_id 101 (should be deduplicated)
            {
                "poste_id": 101,
                "structure_siret": "12345678900010",
                "commune_sk": "sk-75001",
                "lieu_commune_insee_code": "75001",
                "rome_code": "A1203",
                "postes_nombre": 2,
                "intitule_poste": "Ouvrier agricole",
                "description_poste": "Travail de la terre",
                "profil_recherche": "Motivation",
                "contrat_type": "CDD insertion",
            },
        ]
    )

    df_siae_data = pd.DataFrame(
        [
            {
                "siae_structure_siret_code": "12345678900010",
                "siae_type_code": "ACI",
                "siae_structure_label": "Ferme Solidaire",
            },
            {
                "siae_structure_siret_code": "98765432100020",
                "siae_type_code": "GEIQ",
                "siae_structure_label": "GEIQ Accueil",
            },
        ]
    )

    df_geo_data = pd.DataFrame(
        [
            {"commune_sk": "sk-75001", "commune_insee_code": "75001"},
            {"commune_sk": "sk-13054", "commune_insee_code": "13054"},
            {"commune_sk": "sk-97101", "commune_insee_code": "97101"},
        ]
    )

    def mock_fetch(table, ttl_days=None):
        if table == "fact_siae_poste":
            return df_postes_data
        elif table == "dim_siae_structure":
            return df_siae_data
        elif table == "dim_geo":
            return df_geo_data
        return pd.DataFrame()

    mock_client.fetch_table.side_effect = mock_fetch

    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    with (
        patch("pipeline.ingest.context.OUTPUT_DIR", out_dir),
        patch("pipeline.ingest.INCLUSION_OUTPUT_PATH", out_dir / "odis_inclusion_jobs.parquet"),
        patch("pipeline.ingest.INCLUSION_COVERAGE_SHARED_OUTPUT_PATH", out_dir / "odis_inclusion_jobs_coverage.parquet"),
    ):
        config = {
            "sources": {
                "inclusion_jobs": {
                    "use_odace": True,
                    "odace_table": "fact_siae_poste",
                    "ttl_days": 7,
                }
            }
        }

        clean_inclusion_jobs(config, MagicMock(spec=PipelineLogger))

        jobs_file = out_dir / "odis_inclusion_jobs.parquet"
        struct_file = out_dir / "odis_inclusion_structures.parquet"
        coverage_file = out_dir / "odis_inclusion_jobs_coverage.parquet"

        assert jobs_file.exists()
        assert struct_file.exists()
        assert coverage_file.exists()

        df_jobs = pd.read_parquet(jobs_file)
        # Exactly 2 unique metropolitan posts (duplicate removed, DOM-TOM removed)
        assert len(df_jobs) == 2
        assert set(df_jobs["job_id"]) == {101, 102}
        assert df_jobs["job_id"].is_unique

        # Check resolution via commune_sk for post 102
        post_102 = df_jobs[df_jobs["job_id"] == 102].iloc[0]
        assert post_102["codgeo"] == "13054"
        assert post_102["siae_type"] == "GEIQ"
        assert post_102["siae_name"] == "GEIQ Accueil"
        assert post_102["intitule_poste"] == "Agent d'accueil"

        # Check coverage
        df_cov = pd.read_parquet(coverage_file)
        assert len(df_cov) == 96
        assert (df_cov["status"] == "success").all()
        assert df_cov.loc[df_cov["department"] == "75", "offers_count"].iloc[0] == 1
        assert df_cov.loc[df_cov["department"] == "13", "offers_count"].iloc[0] == 1


@patch("pipeline.ingest.get_odace_client")
def test_clean_inclusion_jobs_odace_failure(mock_get_client, tmp_path):
    """Verifies clean_inclusion_jobs raises PipelineRunError on ODAS failure."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("ODAS network timeout")

    config = {
        "sources": {
            "inclusion_jobs": {
                "use_odace": True,
                "odace_table": "fact_siae_poste",
                "ttl_days": 7,
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace inclusion_jobs did not produce a usable dataset"):
        clean_inclusion_jobs(config, MagicMock(spec=PipelineLogger))


@patch("pipeline.ingest.cleaners.salesforce.get_odace_client")
def test_clean_salesforce_jaccueille_odace_success(mock_get_client, tmp_path, monkeypatch):
    """Verifies clean_salesforce_jaccueille correctly fetches and formats fact_jaccueille_code_postal."""
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pipeline.ingest.context.CLEAN_DIR", clean_dir)

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    sample_odas = pd.DataFrame(
        [
            {
                "jaccueille_code_postal_sk": "sk1",
                "commune_sk": "c1",
                "code_postal": "75015",
                "nb_communes": 1,
                "nb_prospects": 4,
                "nb_contacts": 6,
                "nb_total": 10,
            },
            {
                "jaccueille_code_postal_sk": "sk2",
                "commune_sk": None,
                "code_postal": "01000",
                "nb_communes": 2,
                "nb_prospects": 1,
                "nb_contacts": 2,
                "nb_total": 3,
            },
        ]
    )
    mock_client.fetch_table.return_value = sample_odas

    config = {
        "sources": {
            "salesforce_jaccueille": {
                "use_odace": True,
                "odace_table": "fact_jaccueille_code_postal",
                "ttl_days": 7,
            }
        }
    }

    logger = MagicMock(spec=PipelineLogger)
    clean_salesforce_jaccueille(config, logger)

    out_file = clean_dir / "salesforce_jaccueille.parquet"
    assert out_file.exists()

    df = pd.read_parquet(out_file)
    assert len(df) == 2
    assert list(df.columns) == [
        "code_postal",
        "lead_count",
        "contact_count",
        "total_jaccueille_count",
    ]
    # Check sorted by code_postal
    assert df.iloc[0]["code_postal"] == "01000"
    assert df.iloc[0]["lead_count"] == 1
    assert df.iloc[0]["contact_count"] == 2
    assert df.iloc[0]["total_jaccueille_count"] == 3

    assert df.iloc[1]["code_postal"] == "75015"
    assert df.iloc[1]["lead_count"] == 4
    assert df.iloc[1]["contact_count"] == 6
    assert df.iloc[1]["total_jaccueille_count"] == 10


@patch("pipeline.ingest.cleaners.salesforce.get_odace_client")
def test_clean_salesforce_jaccueille_odace_failure(mock_get_client, tmp_path, monkeypatch):
    """Verifies clean_salesforce_jaccueille raises PipelineRunError on failure."""
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pipeline.ingest.context.CLEAN_DIR", clean_dir)

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("ODAS API error")

    config = {
        "sources": {
            "salesforce_jaccueille": {
                "use_odace": True,
                "odace_table": "fact_jaccueille_code_postal",
                "ttl_days": 7,
            }
        }
    }

    with pytest.raises(PipelineRunError, match="ODAS Salesforce J'accueille cleaner failed"):
        clean_salesforce_jaccueille(config, MagicMock(spec=PipelineLogger))


@patch("pipeline.ingest.get_odace_client")
def test_clean_live_jobs_odace_success(mock_get_client, tmp_path, monkeypatch):
    """Verifies clean_live_jobs correctly aggregates fact_offre_emploi into odis_ft_jobs_agg & coverage."""
    import pipeline.ingest as ingest

    output_dir = tmp_path / "output"
    clean_dir = tmp_path / "clean"
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ingest, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr("pipeline.ingest.context.CLEAN_DIR", clean_dir)
    monkeypatch.setattr(ingest, "FT_SHARED_OUTPUT_PATH", output_dir / "odis_ft_jobs_agg.parquet")
    monkeypatch.setattr(ingest, "FT_COVERAGE_SHARED_OUTPUT_PATH", output_dir / "odis_ft_jobs_coverage.parquet")

    # Create mock clean rome referential
    pd.DataFrame(
        [
            {"code": "M1213", "label": "Comptable"},
            {"code": "A1208", "label": "Jardinier"},
        ]
    ).to_parquet(clean_dir / "rome.parquet")

    sample_offers = pd.DataFrame(
        [
            {
                "offre_emploi_sk": "sk1",
                "commune_insee_code": "75056",
                "rome_code": "M1213",
                "postes_nombre": 2,
                "is_offre_manque_candidats": True,
            },
            {
                "offre_emploi_sk": "sk2",
                "commune_insee_code": "75056",
                "rome_code": "M1213",
                "postes_nombre": 1,
                "is_offre_manque_candidats": False,
            },
            {
                "offre_emploi_sk": "sk3",
                "commune_insee_code": "13055",
                "rome_code": "A1208",
                "postes_nombre": 1,
                "is_offre_manque_candidats": False,
            },
            # DOM-TOM offer: should be excluded by metropolitan filter
            {
                "offre_emploi_sk": "sk4",
                "commune_insee_code": "97401",
                "rome_code": "A1208",
                "postes_nombre": 5,
                "is_offre_manque_candidats": True,
            },
            # Null commune INSEE offer: should be excluded
            {
                "offre_emploi_sk": "sk5",
                "commune_insee_code": None,
                "rome_code": "M1213",
                "postes_nombre": 1,
                "is_offre_manque_candidats": False,
            },
        ]
    )

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.return_value = sample_offers

    config = {
        "sources": {
            "france_travail_live": {
                "use_odace": True,
                "odace_table": "fact_offre_emploi",
                "ttl_days": 7,
            }
        }
    }

    logger = MagicMock(spec=PipelineLogger)
    clean_live_jobs(config, logger)

    jobs_file = output_dir / "odis_ft_jobs_agg.parquet"
    cov_file = output_dir / "odis_ft_jobs_coverage.parquet"

    assert jobs_file.exists()
    assert cov_file.exists()

    df_jobs = pd.read_parquet(jobs_file)
    assert len(df_jobs) == 2
    assert list(df_jobs.columns) == [
        "commune",
        "romeCode",
        "romeLibelle",
        "nb_offres",
        "total_postes",
        "nb_offres_tension",
    ]

    row_13 = df_jobs[df_jobs["commune"] == "13055"].iloc[0]
    assert row_13["romeCode"] == "A1208"
    assert row_13["romeLibelle"] == "Jardinier"
    assert row_13["nb_offres"] == 1
    assert row_13["total_postes"] == 1
    assert row_13["nb_offres_tension"] == 0

    row_75 = df_jobs[df_jobs["commune"] == "75056"].iloc[0]
    assert row_75["romeCode"] == "M1213"
    assert row_75["romeLibelle"] == "Comptable"
    assert row_75["nb_offres"] == 2
    assert row_75["total_postes"] == 3
    assert row_75["nb_offres_tension"] == 1

    df_cov = pd.read_parquet(cov_file)
    assert len(df_cov) == 96
    assert (df_cov["status"] == "success").all()
    assert df_cov.loc[df_cov["department"] == "75", "offers_count"].iloc[0] == 2
    assert df_cov.loc[df_cov["department"] == "13", "offers_count"].iloc[0] == 1


@patch("pipeline.ingest.get_odace_client")
def test_clean_live_jobs_odace_failure(mock_get_client, tmp_path):
    """Verifies clean_live_jobs raises PipelineRunError on ODAS failure."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.fetch_table.side_effect = Exception("ODAS network timeout")

    config = {
        "sources": {
            "france_travail_live": {
                "use_odace": True,
                "odace_table": "fact_offre_emploi",
                "ttl_days": 7,
            }
        }
    }

    with pytest.raises(PipelineRunError, match="Odace france_travail_live did not produce a usable dataset"):
        clean_live_jobs(config, MagicMock(spec=PipelineLogger))




