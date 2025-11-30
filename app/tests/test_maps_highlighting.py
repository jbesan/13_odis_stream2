
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

def test_build_scores_layer_highlights_bv(monkeypatch):
    """
    Tests if build_scores_layer uses the BV polygon when view_level is 'Bassins de vie'.
    """
    # Arrange
    # 1. Mock Data
    bv_code = "BV123"
    commune_code = "COM456"
    
    # Current Commune (Selected Geo)
    commune_poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    selected_geo = pd.DataFrame({
        'codgeo': [commune_code],
        'libgeo': ['Commune Test'],
        cfg.BV_CODE_COL: [bv_code],
        'polygon': [commune_poly]
    })
    selected_geo = gpd.GeoDataFrame(selected_geo, geometry='polygon', crs="EPSG:4326")
    
    # BV Geo Data (App Data)
    bv_poly = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    bv_point = Point(1, 1)
    bv_geo = pd.DataFrame({
        cfg.BV_CODE_COL: [bv_code],
        cfg.BV_NAME_COL: ['Bassin Test'],
        'geometry': [bv_poly],
        'centroid': [bv_point]
    }).set_index(cfg.BV_CODE_COL)
    bv_geo = gpd.GeoDataFrame(bv_geo, geometry='geometry', crs="EPSG:4326")
    
    # Scored Data (Input to function)
    scored_df = pd.DataFrame({
        cfg.BV_CODE_COL: [bv_code],
        'libgeo': ['Bassin Test'],
        'weighted_score': [0.8],
        'polygon': [bv_poly]
    })
    scored_df = gpd.GeoDataFrame(scored_df, geometry='polygon', crs="EPSG:4326")

    # 2. Mock Session State
    mock_state = MagicMock()
    mock_state.selected_geo = selected_geo
    mock_state.app_data = {'bv_geo': bv_geo}
    mock_state.get.side_effect = lambda k, d=None: 'Bassins de vie' if k == 'view_level' else d
    
    # Patch st.session_state in app.maps
    monkeypatch.setattr("app.maps.st.session_state", mock_state)

    # Act
    fg, colormap = build_scores_layer(scored_df)

    # Assert
    # Verify that the blue highlight layer (first GeoJson added) uses the BV polygon
    # The function adds: 
    # 1. Current location highlight (Blue)
    # 2. Scored items (Colormap)
    
    # We need to inspect the children of the FeatureGroup
    # Folium FeatureGroup children are stored in a dict `_children`
    
    layers = [child for child in fg._children.values() if isinstance(child, folium.GeoJson)]
    assert len(layers) >= 1
    
    highlight_layer = layers[0]
    
    # Check style to confirm it's the blue one
    # Note: style_function is a lambda, so we can't easily check it without running it.
    # But we know the order: highlight is added first.
    
    # Check the data in the GeoJson
    # The data is stored in `data` attribute as a GeoJSON-like dict
    feature = highlight_layer.data['features'][0]
    geometry = feature['geometry']
    
    # Check if coordinates match BV polygon (larger square)
    # BV Poly: (0,0), (2,0), (2,2), (0,2)
    # Commune Poly: (0,0), (1,0), (1,1), (0,1)
    
    coords = geometry['coordinates'][0] # Outer ring
    # Folium/GeoJSON coordinates are usually lists of lists
    
    # Simple check: max coordinate value should be 2 for BV, 1 for Commune
    max_coord = max(max(p) for p in coords)
    assert max_coord == 2.0, "Expected BV polygon (max coord 2.0), but got something else."
    
    # Check Tooltip
    assert feature['properties']['libgeo'] == 'Bassin Test'

def test_build_scores_layer_highlights_commune_in_commune_view(monkeypatch):
    """
    Tests if build_scores_layer uses the Commune polygon when view_level is 'Communes'.
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
    
    bv_poly = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    bv_geo = pd.DataFrame({
        cfg.BV_CODE_COL: [bv_code],
        cfg.BV_NAME_COL: ['Bassin Test'],
        'geometry': [bv_poly]
    }).set_index(cfg.BV_CODE_COL)
    bv_geo = gpd.GeoDataFrame(bv_geo, geometry='geometry', crs="EPSG:4326")
    
    scored_df = pd.DataFrame({
        'codgeo': [commune_code],
        'libgeo': ['Commune Test'],
        'weighted_score': [0.8],
        'polygon': [commune_poly]
    })
    scored_df = gpd.GeoDataFrame(scored_df, geometry='polygon', crs="EPSG:4326")

    mock_state = MagicMock()
    mock_state.selected_geo = selected_geo
    mock_state.app_data = {'bv_geo': bv_geo}
    mock_state.get.side_effect = lambda k, d=None: 'Communes' if k == 'view_level' else d
    
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
