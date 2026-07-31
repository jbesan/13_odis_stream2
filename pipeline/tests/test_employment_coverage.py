from unittest.mock import MagicMock, patch

import pytest

from pipeline.employment_coverage import METROPOLITAN_DEPARTMENTS
from pipeline.ft_live_ingest import fetch_all_pages
from pipeline.emplois_inclusion_ingest import fetch_department_jobs_with_coverage


def test_metropolitan_scope_has_the_96_real_department_identifiers():
    assert len(METROPOLITAN_DEPARTMENTS) == 96
    assert "20" not in METROPOLITAN_DEPARTMENTS
    assert {"2A", "2B"}.issubset(METROPOLITAN_DEPARTMENTS)


@patch("pipeline.ft_live_ingest.api_call")
def test_france_travail_page_failure_is_not_an_empty_result(mock_api_call):
    first_page = MagicMock()
    first_page.status_code = 206
    first_page.headers = {"Content-Range": "offres 0-149/151"}
    first_page.json.return_value = {"resultats": []}
    mock_api_call.side_effect = [first_page, None]

    with pytest.raises(RuntimeError, match="Could not fetch batch"):
        fetch_all_pages({"departement": "33"})


@patch("pipeline.emplois_inclusion_ingest.requests.get")
def test_inclusion_department_failure_has_explicit_coverage_status(mock_get):
    mock_get.side_effect = RuntimeError("provider unavailable")

    result = fetch_department_jobs_with_coverage("33")

    assert result.status == "failed"
    assert result.jobs == []
    assert result.error == "provider unavailable"
