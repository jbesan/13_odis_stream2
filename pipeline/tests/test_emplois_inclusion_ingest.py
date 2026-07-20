import pytest
from unittest.mock import patch, MagicMock
from pipeline.emplois_inclusion_ingest import fetch_department_jobs


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
    mock_sleep.assert_called_once_with(5.0)  # default starting backoff
