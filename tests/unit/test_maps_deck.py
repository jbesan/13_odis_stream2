import pandas as pd
from shapely.geometry import Point, Polygon

from app.core.maps_deck import (
    compute_choropleth_colors,
    build_choropleth_legend_html,
    get_map_zoom,
    _get_geom,
)
from app.core.models import CommuneResult


def test_compute_choropleth_colors_vectorized():
    scores = pd.Series([0.0, 0.5, 1.0])
    colors = compute_choropleth_colors(scores, alpha=165)
    assert len(colors) == 3
    assert colors[0][:3] == [255, 255, 229]  # 0.0 YlGn anchor
    assert colors[2][:3] == [0, 69, 41]      # 1.0 YlGn anchor
    assert all(c[3] == 165 for c in colors)


def test_build_choropleth_legend_uses_the_map_palette():
    legend = build_choropleth_legend_html([("#D63E2A", "Top 5")])
    assert "linear-gradient" in legend
    assert "rgb(255, 255, 229)" in legend
    assert "rgb(0, 69, 41)" in legend
    assert "Top 5" in legend


def test_get_map_zoom_scopes():
    assert get_map_zoom("departement") == 9
    assert get_map_zoom("region") == 8
    assert get_map_zoom("france") == 6


def test_get_geom_extraction():
    poly = Polygon([[2.0, 46.0], [2.1, 46.0], [2.1, 46.1], [2.0, 46.1]])
    gdf_context = pd.DataFrame({"polygon": [poly]}, index=["75056"])
    c = CommuneResult(
        codgeo="75056",
        name="Paris",
        population=2000000,
        codgeo_bdv="75056",
        name_bdv="Paris",
        global_score=0.92,
        scores={},
    )
    assert _get_geom(c, "polygon", gdf_context=gdf_context) == poly
    pt = _get_geom(c, "centroid", gdf_context=gdf_context)
    assert pt is not None
    assert isinstance(pt, Point)
