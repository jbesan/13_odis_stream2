
import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
import folium

# Adjust the import path to match your project structure
from core.maps import build_top_result_layer
import config as cfg
from utils import common as utils

@pytest.fixture
def sample_result_row():
    """Creates a sample GeoDataFrame row for testing."""
    # Use coordinates that are valid for EPSG:4326 (WGS84)
    # Roughly a triangle near Paris (Lon, Lat)
    data = {
        'libgeo': ['Testville'],
        'binome': [False],
        'polygon_binome': [None],
        'geometry': [Polygon([(2.3, 48.8), (2.4, 48.9), (2.4, 48.8)])]
    }
    # Note: The function assumes input is already in 4326 as per SOTA comments
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

    # Check marker position (Should be the centroid of the polygon in 4326)
    centroid = row.polygon.centroid
    
    # Folium uses [lat, lon] -> [y, x]
    expected_location = [centroid.y, centroid.x]
    
    # Allow for small floating point differences
    assert div_icon_marker.location == pytest.approx(expected_location, abs=1e-6)

