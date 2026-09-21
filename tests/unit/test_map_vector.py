import pandas as pd
from shapely.geometry import Polygon

from app.ui.map_vector import prepare_map_payload
from app.core.models import CommuneResult, SearchResultsData


def test_prepare_map_payload_minimal_size():
    p1 = Polygon([[2.0, 46.0], [2.1, 46.0], [2.1, 46.1], [2.0, 46.1]])

    # Synthetic scores dataframe indexed by codgeo
    df = pd.DataFrame(
        {
            "codgeo": ["01001", "75056"],
            "weighted_score": [0.85, 0.92],
            "polygon": [p1, p1],
        }
    ).set_index("codgeo")

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
    pois_df = pd.DataFrame(
        {
            "codgeo": ["75056", "75056"],
            "name": ["Mairie de Paris", "École Primaire"],
            "type": ["Mairie", "École Primaire"],
            "category": ["mairie", "education"],
            "geometry": [Point(2.35, 48.85), Point(2.36, 48.86)],
        }
    )

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
    pois_df = pd.DataFrame(
        {
            "codgeo": ["75056", "75056"],
            "name": ["Structure A", "Structure B"],
            "type": ["acces-aux-droits", "logement-hebergement"],
            "category": ["incl_services", "incl_services"],
            "geometry": [Point(2.35, 48.85), Point(2.36, 48.86)],
        }
    )

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


def test_render_vector_map_caching_and_ttl(monkeypatch):
    """Verify that render_vector_map injects the multi-tier caching mechanism with 1-year TTL."""
    from app.ui.map_vector import render_vector_map
    import streamlit as st

    captured = {}

    def fake_iframe(html, height=1500):
        captured["html"] = html

    monkeypatch.setattr(st, "iframe", fake_iframe)

    render_vector_map(
        gdf_scores=None,
        center=[46.5, 2.0],
        zoom=7,
    )

    html = captured.get("html", "")
    assert len(html) > 0
    assert "odis-communes-v1" in html
    assert "365 * 24 * 60 * 60 * 1000" in html
    assert "__communesGeoJsonMemoryCache" in html
    assert "__communesGeoJsonPromise" in html
    assert "cacheStorage.open(CACHE_NAME)" in html


def test_prepare_map_payload_snapshot_context_separation():
    """Verify that Top 5, wished city and current commune resolve correctly when contexts are separated."""
    p_lyon = Polygon([[4.8, 45.7], [4.9, 45.7], [4.9, 45.8], [4.8, 45.8]])
    p_marseille = Polygon([[5.3, 43.2], [5.4, 43.2], [5.4, 43.3], [5.3, 43.3]])
    p_paris = Polygon([[2.3, 48.8], [2.4, 48.8], [2.4, 48.9], [2.3, 48.9]])

    # Candidates & wished city in gdf_scores
    gdf_scores = pd.DataFrame(
        {
            "libgeo": ["Lyon", "Marseille"],
            "weighted_score": [0.88, 0.75],
            "polygon": [p_lyon, p_marseille],
        },
        index=pd.Index(["69123", "13055"], name="codgeo"),
    )

    # Current reference commune in current_map_context
    current_map_context = pd.DataFrame(
        {
            "libgeo": ["Paris"],
            "weighted_score": [0.0],
            "polygon": [p_paris],
        },
        index=pd.Index(["75056"], name="codgeo"),
    )

    c_top1 = CommuneResult(
        codgeo="69123",
        name="Lyon",
        population=500000,
        codgeo_bdv="69123",
        name_bdv="Lyon",
        global_score=0.88,
        scores={},
    )
    c_pressentie = CommuneResult(
        codgeo="13055",
        name="Marseille",
        population=800000,
        codgeo_bdv="13055",
        name_bdv="Marseille",
        global_score=0.75,
        scores={},
    )
    c_current = CommuneResult(
        codgeo="75056",
        name="Paris",
        population=2000000,
        codgeo_bdv="75056",
        name_bdv="Paris",
        global_score=0.50,
        scores={},
    )

    search_results = SearchResultsData(
        results=[c_top1],
        current_geo=c_current,
        commune_pressentie=c_pressentie,
        search_hash="test-hash-snapshot",
    )

    payload = prepare_map_payload(
        gdf_scores=gdf_scores,
        current_map_context=current_map_context,
        search_results=search_results,
        show_top_5=True,
    )

    # Verify Top 1 (Lyon)
    assert len(payload["top_markers"]) == 2
    top1 = next(m for m in payload["top_markers"] if m["codgeo"] == "69123")
    assert top1["rank"] == 1
    assert top1["type"] == "top5"
    assert round(top1["lat"], 2) == 45.75
    assert round(top1["lon"], 2) == 4.85

    # Verify Ville Souhaitée (Marseille)
    pressentie = next(m for m in payload["top_markers"] if m["codgeo"] == "13055")
    assert pressentie["rank"] == 0
    assert pressentie["type"] == "pressentie"
    assert round(pressentie["lat"], 2) == 43.25
    assert round(pressentie["lon"], 2) == 5.35

    # Verify Commune Actuelle (Paris)
    assert payload["current_marker"] is not None
    assert payload["current_marker"]["codgeo"] == "75056"
    assert payload["current_marker"]["name"] == "Paris"
    assert round(payload["current_marker"]["lat"], 2) == 48.85
    assert round(payload["current_marker"]["lon"], 2) == 2.35


def test_current_marker_fallback_without_centroid():
    """Verify that current_marker retains codgeo and name even if centroid cannot be computed."""
    c_current = CommuneResult(
        codgeo="75056",
        name="Paris",
        population=2000000,
        codgeo_bdv="75056",
        name_bdv="Paris",
        global_score=0.50,
        scores={},
    )
    search_results = SearchResultsData(
        results=[],
        current_geo=c_current,
        search_hash="test-hash-fallback",
    )

    payload = prepare_map_payload(
        gdf_scores=None,
        current_map_context=None,
        search_results=search_results,
    )

    assert payload["current_marker"] is not None
    assert payload["current_marker"]["codgeo"] == "75056"
    assert payload["current_marker"]["name"] == "Paris"
    assert payload["current_marker"]["lat"] is None
    assert payload["current_marker"]["lon"] is None


def test_render_vector_map_top_communes_layer_and_pressentie_color(monkeypatch):
    """Verify that render_vector_map includes top-communes-polygon-layer and yellow gold for pressentie."""
    from app.ui.map_vector import render_vector_map
    import streamlit as st

    captured = {}

    def fake_iframe(html, height=1500):
        captured["html"] = html

    monkeypatch.setattr(st, "iframe", fake_iframe)

    render_vector_map(
        gdf_scores=None,
        center=[46.5, 2.0],
        zoom=7,
    )

    html = captured.get("html", "")
    assert len(html) > 0
    assert "top-communes-polygon-layer" in html
    assert "[245, 216, 25, 255]" in html
    assert "[214, 62, 42, 255]" in html


def test_render_vector_map_pressentie_pastille_border_and_star(monkeypatch):
    """Verify that render_vector_map configures stroked ScatterplotLayer and star in TextLayer."""
    from app.ui.map_vector import render_vector_map
    import streamlit as st

    captured = {}

    def fake_iframe(html, height=1500):
        captured["html"] = html

    monkeypatch.setattr(st, "iframe", fake_iframe)

    render_vector_map(
        gdf_scores=None,
        center=[46.5, 2.0],
        zoom=7,
    )

    html = captured.get("html", "")
    assert "top5-circles-layer" in html
    assert "stroked: true" in html
    assert (
        "getLineColor: d => d.type === 'pressentie' ? [27, 68, 41, 255] : [255, 255, 255, 255]"
        in html
    )
    assert "top5-texts-layer" in html
    assert "characterSet: 'auto'" in html
    assert "d.type === 'pressentie' ? '★' : String(d.rank)" in html
