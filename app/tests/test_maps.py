
import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
import folium

# Adjust the import path to match your project structure
from app.maps import build_top_result_layer
import app.config as cfg

@pytest.fixture
def sample_result_row():
    """Creates a sample GeoDataFrame row for testing."""
    # Use coordinates that are valid for EPSG:2154 (Lambert-93)
    # Roughly a triangle near Paris
    data = {
        'libgeo': ['Testville'],
        'binome': [False],
        'polygon_binome': [None],
        'geometry': [Polygon([(600000, 6800000), (610000, 6810000), (610000, 6800000)])]
    }
    # Note: We create it without CRS, or generic, because the function assumes 2154
    gdf = gpd.GeoDataFrame(data, geometry='geometry')
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

    # Check marker position (Must convert to EPSG:4326 to match marker)
    centroid = row.polygon.centroid
    centroid_4326 = gpd.GeoSeries([centroid], crs=cfg.PROJECTED_CRS).to_crs("EPSG:4326").iloc[0]
    
    # Folium uses [lat, lon] -> [y, x]
    expected_location = [centroid_4326.y, centroid_4326.x]
    
    # Allow for small floating point differences
    assert div_icon_marker.location == pytest.approx(expected_location, abs=1e-6)

def test_build_top_result_layer_handles_binome(sample_result_row):
    """
    Tests if the function correctly handles a result with a 'binome' (pair).
    It should draw two GeoJson layers (main and binome).
    """
    # Arrange
    rank = 0
    row = sample_result_row.copy()
    row['binome'] = True
    # Valid 2154 coords for binome too
    row['polygon_binome'] = Polygon([(620000, 6820000), (630000, 6830000), (630000, 6820000)])

    # Act
    feature_group = build_top_result_layer(row, rank)

    # Assert
    geojson_layers = [child for child in feature_group._children.values() if isinstance(child, folium.GeoJson)]
    assert len(geojson_layers) == 2, "Expected two GeoJson layers for a binome result."
