import os
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from services.mcp_inclusion import (
    _search_inclusion_jobs_logic,
    _get_inclusion_job_details_logic
)
from pipeline.emplois_inclusion_ingest import fetch_department_jobs

@pytest.fixture
def mock_parquet_data():
    """Mock database of inclusion jobs for testing fallback lookups."""
    data = {
        "job_id": [12345],
        "codgeo": ["33063"], # Bordeaux (Dept 33)
        "siae_siret": ["40231751500037"],
        "siae_type": ["ETTI"],
        "siae_name": ["Mock SIAE"],
        "rome": ["A1203"],
        "postes": [1]
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
    with patch("pandas.read_parquet", return_value=mock_parquet_data), \
         patch("os.path.exists", side_effect=lambda p: True if "odis_inclusion_jobs.parquet" in str(p) else False), \
         patch("requests.get") as mock_get:
         
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
                    "postes": []
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
    with patch("pandas.read_parquet", return_value=mock_parquet_data), \
         patch("os.path.exists", side_effect=lambda p: True if "odis_inclusion_jobs.parquet" in str(p) else False), \
         patch("requests.get") as mock_get:
         
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

@patch("time.sleep")
@patch("requests.get")
def test_fetch_department_jobs_429_backoff(mock_get, mock_sleep):
    """Verify that fetch_department_jobs handles 429 with exponential backoff and retry using Retry-After."""
    # First response is a 429 with Retry-After header
    mock_response_429 = MagicMock()
    mock_response_429.status_code = 429
    mock_response_429.headers = {"Retry-After": "3"}
    
    # Second response is a 200 with data
    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    mock_response_200.json.return_value = {"results": [], "next": None}
    
    # Configure mock_get to return 429 then 200
    mock_get.side_effect = [mock_response_429, mock_response_200]
    
    results = fetch_department_jobs("33")
    
    assert results == []
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(3.0)

@patch("time.sleep")
@patch("requests.get")
def test_fetch_department_jobs_429_backoff_default(mock_get, mock_sleep):
    """Verify that fetch_department_jobs handles 429 using default backoff when Retry-After is absent."""
    # First response is a 429 without Retry-After
    mock_response_429 = MagicMock()
    mock_response_429.status_code = 429
    mock_response_429.headers = {}
    
    # Second response is a 200
    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    mock_response_200.json.return_value = {"results": [], "next": None}
    
    mock_get.side_effect = [mock_response_429, mock_response_200]
    
    fetch_department_jobs("33")
    
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(5.0) # default starting backoff
