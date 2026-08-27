import time
import pandas as pd
from unittest.mock import MagicMock, patch
from agents.utils import get_odis_bg_store
from core.postscoring import launch_background_inclusion_enrichment


def test_launch_background_inclusion_enrichment():
    """Tests that launch_background_inclusion_enrichment correctly calls the Data Inclusion API
    with GPS coordinates from POIs, excludes external CCAS and distant/broad services,
    and updates the background store with sorted local services.
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

    # Setup POIs with mairie for 94041
    mock_engine.pois = pd.DataFrame(
        [
            {
                "category": "mairie",
                "codgeo": "94041",
                "name": "Mairie de Test",
                "lat": 48.8000,
                "lon": 2.4500,
            }
        ]
    )

    # Mocked API response items:
    # 1. Local service (dist 1km, code_insee 94041) -> Keep
    # 2. Local CCAS (dist 0km, code_insee 94041) -> Keep
    # 3. External CCAS (dist 4km, code_insee 94000) -> Exclude
    # 4. External CIAS (dist 5km, code_insee 94000, CIAS) -> Keep
    # 5. Distant service (dist 15km) -> Exclude (> 10km)
    # 6. Broad zone service (dist 2km, zone_diffusion_type="departement") -> Exclude
    # 7. Duplicate of local service -> Exclude (dedup)
    mock_items = [
        {
            "distance": 1,
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
                    "commune": "Ville Test",
                    "code_insee": "94041",
                },
            },
        },
        {
            "distance": 0,
            "service": {
                "id": "srv_ccas_local",
                "nom": "Aide sociale",
                "structure_id": "struct-ccas-local",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-ccas-local",
                    "nom": "Centre Communal d'Action Sociale (CCAS) - Ville Test",
                    "commune": "Ville Test",
                    "code_insee": "94041",
                    "reseaux_porteurs": ["ccas-cias"],
                    "typologie": "CCAS",
                },
            },
        },
        {
            "distance": 4,
            "service": {
                "id": "srv_ccas_ext",
                "nom": "Aide sociale externe",
                "structure_id": "struct-ccas-ext",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-ccas-ext",
                    "nom": "CCAS Voisin",
                    "commune": "Ville Voisine",
                    "code_insee": "94000",
                    "reseaux_porteurs": ["ccas-cias"],
                    "typologie": "CCAS",
                },
            },
        },
        {
            "distance": 5,
            "service": {
                "id": "srv_cias",
                "nom": "Aide intercommunale",
                "structure_id": "struct-cias",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-cias",
                    "nom": "CIAS du Territoire",
                    "commune": "Ville Voisine",
                    "code_insee": "94000",
                    "reseaux_porteurs": ["ccas-cias"],
                },
            },
        },
        {
            "distance": 15,
            "service": {
                "id": "srv_distant",
                "nom": "Cours de français loin",
                "structure_id": "struct-distant",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-distant",
                    "nom": "Centre Loin",
                    "commune": "Ville Loin",
                },
            },
        },
        {
            "distance": 2,
            "service": {
                "id": "srv_broad",
                "nom": "Dispositif Départemental",
                "structure_id": "struct-broad",
                "zone_diffusion_type": "departement",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-broad",
                    "nom": "Conseil Départemental",
                },
            },
        },
        {
            "distance": 1,
            "service": {
                "id": "srv1-dup",
                "nom": "Cours de français A1",
                "structure_id": "struct-alpha",
                "thematiques": ["lecture-ecriture-calcul--maitriser-le-francais"],
                "structure": {
                    "id": "struct-alpha",
                    "nom": "Centre Social Alpha",
                },
            },
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

        # Kept: srv_ccas_local (dist 0), srv1 (dist 1), srv_cias (dist 5)
        # Excluded: srv_ccas_ext (ext CCAS), srv_distant (>10km), srv_broad (departement), srv1-dup (dedup)
        assert len(services) == 3

        # Verify sorted by proximity (distance ascending)
        assert services[0]["id"] == "srv_ccas_local"
        assert services[0]["distance_km"] == 0
        assert services[0]["commune_nom"] == "Ville Test"

        assert services[1]["id"] == "srv1"
        assert services[1]["distance_km"] == 1

        assert services[2]["id"] == "srv_cias"
        assert services[2]["distance_km"] == 5

        # Verify GET call was made with code_commune and GPS coordinates
        assert mock_get.call_count == 1
        mock_get.assert_called_once_with(
            "https://api.data.inclusion.gouv.fr/api/v1/search/services",
            headers={"Authorization": "Bearer fake_api_key"},
            params={
                "code_commune": "94041",
                "lat": 48.8,
                "lon": 2.45,
                "size": 100,
            },
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

        assert store[hash_val]["inclusion_services_status"]["94041"]["status"] == "not_configured"
        assert mock_get.call_count == 0
