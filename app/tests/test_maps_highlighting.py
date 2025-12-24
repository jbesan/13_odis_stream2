
import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon, Point
import folium
import streamlit as st
from unittest.mock import MagicMock
import config as cfg

# Adjust the import path to match your project structure
from app.maps import build_scores_layer

@pytest.fixture
def mock_session_state():
    """Mocks Streamlit's session state."""
    # Create a real dictionary to act as session state
    session_state = {}
    
    # Mock st.session_state to behave like a dictionary
    # We need to patch st.session_state globally or contextually
    # Since st.session_state is a singleton proxy, we can't easily replace it.
    # However, we can mock the attributes if we are careful.
    # A better approach for testing streamlit apps is to mock the module or use st.session_state directly if possible (but it requires a running app context).
    # Here we will rely on the fact that the function uses `st.session_state` directly.
    # We can patch `app.maps.st.session_state` if `st` was imported as `import streamlit as st` inside `maps.py`.
    # But `maps.py` does `import streamlit as st` at module level.
    
    # Let's try to patch the `st` object in `app.maps`
    pass

def test_build_scores_layer_highlights_commune(monkeypatch):
    """
    Tests if build_scores_layer correctly highlights the current commune.
    """
    # Arrange
    bv_code = "BV123"
    commune_code = "COM456"
    
    commune_poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    selected_geo = pd.DataFrame({
        'codgeo': [commune_code],
        'libgeo': ['Commune Test'],
        cfg.BV_CODE_COL: [bv_code],
        'polygon': [commune_poly]
    })
    selected_geo = gpd.GeoDataFrame(selected_geo, geometry='polygon', crs="EPSG:4326")
    
    scored_df = pd.DataFrame({
        'codgeo': [commune_code],
        'libgeo': ['Commune Test'],
        'weighted_score': [0.8],
        'polygon': [commune_poly]
    })
    scored_df = gpd.GeoDataFrame(scored_df, geometry='polygon', crs="EPSG:4326")

    mock_state = MagicMock()
    mock_state.selected_geo = selected_geo
    
    monkeypatch.setattr("app.maps.st.session_state", mock_state)

    # Act
    fg, colormap = build_scores_layer(scored_df)

    # Assert
    layers = [child for child in fg._children.values() if isinstance(child, folium.GeoJson)]
    highlight_layer = layers[0]
    
    feature = highlight_layer.data['features'][0]
    geometry = feature['geometry']
    coords = geometry['coordinates'][0]
    
    max_coord = max(max(p) for p in coords)
    assert max_coord == 1.0, "Expected Commune polygon (max coord 1.0), but got something else."
    assert feature['properties']['libgeo'] == 'Commune Test'
