# /home/jacques/odis/13_odis/eda/streamlit/maps.py
import streamlit as st
import folium as flm
import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping
from branca.colormap import linear
from folium.plugins import FastMarkerCluster, MarkerCluster

from typing import Union, List, Tuple, Optional, Any, Set, Dict
import config as cfg
from core.models import SearchCriterias

import logging
import warnings
from utils import common as utils
from utils import data_loader


def get_map_zoom(search_area: str) -> int:
    """Returns a map zoom level based on a search area scope."""
    if search_area == 'departement':
        return 9
    if search_area == 'region':
        return 8
    return 7 # Fallback for 'france' or unknown



def create_base_map(center: List[float], zoom: int) -> flm.Map:
    """Creates the base Folium map."""
    if center is None: center = cfg.DEFAULT_MAP_CENTER
    if zoom is None: zoom = get_map_zoom(st.session_state.config.loc_search_area)
    return flm.Map(location=center, zoom_start=zoom, tiles="cartodbpositron")

def build_scores_layer(df: pd.DataFrame, id_col: str = "codgeo", name_col: str = "libgeo") -> Tuple[flm.FeatureGroup, Optional[Any]]:
    """
    Builds a choropleth layer from a scored and pruned DataFrame.
    🧪 SOTA: Uses standardized 4326 geometries from the results.
    """
    fg = flm.FeatureGroup(name="Scores (Chaleur)")
    if df.empty:
        return fg, None

    # Ensure id_col is a column (it might be the index)
    if id_col not in df.columns:
        if df.index.name == id_col:
            df = df.reset_index()
        else:
            df = df.reset_index()
            if "index" in df.columns:
                df = df.rename(columns={"index": id_col})

    if id_col not in df.columns:
        logging.error(f"Required ID column '{id_col}' not found.")
        return fg, None

    # Cast IDs to string for robust matching with GeoJSON properties
    df[id_col] = df[id_col].astype(str)
    
    score_dict = df.set_index(id_col)["weighted_score"].to_dict()
    colormap = getattr(linear, 'YlGn_09').scale(min(score_dict.values()), max(score_dict.values()))

    # F-SDD: Pre-format the score for display
    df_serializable = df[[id_col, name_col, 'weighted_score', 'polygon']].copy()
    df_serializable['score_pct'] = df_serializable['weighted_score'].apply(lambda x: f"{x*100:.1f}%" if pd.notna(x) else "N/A")
    
    # Create GeoDataFrame (Already in 4326)
    gdf = gpd.GeoDataFrame(df_serializable, geometry='polygon', crs="EPSG:4326")
    
    flm.GeoJson(
        gdf,
        style_function=lambda feature: {
            "fillColor": colormap(score_dict.get(str(feature["properties"][id_col]))),
            "stroke": False,
            "color": "#1b4429",
            "weight": 0.5,
            "fillOpacity": 0.7,
        },
        tooltip=flm.GeoJsonTooltip(
            fields=[name_col, 'score_pct'], 
            aliases=['Commune:', 'Score:'],
            localize=True
        ),
    ).add_to(fg)

    return fg, colormap

def _get_geom(row: Union[pd.Series, Any], field: str = 'polygon') -> Optional[Any]:
    """Helper to extract geometry from either a pd.Series or a Pydantic model (CommuneResult)."""
    if hasattr(row, field):
        return getattr(row, field)
    # Pydantic CommuneResult uses 'geometry' and 'centroid' fields
    if field == 'polygon' and hasattr(row, 'geometry'):
        return row.geometry
    if field == 'centroid' and hasattr(row, 'centroid'):
        return row.centroid
    # Dictionary/Series access
    try:
        return row.get(field)
    except:
        return None

def build_top_result_layer(row: Union[pd.Series, Any], rank: int) -> flm.FeatureGroup:
    """Builds a FeatureGroup to highlight a single top result (commune + binome)."""
    fg = flm.FeatureGroup(name=f"Top {rank + 1}")

    poly = _get_geom(row, 'polygon')
    if poly is None:
        logging.warning(f"No polygon found for Top {rank+1}")
        return fg

    # Main commune outline
    # 🧪 SOTA: Standardized in 4326 already
    flm.GeoJson(
        mapping(poly),
        style_function=lambda x: {"color": "red", "fillOpacity": 0, "weight": 3}
    ).add_to(fg)


    # Add rank marker at the centroid of the main polygon
    c = poly.centroid
    cx, cy = c.x, c.y # Longitude, Latitude
    
    flm.Marker(
        location=[cy, cx], # Folium expects [lat, lon]
        icon=flm.features.DivIcon(
            icon_size=(25, 25),
            icon_anchor=(12, 12),
            html=f'<div style="font-size: 12pt; font-weight: bold; color: white; background-color: #D63E2A; border-radius: 50%; text-align: center; line-height: 25px;">{rank + 1}</div>',
        )
    ).add_to(fg)
        
    return fg

def build_current_loc_layer(row: Union[pd.Series, Any]) -> flm.FeatureGroup:
    """Builds a thick blue outline for the current location."""
    fg = flm.FeatureGroup(name="Commune Actuelle")
    
    poly = _get_geom(row, 'polygon')
    libgeo = _get_geom(row, 'libgeo') or "Ma position"

    if poly is None:
        return fg

    # 🧪 SOTA: Standardized in 4326 already
    current_geo_df = gpd.GeoDataFrame([{'libgeo': libgeo, 'polygon': poly}], geometry='polygon', crs="EPSG:4326")
    
    flm.GeoJson(
        current_geo_df,
        style_function=lambda x: {"fillColor": 'blue', "fillOpacity": 0.4, "stroke": True, "color": "blue", "weight": 4},
        tooltip=libgeo
    ).add_to(fg)

    return fg

def build_legend(items_list: List[Dict[str, str]]) -> str:
    """Builds an HTML legend for the map."""
    leaflet_colors = {
        "red": "#D63E2A", "blue": "#38A9DC", "green": "#72B026", "purple": "#5B396B",
        "orange": "#F69730", "grey": "#A3A3A3"
    }
    
    legend_html = """
        <div style='position: absolute; z-index: 9999; background-color: rgba(255, 255, 255, 0.8);
                    border-radius: 6px; padding: 10px; font-size: 12px; right: 10px; top: 10px;'>
            <ul style='list-style: none; margin: 0; padding: 0;'>
                <li><span style='display: inline-block; width:100px; background: linear-gradient(90deg, #ffffcc 0%, #006837 100%); 
                    border: 1px solid grey;'>&nbsp;</span> Score</li>
    """
    for item in items_list:
        color = leaflet_colors.get(item['color'], 'grey')
        icon_html = f"<i class='fa fa-{item['icon']}' style='color:{color}; width: 20px; text-align: center;'></i>" if item.get('icon') else f"<span style='display:inline-block; width:12px; height:12px; background-color:{color}; border-radius:50%; margin-right:5px; border:1px solid white;'></span>"
        legend_html += f"""
            <li>{icon_html} {item['text']}</li>
        """
    legend_html += "</ul></div>"
    return legend_html

def _build_generic_points_layer(df: gpd.GeoDataFrame, icon: str, color: str, tooltip_cols: List[str]) -> Any:
    """Generic helper to build a FastMarkerCluster layer."""
    df = df.copy()
    if df.empty:
        return flm.FeatureGroup()

    # Robustness: Check if geometry is available
    if hasattr(df, 'geometry') and df.geometry.name in df.columns:
        df['lat'] = df.geometry.y
        df['lon'] = df.geometry.x
    elif 'lat' in df.columns and 'lon' in df.columns:
        # Use existing lat/lon if geometry is missing (e.g. DataFrame instead of GeoDataFrame)
        pass
    else:
        logging.error("_build_generic_points_layer: No geometry or lat/lon columns found.")
        return flm.FeatureGroup()

    df.dropna(subset=['lat', 'lon'], inplace=True)
    
    # Prepare data for map layer
    
    # Switch to MarkerCluster for robustness with icons and popups
    # FastMarkerCluster with JS callback was proving fragile/broken in some contexts
    logging.info(f"Building MarkerCluster with {len(df)} points. Icon: {icon}, Color: {color}")
    
    cluster = MarkerCluster()
    
    for _, row in df.iterrows():
        # Construct popup content safely
        popup_content = "<br>".join([f"<b>{'Catégorie' if col == 'type' else 'Nom'}</b>: {row.get(col, '')}" for col in tooltip_cols])
        
        flm.Marker(
            location=[row['lat'], row['lon']],
            popup=flm.Popup(popup_content, max_width=300),
            icon=flm.Icon(color=color, icon=icon, prefix='fa')
        ).add_to(cluster)

    return cluster

def build_ecoles_layer(pois: gpd.GeoDataFrame, target_codgeos: Set[str], config: SearchCriterias) -> flm.FeatureGroup:
    """Builds the map layer for schools using unified POIs."""
    fg = flm.FeatureGroup(name="Établissements Scolaires")
    
    # Filter by category and codgeo
    filtered = pois[
        (pois['category'] == 'education') & 
        (pois['codgeo'].isin(target_codgeos))
    ].copy()
    
    if not config.classe_enfants or filtered.empty:
        logging.info("build_ecoles_layer: No children or no schools found.")
        return fg # No kids or no schools
        
    logging.info(f"build_ecoles_layer: Found {len(filtered)} schools before filtering by type.")
        
    # Filter by type (nature_uai_libe)
    # Replicate logic from data_loader.py using 'type' column
    # Data is now standardized in ETL (pipeline/build.py)
    
    # Robust type matching: check for variations and standardize
    is_maternelle = filtered['type'].isin(['Maternelle', 'Maternelle D Application', 'Maternelle d\'Application'])
    is_elementaire = filtered['type'].isin(['Elémentaire', 'Élémentaire', 'Elémentaire D Application', 'Élémentaire d\'Application', 'Elementaire'])
    is_college = filtered['type'].isin(['Collège', 'College'])
    is_lycee = filtered['type'].isin(['Lycée', 'Lycee'])
    
    # Map "Crèche / Assistante Maternelle" to available Crèche types
    creche_types = ['Crèche', 'Micro crèche', "Halte-garderie", "Crèche familiale", "Crèche collective", "Crèche parentale", "Creche"]
    # Ensure 'type_standardized' exists for the new logic, if not, fallback to 'type'
    if 'type_standardized' not in filtered.columns:
        filtered['type_standardized'] = filtered['type']
    
    def is_creche_func(row):
        return row['type_standardized'] in creche_types

    niveaux_map = {
        'Maternelle': is_maternelle,
        'Elémentaire': is_elementaire,
        'Collège': is_college,
        'Lycée': is_lycee,
        'Crèche / Assistante Maternelle': filtered.apply(is_creche_func, axis=1)
    }
    
    mask = pd.Series(False, index=filtered.index)
    for niveau in config.classe_enfants:
        if niveau in niveaux_map:
            mask |= niveaux_map[niveau]
            
    filtered = filtered[mask]
    logging.info(f"build_ecoles_layer: {len(filtered)} schools remaining after type filtering.")
    
    if filtered.empty:
        return fg

    cluster = _build_generic_points_layer(filtered, icon='pencil', color='green', tooltip_cols=['name', 'type'])
    cluster.add_to(fg)
    return fg

def build_sante_layer(pois: gpd.GeoDataFrame, target_codgeos: Set[str], config: SearchCriterias) -> flm.FeatureGroup:
    """Builds the map layer for health facilities using unified POIs."""
    fg = flm.FeatureGroup(name="Établissements de Santé")
    
    filtered = pois[
        (pois['category'] == 'sante') & 
        (pois['codgeo'].isin(target_codgeos))
    ].copy()
    
    if filtered.empty:
        logging.info("build_sante_layer: No health facilities found.")
        return fg
        
    logging.info(f"build_sante_layer: Found {len(filtered)} health facilities before filtering.")
    logging.info(f"build_sante_layer: Config selection: '{config.besoin_sante}'")
    if not filtered.empty:
        logging.info(f"build_sante_layer: Available types: {filtered['type'].unique()}")

    # Filter by type (Standardized in ETL)
    mask = pd.Series(False, index=filtered.index)
    if config.besoin_sante == 'Maternité':
        mask = filtered['type'] == 'Maternité'
    elif config.besoin_sante == "Hopital":
        mask = filtered['type'] == 'Hopital'
    elif config.besoin_sante == "Soutien Psychologique & Addictologie":
        mask = filtered['type'] == 'Soutien Psychologique & Addictologie'
    
    if not mask.any():
        logging.info("build_sante_layer: No facilities match the selected type.")
        return fg
    
    logging.info(f"build_sante_layer: {mask.sum()} facilities match the selected type.")

    cluster = _build_generic_points_layer(filtered[mask], icon='plus', color='blue', tooltip_cols=['name', 'type'])
    cluster.add_to(fg)
    return fg

def build_services_layer(pois: gpd.GeoDataFrame, target_codgeos: Set[str], config: SearchCriterias) -> flm.FeatureGroup:
    """Builds the map layer for inclusion services using unified POIs."""
    fg = flm.FeatureGroup(name="Services d'inclusion")
    
    if not config.inc_services_add_selection:
        return fg
        
    filtered = pois[
        (pois['category'] == 'incl_services') & 
        (pois['codgeo'].isin(target_codgeos))
    ].copy()
    
    if filtered.empty:
        return fg

    # 'type' column contains 'thematiques' (e.g. "category--service")
    # We check if 'type' starts with any of the selected categories
    # config.besoins_autres is a dict or list of slugs?
    # It's a dict {slug: label} or just keys.
    # In data_loader.py, we split thematiques into categorie and service.
    # Here we can just check string containment or split 'type'.
    
    # Extract category from type
    # filtered['categorie'] = filtered['type'].str.split('--').str[0]
    
    # mask = filtered['categorie'].isin(config.besoins_autres.keys())
    
    # New logic: check if 'type' (slug) is in the list of selected slugs
    if isinstance(config.inc_services_add_selection, list):
        slugs = []
        for item in config.inc_services_add_selection:
            slugs.append(item.code if hasattr(item, 'code') else item)
        mask = filtered['type'].isin(slugs)
    elif isinstance(config.inc_services_add_selection, dict):
        # Backward compatibility if it's still a dict (keys are categories?)
        # Or keys are slugs? The old code used keys() as categories.
        # Let's assume keys are categories if it's a dict, but user said it's a list of slugs.
        # If it was a dict of slugs, we'd check keys.
        # But old code split type to get category.
        # Let's just try to match type against keys if it's a dict, or split.
        # Safest is to log warning and try exact match.
        logging.warning("build_services_layer: inc_services_add_selection is a dict, using keys as slugs.")
        mask = filtered['type'].isin(config.inc_services_add_selection.keys())
    else:
        mask = pd.Series(False, index=filtered.index)
        
    logging.info(f"build_services_layer: {mask.sum()} services match the selection.")
    
    if not mask.any():
        return fg

    cluster = _build_generic_points_layer(filtered[mask], icon='heart', color='purple', tooltip_cols=['name', 'type'])
    cluster.add_to(fg)
    return fg
