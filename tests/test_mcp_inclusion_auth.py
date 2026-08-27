import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from services.mcp_inclusion import (
    _search_inclusion_jobs_logic,
    _get_inclusion_job_details_logic,
)


@pytest.fixture
def mock_parquet_data():
    """Mock database of inclusion jobs for testing fallback lookups."""
    data = {
        "job_id": [12345],
        "codgeo": ["33063"],  # Bordeaux (Dept 33)
        "siae_siret": ["40231751500037"],
        "siae_type": ["ETTI"],
        "siae_name": ["Mock SIAE"],
        "rome": ["A1203"],
        "postes": [1],
    }
    return pd.DataFrame(data)


def test_search_inclusion_jobs_public_no_auth():
    """Verify that _search_inclusion_jobs_logic calls API with Accept but no Authorization header."""
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}
        mock_get.return_value = mock_response

        res = _search_inclusion_jobs_logic(location="33063", rome="A1203")

        assert "offres" in res
        assert res["total"] == 0
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        # Headers should not contain Authorization
        headers = kwargs.get("headers", {})
        assert "Authorization" not in headers
        assert headers.get("Accept") == "application/json"

        # Params should include code_insee and distance_max_km
        params = kwargs.get("params", {})
        assert params.get("code_insee") == "33063"
        assert params.get("distance_max_km") == 20


def test_get_inclusion_job_details_fallback_siret(mock_parquet_data):
    """Verify that details lookup for a SIRET resolves department via parquet and queries public API."""
    with (
        patch(
            "services.mcp_inclusion.load_parquet_dataset",
            return_value=mock_parquet_data,
        ),
        patch("requests.get") as mock_get,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "id": "mock-uuid",
                    "enseigne": "Mock SIAE",
                    "type": "ETTI",
                    "siret": "40231751500037",
                    "description": "Mocked live details",
                    "postes": [],
                }
            ]
        }
        mock_get.return_value = mock_response

        # Call with the SIRET from parquet
        res = _get_inclusion_job_details_logic("40231751500037")

        assert res["siret"] == "40231751500037"
        assert res["name"] == "Mock SIAE"
        assert res["description"] == "Mocked live details"

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        # Should query department 33 (from codgeo 33063)
        params = kwargs.get("params", {})
        assert params.get("postes_dans_le_departement") == "33"
        assert "Authorization" not in kwargs.get("headers", {})


def test_get_inclusion_job_details_not_found_returns_cache_stub(mock_parquet_data):
    """Verify that if live public query fails to find the structure, we fall back to a clean cache stub."""
    with (
        patch(
            "services.mcp_inclusion.load_parquet_dataset",
            return_value=mock_parquet_data,
        ),
        patch("requests.get") as mock_get,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        # API doesn't return the matching siret
        mock_response.json.return_value = {"results": []}
        mock_get.return_value = mock_response

        # Call with SIRET
        res = _get_inclusion_job_details_logic("40231751500037")

        # Must return fallback stub filled from mock_parquet_data
        assert res["siret"] == "40231751500037"
        assert res["name"] == "Mock SIAE"
        assert "indisponibles en direct" in res["description"]
        assert len(res["postes"]) == 1
        assert res["postes"][0]["rome"] == "A1203"


def test_prune_inclusion_structure_and_job_truncation():
    """Verify that structure descriptions and individual job descriptions/profils are truncated to 500 chars."""
    from services.mcp_inclusion import _prune_inclusion_structure, _prune_inclusion_job

    long_text = "A" * 600
    raw_siae = {
        "id": "siae-1",
        "enseigne": "EST EMPLOI",
        "type": "ETTI",
        "siret": "40292803000046",
        "description": long_text,
        "postes": [
            {
                "id": 124525,
                "rome": "Carreleur / Carreleuse (F1608)",
                "appellation_modifiee": "Ouvrier(e) Polyvalent(e)",
                "type_contrat": "Contrat de mission intérimaire",
                "nombre_postes_ouverts": 1,
                "lieu": {
                    "nom": "Saint-Priest",
                    "departement": "69",
                    "code_postaux": ["69800"],
                    "code_insee": "69290",
                },
                "description": long_text,
                "profil_recherche": long_text,
                "recrutement_ouvert": "True",
                "extra_field_to_prune": "should_not_exist",
            }
        ],
    }

    pruned_single_job = _prune_inclusion_job(raw_siae["postes"][0])
    assert pruned_single_job["id"] == 124525
    assert len(pruned_single_job["description"]) == 503

    pruned = _prune_inclusion_structure(raw_siae)

    # Structure description truncated to 500 + "..." (503 chars)
    assert len(pruned["description"]) == 503
    assert pruned["description"].endswith("...")

    # Postes pruned
    assert len(pruned["postes"]) == 1
    job = pruned["postes"][0]
    assert job["id"] == 124525
    assert job["rome"] == "Carreleur / Carreleuse (F1608)"
    assert len(job["description"]) == 503
    assert job["description"].endswith("...")
    assert len(job["profil_recherche"]) == 503
    assert job["profil_recherche"].endswith("...")
    assert "extra_field_to_prune" not in job
    assert job["lieu"]["nom"] == "Saint-Priest"
    assert job["lieu"]["departement"] == "69"
    assert job["lieu"]["code_insee"] == "69290"

