import geopandas as gpd
import pandas as pd
import pydeck as pdk
from shapely.geometry import Point, Polygon

from app.core.maps_deck import (
    compute_choropleth_colors,
    build_choropleth_layer,
    build_top_results_layers,
    build_poi_layers,
    create_deck_map,
)
from app.core.models import CommuneResult, SearchResultsData, SearchCriterias


def test_compute_choropleth_colors_vectorized():
    scores = pd.Series([0.0, 0.5, 1.0])
    colors = compute_choropleth_colors(scores, alpha=165)
    assert len(colors) == 3
    assert colors[0][:3] == [255, 255, 229]  # 0.0 YlGn anchor
    assert colors[2][:3] == [0, 69, 41]      # 1.0 YlGn anchor
    assert all(c[3] == 165 for c in colors)


def test_build_choropleth_layer_synthetic():
    p1 = Polygon([[2.0, 46.0], [2.1, 46.0], [2.1, 46.1], [2.0, 46.1]])
    p2 = Polygon([[2.1, 46.0], [2.2, 46.0], [2.2, 46.1], [2.1, 46.1]])
    df = pd.DataFrame({
        "codgeo": ["01001", "01002"],
        "libgeo": ["Commune A", "Commune B"],
        "weighted_score": [0.85, 0.42],
        "polygon": [p1, p2],
    })
    layer = build_choropleth_layer(df)
    assert layer is not None
    assert layer.type == "GeoJsonLayer"
    assert layer.id == "choropleth-scores-layer"


def test_build_top_results_layers_pure_text():
    p1 = Polygon([[2.0, 46.0], [2.1, 46.0], [2.1, 46.1], [2.0, 46.1]])
    c1 = CommuneResult(
        codgeo="75056",
        name="Paris",
        population=2000000,
        codgeo_bdv="75056",
        name_bdv="Paris",
        global_score=0.92,
        scores={},
        polygon=p1,
        centroid=p1.centroid,
    )
    search_results = SearchResultsData(
        results=[c1],
        current_geo=c1,
        commune_pressentie=None,
        search_hash="test-hash",
    )
    gdf_context = pd.DataFrame({"polygon": [p1]}, index=["75056"])
    layers = build_top_results_layers(search_results, gdf_context=gdf_context)
    assert len(layers) == 2
    # Outlines layer + Text layer
    layer_types = [l.type for l in layers]
    assert "GeoJsonLayer" in layer_types
    assert "TextLayer" in layer_types


def test_build_poi_layers_icons():
    pois_df = gpd.GeoDataFrame({
        "codgeo": ["75056", "75056"],
        "name": ["Mairie du 1er", "École Élémentaire"],
        "type": ["Mairie", "École Élémentaire"],
        "category": ["mairie", "education"],
        "lat": [48.85, 48.86],
        "lon": [2.35, 2.36],
        "geometry": [Point(2.35, 48.85), Point(2.36, 48.86)],
    }, crs="EPSG:4326")

    config = SearchCriterias(classe_enfants=["Elémentaire"])
    layers = build_poi_layers(
        pois=pois_df,
        target_codgeos={"75056"},
        config=config,
        selected_ids={"edu"},
    )
    assert len(layers) == 2  # Mairie + École
    for l in layers:
        assert l.type == "ScatterplotLayer"


def test_create_deck_map_full_assembly():
    p1 = Polygon([[2.0, 46.0], [2.1, 46.0], [2.1, 46.1], [2.0, 46.1]])
    df = pd.DataFrame({
        "codgeo": ["75056"],
        "libgeo": ["Paris"],
        "weighted_score": [0.85],
        "polygon": [p1],
    })
    deck = create_deck_map(
        gdf_scores=df,
        center=[46.5, 2.5],
        zoom=7,
        selected_ids=set(),
    )
    assert isinstance(deck, pdk.Deck)
    json_str = deck.to_json()
    assert "CARTO_LIGHT" in json_str or "carto" in json_str.lower() or "choropleth" in json_str
