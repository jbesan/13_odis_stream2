
import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
import folium

# Adjust the import path to match your project structure
from app.maps import build_top_result_layer

@pytest.fixture
def sample_result_row():
    """Creates a sample GeoDataFrame row for testing."""
    data = {
        'libgeo': ['Testville'],
        'binome': [False],
        'polygon_binome': [None],
        'geometry': [Polygon([(0, 0), (1, 1), (1, 0)])]
    }
    gdf = gpd.GeoDataFrame(data, geometry='geometry', crs="EPSG:4326")
    # The geometry column in the app is named 'polygon', let's align with that
    gdf = gdf.rename_geometry('polygon')
    return gdf.iloc[0]

def test_build_top_result_layer_creates_ranked_marker(sample_result_row):
    """
    Tests if build_top_result_layer correctly adds a DivIcon marker with the rank.
    """
    # Arrange
    rank = 2  # Example rank (0-indexed, so this is for Top 3)
    row = sample_result_row

    # Act
    feature_group = build_top_result_layer(row, rank)

    # Assert
    assert isinstance(feature_group, folium.FeatureGroup)
    assert feature_group.layer_name == f"Top {rank + 1}"

    # Find the DivIcon marker among the children of the feature group
    div_icon_marker = None
    for child in feature_group._children.values():
        if isinstance(child, folium.Marker) and isinstance(child.icon, folium.features.DivIcon):
            div_icon_marker = child
            break

    # Check if the marker was found and if its HTML content is correct
    assert div_icon_marker is not None, "No DivIcon marker was found in the feature group."
    
    expected_rank_html = f'>{rank + 1}<'
    assert expected_rank_html in div_icon_marker.icon.options['html'], \
        f"The marker's HTML does not contain the correct rank. Expected '{expected_rank_html}'."

    # Check marker position
    centroid = row.polygon.centroid
    assert div_icon_marker.location == [centroid.y, centroid.x]

def test_build_top_result_layer_handles_binome(sample_result_row):
    """
    Tests if the function correctly handles a result with a 'binome' (pair).
    It should draw two GeoJson layers (main and binome).
    """
    # Arrange
    rank = 0
    row = sample_result_row.copy()
    row['binome'] = True
    row['polygon_binome'] = Polygon([(2, 2), (3, 3), (3, 2)])

    # Act
    feature_group = build_top_result_layer(row, rank)

    # Assert
    geojson_layers = [child for child in feature_group._children.values() if isinstance(child, folium.GeoJson)]
    assert len(geojson_layers) == 2, "Expected two GeoJson layers for a binome result."

