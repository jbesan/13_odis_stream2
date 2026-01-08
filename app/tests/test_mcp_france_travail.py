import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from mcp_france_travail import (
    _get_access_token, 
    search_job_offers_logic, 
    _get_job_details_logic, 
    _resolve_fap_label,
    _resolve_rome_clusters,
    TOKEN_CACHE
)
import time
import os

@pytest.fixture(autouse=True)
def reset_token_cache():
    TOKEN_CACHE["access_token"] = None
    TOKEN_CACHE["expires_at"] = 0
    with patch.dict(os.environ, {
        "FRANCE_TRAVAIL_CLIENT_ID": "test_id",
        "FRANCE_TRAVAIL_CLIENT_SECRET": "test_secret"
    }):
        yield

def test_get_access_token_success():
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "fake_token",
            "expires_in": "3600"
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        
        token = _get_access_token()
        assert token == "fake_token"
        assert TOKEN_CACHE["access_token"] == "fake_token"
        assert TOKEN_CACHE["expires_at"] > time.time()

def test_get_access_token_cache():
    TOKEN_CACHE["access_token"] = "cached_token"
    TOKEN_CACHE["expires_at"] = time.time() + 1000
    
    token = _get_access_token()
    assert token == "cached_token"

def test_search_job_offers_success():
    TOKEN_CACHE["access_token"] = "valid_token"
    TOKEN_CACHE["expires_at"] = time.time() + 1000
    
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "resultats": [{"id": "1", "intitule": "Dev"}]
        }
        mock_response.headers = {"Content-Range": "offres 0-49/150"}
        mock_get.return_value = mock_response
        
        results = search_job_offers_logic(query="Dev", location="33063")
        assert len(results["offres"]) == 1
        assert results["total"] == 150
        assert results["offres"][0]["intitule"] == "Dev"

def test_search_job_offers_no_results():
    TOKEN_CACHE["access_token"] = "valid_token"
    TOKEN_CACHE["expires_at"] = time.time() + 1000
    
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_get.return_value = mock_response
        
        results = search_job_offers_logic(query="Unknown")
        assert results["offres"] == []
        assert results["total"] == 0

def test_get_job_details_success():
    TOKEN_CACHE["access_token"] = "valid_token"
    TOKEN_CACHE["expires_at"] = time.time() + 1000
    
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "123", "intitule": "Expert"}
        mock_get.return_value = mock_response
        
        details = _get_job_details_logic("123")
        assert details["id"] == "123"
        assert details["intitule"] == "Expert"

def test_resolve_fap_label():
    with patch("pandas.read_parquet") as mock_read:
        mock_df = MagicMock()
        mock_df.__getitem__.side_effect = lambda key: {
            'key': pd.Series(['fap_codes']),
            'code': pd.Series(['D1X33']),
            'label': pd.Series(['Soudeurs'])
        }[key]
        mock_df.empty = False
        mock_df.iloc = [MagicMock(label='Soudeurs')]
        
        # Simpler mock for pandas filtering
        mock_read.return_value = pd.DataFrame({
            'key': ['fap_codes'],
            'code': ['D1X33'],
            'label': ['Soudeurs']
        })
        
        label = _resolve_fap_label("D1X33")
        assert label == "Soudeurs"

def test_resolve_rome_clusters():
    with patch("pandas.read_parquet") as mock_read:
        # Mock mapping for G0B41 (Automobile)
        # Should return I16 (automotive) and I11 (supervision)
        # I16 has more entries in the mock data, so it should be first
        mock_read.return_value = pd.DataFrame({
            'key': ['fap_rome_mapping'] * 3,
            'code': ['G0B41', 'G0B41', 'G0B41'],
            'label': ['I1604', 'I1603', 'I1103']
        })
        
        clusters = _resolve_rome_clusters("G0B41")
        assert clusters == ["I16", "I11"] # I16 (2 entries) before I11 (1 entry)

def test_search_job_offers_with_fap_to_domaine():
    TOKEN_CACHE["access_token"] = "valid_token"
    TOKEN_CACHE["expires_at"] = time.time() + 1000
    
    with patch("mcp_france_travail._resolve_rome_clusters") as mock_resolve:
        mock_resolve.return_value = ["I16", "I11"]
        
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"resultats": []}
            mock_get.return_value = mock_response
            
            search_job_offers_logic(fap_code="G0B41", location="11069")
            
            # Check if 'domaine' parameter is used with the FIRST cluster
            args, kwargs = mock_get.call_args
            assert kwargs['params']['domaine'] == "I16"
            # Mots-clés should NOT contain FAP label by default
            assert 'motsCles' not in kwargs['params']
