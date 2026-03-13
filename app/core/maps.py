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

def build_scores_layer(df: pd.DataFrame) -> Tuple[flm.FeatureGroup, Optional[Any]]:
    """Builds the FeatureGroup for all scored communes or bassins de vie, colored by score."""
    fg = flm.FeatureGroup(name="Scores")
    
    id_col = 'codgeo'
    name_col = 'libgeo'
    tooltip_fields = [name_col, 'weighted_score']
    tooltip_aliases = ['Commune:', 'Score:']

    if id_col not in df.columns:
        # Check if it's in the index
        if df.index.name == id_col:
             df = df.reset_index()
        else:
             # Try resetting anyway, maybe index doesn't have a name but is the ID
             df = df.reset_index()
             if id_col not in df.columns:
                 # If still not found, rename 'index' to id_col if it looks right? 
                 # Or just return empty.
                 # Let's try to be robust: if 'index' is the column now, rename it?
                 if 'index' in df.columns:
                     df.rename(columns={'index': id_col}, inplace=True)
                 
                 if id_col not in df.columns:
                     return fg, None # Return empty layer if the required ID is missing

    score_dict = df.set_index(id_col)["weighted_score"]
    colormap = getattr(linear, 'YlGn_09').scale(score_dict.min(), score_dict.max())

    # Add current commune in blue
    current_geo_df = st.session_state.selected_geo
    
    # Default to Commune view
    # Prepare serializable DF in 4326 for Folium
    current_geo_df_serializable = current_geo_df[['libgeo', 'polygon']].copy()
    current_geo_df_serializable.set_geometry('polygon', inplace=True)
    if current_geo_df_serializable.crs != "EPSG:4326":
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*array with ndim > 0 to a scalar is deprecated.*")
            current_geo_df_serializable = current_geo_df_serializable.to_crs("EPSG:4326")

    flm.GeoJson(
        current_geo_df_serializable,
        style_function=lambda x: {"fillColor": 'blue', "fillOpacity": 0.5, "stroke": True, "color": "blue"},
        tooltip=current_geo_df_serializable['libgeo'].iloc[0]
    ).add_to(fg)

    # Add all scored geometries (communes or bassins de vie)
    df_serializable = df[[id_col, name_col, 'weighted_score', 'polygon']].copy()
    df_serializable.set_geometry('polygon', inplace=True)
    
    # Force the known CRS (PROJECTED_CRS) if missing, then convert to 4326 for Folium
    if df_serializable.crs is None:
        df_serializable.crs = cfg.PROJECTED_CRS
    
    if df_serializable.crs != "EPSG:4326":
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*array with ndim > 0 to a scalar is deprecated.*")
            df_serializable = df_serializable.to_crs("EPSG:4326")

    flm.GeoJson(
        df_serializable,
        style_function=lambda feature: {
            "fillColor": colormap(score_dict.get(feature["properties"][id_col])),
            "color": "grey",
            "weight": 1,
            "fillOpacity": 0.7,
        },
        tooltip=flm.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_aliases, fmt=['', '{:.0%}']),
    ).add_to(fg)

    return fg, colormap

def build_top_result_layer(row: pd.Series, rank: int) -> flm.FeatureGroup:
    """Builds a FeatureGroup to highlight a single top result (commune + binome)."""
    fg = flm.FeatureGroup(name=f"Top {rank + 1}")

    # Main commune outline
    # Project to 4326
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*array with ndim > 0 to a scalar is deprecated.*")
        poly_4326 = gpd.GeoSeries([row.polygon], crs=cfg.PROJECTED_CRS).to_crs("EPSG:4326").iloc[0]
    
    flm.GeoJson(
        mapping(poly_4326),
        style_function=lambda x: {"color": "red", "fillOpacity": 0, "weight": 3}
    ).add_to(fg)



    # Add rank marker at the centroid of the main polygon
    # Project centroid to 4326 using scalar-safe helper
    cx, cy = utils.project_point(row.polygon.centroid.x, row.polygon.centroid.y, from_crs=cfg.PROJECTED_CRS, to_crs='EPSG:4326')
    
    flm.Marker(
        location=[cy, cx],
        icon=flm.features.DivIcon(
            icon_size=(25, 25),
            icon_anchor=(12, 12),
            html=f'<div style="font-size: 12pt; font-weight: bold; color: white; background-color: #D63E2A; border-radius: 50%; text-align: center; line-height: 25px;">{rank + 1}</div>',
        )
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
        legend_html += f"""
            <li><i class='fa fa-{item['icon']}' style='color:{color}; width: 20px; text-align: center;'></i> {item['text']}</li>
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
