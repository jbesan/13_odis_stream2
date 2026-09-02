import pandas as pd
from shapely.geometry import Polygon

from app.ui.map_vector import prepare_map_payload
from app.core.models import CommuneResult, SearchResultsData


def test_prepare_map_payload_minimal_size():
    p1 = Polygon([[2.0, 46.0], [2.1, 46.0], [2.1, 46.1], [2.0, 46.1]])
    
    # Synthetic scores dataframe indexed by codgeo
    df = pd.DataFrame({
        "codgeo": ["01001", "75056"],
        "weighted_score": [0.85, 0.92],
        "polygon": [p1, p1],
    }).set_index("codgeo")
    
    c1 = CommuneResult(
        codgeo="75056",
        name="Paris",
        population=2000000,
        codgeo_bdv="75056",
        name_bdv="Paris",
        global_score=0.92,
        scores={},
    )
    search_results = SearchResultsData(
        results=[c1],
        current_geo=c1,
        commune_pressentie=None,
        search_hash="test-hash-vector",
    )
    
    payload = prepare_map_payload(
        gdf_scores=df,
        center=[48.85, 2.35],
        zoom=6,
        search_results=search_results,
        selected_ids={"edu", "sante"},
    )
    
    assert "scores" in payload
    assert payload["scores"]["01001"] == 0.85
    assert payload["scores"]["75056"] == 0.92
    assert payload["center"] == [48.85, 2.35]
    assert payload["zoom"] == 6
    assert len(payload["top_markers"]) == 1
    assert payload["top_markers"][0]["name"] == "Paris"
    assert payload["geojson_url"] == "/app/static/data/communes_france.geojson"


def test_prepare_map_payload_poi_filtering():
    import pandas as pd
    from shapely.geometry import Point

    c1 = CommuneResult(
        codgeo="75056",
        name="Paris",
        population=2000000,
        codgeo_bdv="75056",
        name_bdv="Paris",
        global_score=0.92,
        scores={},
    )
    search_results = SearchResultsData(
        results=[c1],
        current_geo=c1,
        commune_pressentie=None,
        search_hash="test-hash-pois",
    )
    pois_df = pd.DataFrame({
        "codgeo": ["75056", "75056"],
        "name": ["Mairie de Paris", "École Primaire"],
        "type": ["Mairie", "École Primaire"],
        "category": ["mairie", "education"],
        "geometry": [Point(2.35, 48.85), Point(2.36, 48.86)],
    })

    # 1. Mairie selected
    payload_mairie = prepare_map_payload(
        search_results=search_results,
        pois_df=pois_df,
        selected_ids={"mairie"},
    )
    assert len(payload_mairie["poi_markers"]) == 1
    assert payload_mairie["poi_markers"][0]["category"] == "mairie"

    # 2. None selected
    payload_none = prepare_map_payload(
        search_results=search_results,
        pois_df=pois_df,
        selected_ids=set(),
    )
    assert len(payload_none["poi_markers"]) == 0

    # 3. Both selected
    payload_both = prepare_map_payload(
        search_results=search_results,
        pois_df=pois_df,
        selected_ids={"mairie", "edu"},
    )
    assert len(payload_both["poi_markers"]) == 2


def test_prepare_map_payload_inclusion_filtering():
    import pandas as pd
    from shapely.geometry import Point
    from app.core.models import CriteriaItem, SearchCriterias

    c1 = CommuneResult(
        codgeo="75056",
        name="Paris",
        population=2000000,
        codgeo_bdv="75056",
        name_bdv="Paris",
        global_score=0.92,
        scores={},
    )
    search_results = SearchResultsData(
        results=[c1],
        current_geo=c1,
        commune_pressentie=None,
        search_hash="test-hash-inc",
    )
    pois_df = pd.DataFrame({
        "codgeo": ["75056", "75056"],
        "name": ["Structure A", "Structure B"],
        "type": ["acces-aux-droits", "logement-hebergement"],
        "category": ["incl_services", "incl_services"],
        "geometry": [Point(2.35, 48.85), Point(2.36, 48.86)],
    })

    # 1. Inclusion active, no specific filter
    payload = prepare_map_payload(
        search_results=search_results,
        pois_df=pois_df,
        selected_ids={"inc"},
    )
    assert len(payload["poi_markers"]) == 2
    assert payload["poi_markers"][0]["category"] == "incl_services"

    # 2. Inclusion active with thematic filter in config
    config = SearchCriterias(
        inc_services_selection=[
            CriteriaItem(code="acces-aux-droits", label="Accès aux droits")
        ]
    )
    payload_filtered = prepare_map_payload(
        search_results=search_results,
        config=config,
        pois_df=pois_df,
        selected_ids={"inc"},
    )
    assert len(payload_filtered["poi_markers"]) == 1
    assert payload_filtered["poi_markers"][0]["name"] == "Structure A"
    assert payload_filtered["poi_markers"][0]["type"] == "acces-aux-droits"

    # 3. Inclusion active with inclusion_services_index mapping
    inc_index = pd.DataFrame(
        [{"code": "acces-aux-droits", "label": "Accès aux droits"}]
    ).set_index("code")
    payload_mapped = prepare_map_payload(
        search_results=search_results,
        config=config,
        pois_df=pois_df,
        selected_ids={"inc"},
        inclusion_services_index=inc_index,
    )
    assert len(payload_mapped["poi_markers"]) == 1
    assert payload_mapped["poi_markers"][0]["name"] == "Structure A"
    assert payload_mapped["poi_markers"][0]["type"] == "Accès aux droits"


def test_prepare_map_payload_center_offset():
    """Verify that center_offset_lon is correctly added to longitude."""
    payload = prepare_map_payload(
        gdf_scores=None,
        center=[46.5, 2.0],
        zoom=9,
        center_offset_lon=-0.5,
    )
    assert payload["center"] == [46.5, 1.5]
    assert payload["zoom"] == 9

