import pytest
from unittest.mock import patch, MagicMock
from app.services.mcp_france_travail import search_job_offers_logic

@patch('app.services.mcp_france_travail.requests.get')
@patch('app.services.mcp_france_travail._get_access_token')
def test_search_job_offers_params(mock_token, mock_get):
    mock_token.return_value = "fake_token"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"resultats": [], "total": 0}
    mock_response.headers = {"Content-Range": "offres 0-0/0"}
    mock_get.return_value = mock_response

    # Test with ROME code
    search_job_offers_logic(rome_code="M1805", location="33063")
    
    args, kwargs = mock_get.call_args
    params = kwargs.get('params')
    
    assert "codeROME" in params
    assert params["codeROME"] == "M1805"
    assert "codeRome" not in params

@patch('app.services.mcp_france_travail.requests.get')
@patch('app.services.mcp_france_travail._get_access_token')
def test_search_job_offers_appellation_params(mock_token, mock_get):
    mock_token.return_value = "fake_token"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"resultats": [], "total": 0}
    mock_response.headers = {"Content-Range": "offres 0-0/0"}
    mock_get.return_value = mock_response

    # Test with Appellation code (numeric)
    search_job_offers_logic(appellation_codes=["12345"], location="33063")
    
    args, kwargs = mock_get.call_args
    params = kwargs.get('params')
    
    assert "appellation" in params
    assert params["appellation"] == "12345"
    assert "codeROME" not in params

@patch('app.services.mcp_france_travail.requests.get')
@patch('app.services.mcp_france_travail._get_access_token')
def test_search_job_offers_mixed_params(mock_token, mock_get):
    mock_token.return_value = "fake_token"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"resultats": [], "total": 0}
    mock_response.headers = {"Content-Range": "offres 0-0/0"}
    mock_get.return_value = mock_response

    # Test with Mixed ROME and Appellation
    search_job_offers_logic(appellation_codes=["M1805", "12345"], location="33063")
    
    args, kwargs = mock_get.call_args
    params = kwargs.get('params')
    
    assert "codeROME" in params
    assert params["codeROME"] == "M1805"
    assert "appellation" in params
    assert params["appellation"] == "12345"
