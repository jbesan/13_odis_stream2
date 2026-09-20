"""Regression coverage for publishing enrichment data before enabling actions."""

from typing import Any

import pytest
from pydantic import ValidationError

from core.models import CommuneResult, SearchResultsData
from core.postscoring import sync_commune_data
from ui import results_actions


def terminal_payload(code: str) -> dict[str, Any]:
    """Build completed provider results containing a real job payload.

    Args:
        code: Commune receiving the result.

    Returns:
        Provider results with successful jobs and unavailable other providers.
    """
    return {
        "jobs_enrichment": {
            code: {
                "status": "success_nonempty",
                "jobs": [
                    [{"id": "job-1", "title": "Boulanger", "contract_type": "CDI"}]
                ],
                "total": 1,
            }
        },
        "association_enrichment_status": {code: {"status": "error"}},
        "inclusion_services_status": {code: {"status": "timeout"}},
    }


@pytest.mark.parametrize(
    "action", ["render_export_pdf_button", "render_share_search_button"]
)
def test_action_publishes_data_before_opening_modal(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """A fragment click must expose copied results to the export/share modal."""
    city = CommuneResult(codgeo="33063", name="Bordeaux")
    results = SearchResultsData(search_hash="publication", results=[city])
    payload = terminal_payload(city.codgeo)
    monkeypatch.setattr(
        results_actions.st, "session_state", {"search_results": results}
    )
    monkeypatch.setattr(results_actions, "odis_get_bg_result", lambda _: payload)

    def click(label: str, **kwargs: Any) -> bool:
        """Assert that data is ready when the button becomes clickable."""
        assert kwargs["disabled"] is False
        assert city.commune_results_hydrated
        assert city.employment.matching_job_offers[0][0].id == "job-1"
        return True

    reruns = []
    monkeypatch.setattr(results_actions.st, "button", click)
    monkeypatch.setattr(results_actions.st, "rerun", lambda **kwargs: reruns.append(kwargs))
    function = getattr(results_actions, action)
    getattr(function, "__wrapped__", function)(h="publication")
    expected_state_key = (
        "active_pdf_modal"
        if action == "render_export_pdf_button"
        else "active_share_dialog"
    )
    assert results_actions.st.session_state[expected_state_key] is True
    assert reruns == [{"scope": "app"}]
    assert (
        results.model_dump()["results"][0]["employment"]["matching_job_offers"][0][0]["id"]
        == "job-1"
    )


def test_validation_failure_does_not_publish_hydrated_flag() -> None:
    """Malformed provider data must fail visibly without enabling actions."""
    city = CommuneResult(codgeo="33063", name="Bordeaux")
    payload = terminal_payload(city.codgeo)
    payload["jobs_enrichment"][city.codgeo]["jobs"] = [[{"id": "invalid"}]]
    with pytest.raises(ValidationError):
        sync_commune_data(city, payload)
    assert city.commune_results_hydrated is False


@pytest.mark.parametrize("status", ["pending", "running", None])
def test_unfinished_provider_does_not_publish_hydrated_flag(status: str | None) -> None:
    """Available jobs can be copied while another provider is unfinished."""
    city = CommuneResult(codgeo="33063", name="Bordeaux")
    payload = terminal_payload(city.codgeo)
    payload["association_enrichment_status"][city.codgeo]["status"] = status
    sync_commune_data(city, payload)
    assert city.employment.matching_job_offers[0][0].id == "job-1"
    assert city.commune_results_hydrated is False
    payload["association_enrichment_status"][city.codgeo]["status"] = "success_empty"
    sync_commune_data(city, payload)
    assert city.commune_results_hydrated is True


def test_missing_provider_status_does_not_publish_hydrated_flag() -> None:
    """A legacy entry cannot turn absent provider state into a success."""
    city = CommuneResult(codgeo="33063", name="Bordeaux")
    payload = terminal_payload(city.codgeo)
    payload.pop("inclusion_services_status")
    sync_commune_data(city, payload)
    assert city.employment.matching_job_offers[0][0].id == "job-1"
    assert city.commune_results_hydrated is False
