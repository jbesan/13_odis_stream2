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
    mock_items = [
        {
            "id": "srv1",
            "nom": "Cours de français A1",
            "structure_id": "struct-alpha",
            "description": "Apprentissage du français langue étrangère.",
            "lien_source": "https://example.com/srv1",
            "source": "dora",
            "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
        },
        {
            "id": "srv2",
            "nom": "Cours de français B2",
            "structure_id": "struct-beta",
            "description": "Niveau avancé.",
            "lien_source": "https://example.com/srv2",
            "source": "soliguide",
            "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
        },
        # Duplicate: same structure_id + same nom as srv1 → should be deduplicated
        {
            "id": "srv3-duplicate",
            "nom": "Cours de français A1",
            "structure_id": "struct-alpha",
            "description": "Version doublon du même service.",
            "lien_source": "https://example.com/srv3",
            "source": "dora",
            "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
        },
    ]

    # Mock requests.get and os.getenv
    with patch("requests.get") as mock_get, patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = "fake_api_key"

        mock_services_response = MagicMock()
        mock_services_response.status_code = 200
        mock_services_response.json.return_value = {"items": mock_items}

        mock_structures_response = MagicMock()
        mock_structures_response.status_code = 200
        mock_structures_response.json.return_value = {
            "items": [
                {"id": "struct-alpha", "nom": "Centre Social Alpha"},
                {"id": "struct-beta", "nom": "CCAS Beta"},
            ]
        }

        # First call → services, second → structures
        mock_get.side_effect = [mock_services_response, mock_structures_response]

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

        # Verify 2 GET calls were made (services + structures)
        assert mock_get.call_count == 2
