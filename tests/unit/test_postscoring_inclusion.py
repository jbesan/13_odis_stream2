import time
import pandas as pd
from unittest.mock import MagicMock, patch
from agents.utils import get_odis_bg_store
from core.postscoring import launch_background_inclusion_enrichment


def test_launch_background_inclusion_enrichment():
    """Tests that launch_background_inclusion_enrichment correctly calls the Data Inclusion API

    and updates the background store with the grouped services.
    """
    # 1. Setup Mock Engine & Index
    mock_engine = MagicMock()
    mock_engine.inclusion_services_index = pd.DataFrame(
        [
            {
                "code": "lecture-ecriture-calcul--maitriser-le-francais",
                "label": "Maitriser le français",
            }
        ]
    ).set_index("code")

    # Mocked API response items — two different services with same (structure_id, nom) = duplicate
    # Wrapped inside "service" structure like the real /search/services response
    mock_items = [
        {
            "service": {
                "id": "srv1",
                "nom": "Cours de français A1",
                "structure_id": "struct-alpha",
                "description": "Apprentissage du français langue étrangère.",
                "lien_source": "https://example.com/srv1",
                "source": "dora",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-alpha",
                    "nom": "Centre Social Alpha",
                }
            }
        },
        {
            "service": {
                "id": "srv2",
                "nom": "Cours de français B2",
                "structure_id": "struct-beta",
                "description": "Niveau avancé.",
                "lien_source": "https://example.com/srv2",
                "source": "soliguide",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-beta",
                    "nom": "CCAS Beta",
                }
            }
        },
        # Duplicate: same structure_id + same nom as srv1 → should be deduplicated
        {
            "service": {
                "id": "srv3-duplicate",
                "nom": "Cours de français A1",
                "structure_id": "struct-alpha",
                "description": "Version doublon du même service.",
                "lien_source": "https://example.com/srv3",
                "source": "dora",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-alpha",
                    "nom": "Centre Social Alpha",
                }
            }
        },
    ]

    # Mock requests.get and os.getenv
    with patch("requests.get") as mock_get, patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = "fake_api_key"

        mock_services_response = MagicMock()
        mock_services_response.status_code = 200
        mock_services_response.json.return_value = {"items": mock_items}
        mock_get.return_value = mock_services_response

        # 2. Parameters
        codgeos = ["94041"]
        hash_val = "test_inclusion_hash"

        # Clean store before test
        store = get_odis_bg_store()
        if hash_val in store:
            del store[hash_val]

        # 3. Act
        launch_background_inclusion_enrichment(mock_engine, codgeos, hash_val)

        # 4. Wait for background thread (with timeout)
        timeout = 2.0
        start = time.time()
        while time.time() - start < timeout:
            if hash_val in store and "inclusion_services_enrichment" in store[hash_val]:
                break
            time.sleep(0.1)

        # 5. Assertions
        assert hash_val in store
        enrichment_data = store[hash_val]["inclusion_services_enrichment"]
        assert "94041" in enrichment_data

        city_data = enrichment_data["94041"]
        assert "Maitriser le français" in city_data
        services = city_data["Maitriser le français"]

        # srv3 is a duplicate of srv1 (same structure_id + nom) → only 2 unique services
        assert len(services) == 2

        assert services[0]["id"] == "srv1"
        assert services[0]["name"] == "Cours de français A1"
        assert services[0]["nom_structure"] == "Centre Social Alpha"
        assert services[0]["structure_id"] == "struct-alpha"
        assert (
            services[0]["description"] == "Apprentissage du français langue étrangère."
        )
        assert services[0]["lien_source"] == "https://example.com/srv1"
        assert services[0]["source"] == "dora"

        assert services[1]["id"] == "srv2"
        assert services[1]["name"] == "Cours de français B2"
        assert services[1]["nom_structure"] == "CCAS Beta"
        assert services[1]["description"] == "Niveau avancé."
        assert services[1]["lien_source"] == "https://example.com/srv2"
        assert services[1]["source"] == "soliguide"

        # Verify only 1 GET call was made
        assert mock_get.call_count == 1
        mock_get.assert_called_once_with(
            "https://api.data.inclusion.gouv.fr/api/v1/search/services",
            headers={"Authorization": "Bearer fake_api_key"},
            params={"code_commune": "94041", "size": 100},
            timeout=10,
        )


def test_launch_background_inclusion_enrichment_missing_api_key():
    """Tests that launch_background_inclusion_enrichment returns early if the API key is missing."""
    mock_engine = MagicMock()
    with patch("os.getenv") as mock_getenv, patch("requests.get") as mock_get:
        mock_getenv.return_value = None

        hash_val = "test_missing_key_hash"
        store = get_odis_bg_store()
        if hash_val in store:
            del store[hash_val]

        launch_background_inclusion_enrichment(mock_engine, ["94041"], hash_val)
        time.sleep(0.2)

        # The store should not have been updated
        assert hash_val not in store
        assert mock_get.call_count == 0
