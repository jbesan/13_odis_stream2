import pytest
import time
from unittest.mock import patch, MagicMock
import pandas as pd
from core.models import InclusionServiceDetail, InclusionMetrics
from agents.utils import get_odis_bg_store
from core.postscoring import launch_background_inclusion_enrichment


@pytest.fixture(autouse=True)
def clean_bg_store():
    """Cleans up the background store cache before and after each test."""
    store = get_odis_bg_store()
    store.clear()
    yield
    store.clear()


def test_inclusion_service_detail_model_validation():
    """Validates that InclusionServiceDetail instantiates and validates correctly."""
    srv_data = {
        "id": "srv123",
        "name": "Service d'Accompagnement",
        "description": "Description du service.",
        "lien_source": "https://soliguide.fr/fiche/srv123",
        "source": "soliguide",
    }

    srv = InclusionServiceDetail.model_validate(srv_data)
    assert srv.id == "srv123"
    assert srv.name == "Service d'Accompagnement"
    assert srv.source == "soliguide"
    assert srv.lien_source == "https://soliguide.fr/fiche/srv123"


def test_inclusion_metrics_detailed_services():
    """Validates that InclusionMetrics can house services_detailed."""
    srv = InclusionServiceDetail(
        id="srv1",
        name="Service A",
        description="Desc A",
        lien_source="https://example.com",
        source="dora",
    )

    metrics = InclusionMetrics(cat_score=0.75, services_detailed={"Logement": [srv]})

    assert "Logement" in metrics.services_detailed
    assert metrics.services_detailed["Logement"][0].id == "srv" + "1"


@patch("requests.get")
@patch("os.getenv")
def test_background_inclusion_enrichment_success(mock_getenv, mock_get):
    """Verifies that background inclusion enrichment queries the API and parses results successfully."""
    mock_getenv.return_value = "fake_api_key"

    # Mock index for thematic mapping
    mock_engine = MagicMock()
    mock_engine.inclusion_services_index = pd.DataFrame(
        [
            {
                "code": "lecture-ecriture-calcul--maitriser-le-francais",
                "label": "Maitriser le français",
            }
        ]
    ).set_index("code")

    mock_items = [
        {
            "service": {
                "id": "srv_dora_1",
                "nom": "Apprendre le français",
                "structure_id": "struct-alpha",
                "description": "Cours pour débutants.",
                "lien_source": "https://dora.fr/srv1",
                "source": "dora",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-alpha",
                    "nom": "Centre Social Alpha",
                }
            }
        }
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"items": mock_items}
    mock_get.return_value = mock_response

    codgeos = ["94041"]
    hash_val = "e2e_inclusion_hash"

    # Act
    launch_background_inclusion_enrichment(mock_engine, codgeos, hash_val)

    # Wait for completion (with timeout)
    store = get_odis_bg_store()
    timeout = 2.0
    start = time.time()
    while time.time() - start < timeout:
        if hash_val in store and "inclusion_services_enrichment" in store[hash_val]:
            break
        time.sleep(0.1)

    # Assert
    assert hash_val in store
    data = store[hash_val]["inclusion_services_enrichment"]
    assert "94041" in data
    assert "Maitriser le français" in data["94041"]
    services = data["94041"]["Maitriser le français"]
    assert len(services) == 1
    assert services[0]["id"] == "srv_dora_1"
    assert services[0]["name"] == "Apprendre le français"
    assert services[0]["nom_structure"] == "Centre Social Alpha"
    assert services[0]["lien_source"] == "https://dora.fr/srv1"


@patch("requests.get")
@patch("os.getenv")
def test_background_inclusion_enrichment_api_error(mock_getenv, mock_get):
    """Verifies that background inclusion enrichment handles API errors gracefully without crashing."""
    mock_getenv.return_value = "fake_api_key"

    mock_engine = MagicMock()
    mock_engine.inclusion_services_index = pd.DataFrame()

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_get.return_value = mock_response

    codgeos = ["94041"]
    hash_val = "e2e_inclusion_error_hash"

    # Act
    launch_background_inclusion_enrichment(mock_engine, codgeos, hash_val)

    # Wait for task to finish
    time.sleep(0.5)

    # Assert (store should have empty results or ignore failing codgeo)
    store = get_odis_bg_store()
    assert hash_val in store
    assert store[hash_val]["inclusion_services_enrichment"] == {}
