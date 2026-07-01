import pytest
from unittest.mock import patch, MagicMock
from services.mcp_france_travail import (
    _search_job_offers_logic,
    _get_job_details_logic,
    TOKEN_CACHE,
)
import time
import os


@pytest.fixture(autouse=True)
def reset_token_cache():
    TOKEN_CACHE["access_token"] = None
    TOKEN_CACHE["expires_at"] = 0
    with patch.dict(
        os.environ,
        {
            "FRANCE_TRAVAIL_CLIENT_ID": "test_id",
            "FRANCE_TRAVAIL_CLIENT_SECRET": "test_secret",
        },
    ):
        yield


def test_get_access_token_success():
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "fake_token",
            "expires_in": "3600",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        from services.mcp_france_travail import _get_access_token

        token = _get_access_token()
        assert token == "fake_token"
        assert TOKEN_CACHE["access_token"] == "fake_token"
        assert TOKEN_CACHE["expires_at"] > time.time()


def test_get_access_token_cache():
    TOKEN_CACHE["access_token"] = "cached_token"
    TOKEN_CACHE["expires_at"] = time.time() + 1000

    from services.mcp_france_travail import _get_access_token

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

        results = _search_job_offers_logic(query="Dev", location="33063")
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

        results = _search_job_offers_logic(query="Unknown")
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
