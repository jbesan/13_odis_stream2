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
    import geopandas as gpd
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
    pois_df = gpd.GeoDataFrame({
        "codgeo": ["75056", "75056"],
        "name": ["Mairie de Paris", "École Primaire"],
        "type": ["Mairie", "École Primaire"],
        "category": ["mairie", "education"],
        "geometry": [Point(2.35, 48.85), Point(2.36, 48.86)],
    }, crs="EPSG:4326")

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
