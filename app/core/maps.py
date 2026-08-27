# /home/jacques/odis/13_odis/eda/streamlit/maps.py
import streamlit as st
import folium as flm
import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping
from branca.colormap import linear

from typing import Union, List, Tuple, Optional, Any, Set, Dict
import config as cfg
from core.models import SearchCriterias
from utils import data_loader  # noqa: F401

import logging


def get_map_zoom(search_area: str) -> int:
    """Returns a map zoom level based on a search area scope."""
    if search_area == "departement":
        return 9
    if search_area == "region":
        return 8
    return 7  # Fallback for 'france' or unknown


def create_base_map(center: List[float], zoom: int) -> flm.Map:
    """
    Creates the base Folium map.
    Uses OpenStreetMap France Humanitarian (HOT) tiles for softer pastel colors and full French toponymy.
    🧪 SOTA: 'prefer_canvas=True' is critical for 35k+ polygons at France-wide scale.
    """
    if center is None:
        center = cfg.DEFAULT_MAP_CENTER
    if zoom is None:
        zoom = get_map_zoom(st.session_state.config.loc_search_area)
    return flm.Map(
        location=center,
        zoom_start=zoom,
        tiles="https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
        attr='&copy; OpenStreetMap France | &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        prefer_canvas=True,
    )


def build_scores_layer(
    df: pd.DataFrame, id_col: str = "codgeo", name_col: str = "libgeo"
) -> Tuple[flm.FeatureGroup, Optional[Any]]:
    """
    Builds a choropleth layer from a scored and pruned DataFrame.
    🧪 Performance: JIT Decoding from raw WKB bytes.
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
    colormap = getattr(linear, "YlGn_09").scale(
        min(score_dict.values()), max(score_dict.values())
    )

    # F-SDD: Pre-format the score for display
    df_serializable = df[[id_col, name_col, "weighted_score", "polygon"]].copy()
    df_serializable["score_pct"] = df_serializable["weighted_score"].apply(
        lambda x: f"{x * 100:.1f}%" if pd.notna(x) else "N/A"
    )

    # 🧪 JIT Hydration: Vectorized WKB Decoding (Much faster than .apply)
    # This handles France-wide (35k rows) in milliseconds instead of seconds.
    # Robustness check: if already Shapely polygons (eg. in tests), skip from_wkb
    first_valid = (
        df_serializable["polygon"].dropna().iloc[0]
        if not df_serializable["polygon"].dropna().empty
        else None
    )
    if isinstance(first_valid, (bytes, bytearray)):
        geom = gpd.GeoSeries.from_wkb(df_serializable["polygon"], crs="EPSG:4326")
    else:
        # Fallback for already decoded geometries (e.g. tests)
        geom = gpd.GeoSeries(df_serializable["polygon"], crs="EPSG:4326")

    # Assign back and drop any missing geometries to protect Folium
    df_serializable["polygon"] = geom
    df_serializable = df_serializable.dropna(subset=["polygon"])

    if df_serializable.empty:
        logging.warning("build_scores_layer: No valid geometries found.")
        return fg, None

    # Create GeoDataFrame in 4326
    gdf = gpd.GeoDataFrame(df_serializable, geometry="polygon", crs="EPSG:4326")

    flm.GeoJson(
        gdf,
        style_function=lambda feature: {
            "fillColor": colormap(score_dict.get(str(feature["properties"][id_col]))),
            "stroke": True,
            "color": "#1b4429",
            "weight": 0.5,
            "fillOpacity": 0.7,
        },
        tooltip=flm.GeoJsonTooltip(
            fields=[name_col, "score_pct"],
            aliases=["Commune:", "Score:"],
            localize=True,
        ),
    ).add_to(fg)

    return fg, colormap


def _get_geom(
    row: Union[pd.Series, Any],
    field: str = "polygon",
    gdf_context: Optional[pd.DataFrame] = None,
) -> Optional[Any]:
    """
    Helper to extract geometry from a row or model.
    🧪 Performance: JIT Decoding from WKB bytes.
    """
    from shapely import wkb

    codgeo = None
    if hasattr(row, "codgeo") and row.codgeo:
        codgeo = str(row.codgeo)
    elif isinstance(row, pd.Series):
        codgeo = str(row.name) if "codgeo" not in row else str(row["codgeo"])
    elif isinstance(row, dict):
        codgeo = str(row.get("codgeo", ""))

    # 1. Try Lookup in provided context (Fastest)
    if gdf_context is not None and codgeo in gdf_context.index:
        try:
            # 🧪 SOTA: If centroid requested but missing, fallback to polygon-based JIT calculation
            if field == "centroid" and "centroid" not in gdf_context.columns:
                poly_wkb = gdf_context.loc[codgeo, "polygon"]
                poly = (
                    wkb.loads(bytes(poly_wkb))
                    if isinstance(poly_wkb, (bytes, bytearray))
                    else poly_wkb
                )
                return poly.centroid if poly else None

            val = gdf_context.loc[codgeo, field]

            # JIT Decode if it's a WKB blob
            if isinstance(val, (bytes, bytearray)):
                return wkb.loads(bytes(val))
            return val
        except KeyError:
            # Maybe the column name in GDF is different (e.g. 'geometry')
            alt_field = "geometry" if field == "polygon" else "polygon"
            val = gdf_context.loc[codgeo].get(alt_field)

            # 🧪 JIT Decode if it's a WKB blob
            geom = val
            if isinstance(val, (bytes, bytearray)):
                geom = wkb.loads(bytes(val))

            # If centroid was requested but we found a polygon (or vice versa), handle conversion
            if field == "centroid" and geom and not hasattr(geom, "x"):
                return geom.centroid
            return geom

    # 2. Fallback: check the object itself (Metadata only)
    val = None
    if hasattr(row, field):
        val = getattr(row, field)
    elif isinstance(row, dict) and field in row:
        val = row.get(field)

    # 🧪 JIT Decode if it's a WKB blob or handle centroid conversion
    if val is not None:
        geom = val
        if isinstance(val, (bytes, bytearray)):
            geom = wkb.loads(bytes(val))

        if field == "centroid" and geom and not hasattr(geom, "x"):
            return geom.centroid
        return geom

    return None


def build_top_result_layer(
    row: Union[pd.Series, Any], rank: int, gdf_context: Optional[pd.DataFrame] = None
) -> flm.FeatureGroup:
    """Builds a FeatureGroup to highlight a single top result (commune + binome) or shortlisted city (rank=-1)."""
    is_pressentie = rank == -1
    fg_name = "Ville Pressentie" if is_pressentie else f"Top {rank + 1}"
    fg = flm.FeatureGroup(name=fg_name)

    poly = _get_geom(row, "polygon", gdf_context=gdf_context)
    if poly is None:
        logging.warning(f"No polygon found for {fg_name}")
        return fg

    # Outline color: yellow #F5D819 for pressentie, red for top 5
    border_color = "#F5D819" if is_pressentie else "red"

    flm.GeoJson(
        mapping(poly),
        style_function=lambda x: {"color": border_color, "fillOpacity": 0, "weight": 3},
    ).add_to(fg)

    # Add rank marker at the centroid of the main polygon
    # 🧪 JIT: _get_geom handles the decoding/calculation if necessary
    c = _get_geom(row, "centroid", gdf_context=gdf_context)
    if c is None:
        return fg

    cx, cy = c.x, c.y  # Longitude, Latitude

    if is_pressentie:
        # Yellow background, premium Material Design push_pin SVG
        marker_html = """
        <div style="background-color: #F5D819; border: 2px solid #1B4429; border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 5px rgba(0,0,0,0.3);">
            <svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="#1B4429">
                <path d="m640-480 80 80v80H520v240l-40 40-40-40v-240H240v-80l80-80v-280h-40v-80h400v80h-40v280Z"/>
            </svg>
        </div>
        """
        icon_size = (32, 32)
        icon_anchor = (16, 16)
    else:
        marker_html = f'<div style="font-size: 12pt; font-weight: bold; color: white; background-color: #D63E2A; border-radius: 50%; text-align: center; line-height: 25px;">{rank + 1}</div>'
        icon_size = (25, 25)
        icon_anchor = (12, 12)

    flm.Marker(
        location=[cy, cx],  # Folium expects [lat, lon]
        icon=flm.features.DivIcon(
            icon_size=icon_size,
            icon_anchor=icon_anchor,
            html=marker_html,
        ),
    ).add_to(fg)

    return fg


def build_current_loc_layer(
    row: Union[pd.Series, Any], gdf_context: Optional[pd.DataFrame] = None
) -> flm.FeatureGroup:
    """Builds a thick blue outline for the current location."""
    fg = flm.FeatureGroup(name="Commune Actuelle")

    poly = _get_geom(row, "polygon", gdf_context=gdf_context)
    libgeo = _get_geom(row, "libgeo", gdf_context=gdf_context) or "Ma position"

    if poly is None:
        return fg

    # 🧪 SOTA: Standardized in 4326 already
    current_geo_df = gpd.GeoDataFrame(
        [{"libgeo": libgeo, "polygon": poly}], geometry="polygon", crs="EPSG:4326"
    )

    flm.GeoJson(
        current_geo_df,
        style_function=lambda x: {
            "fillColor": "blue",
            "fillOpacity": 0.4,
            "stroke": True,
            "color": "blue",
            "weight": 4,
        },
        tooltip=libgeo,
    ).add_to(fg)

    return fg


def build_legend(items_list: List[Dict[str, str]]) -> str:
    """Builds an HTML legend for the map."""
    leaflet_colors = {
        "red": "#D63E2A",
        "blue": "#38A9DC",
        "green": "#72B026",
        "purple": "#5B396B",
        "orange": "#F69730",
        "grey": "#A3A3A3",
        "yellow": "#F5D819",
    }

    legend_html = """
        <div style='position: absolute; z-index: 9999; background-color: rgba(255, 255, 255, 0.8);
                    border-radius: 6px; padding: 10px; font-size: 12px; right: 10px; top: 10px;'>
            <ul style='list-style: none; margin: 0; padding: 0;'>
                <li><span style='display: inline-block; width:100px; background: linear-gradient(90deg, #ffffcc 0%, #006837 100%); 
                    border: 1px solid grey;'>&nbsp;</span> Score</li>
    """
    for item in items_list:
        color = leaflet_colors.get(item["color"], "grey")
        icon_html = (
            f"<i class='fa fa-{item['icon']}' style='color:{color}; width: 20px; text-align: center;'></i>"
            if item.get("icon")
            else f"<span style='display:inline-block; width:12px; height:12px; background-color:{color}; border-radius:50%; margin-right:5px; border:1px solid white;'></span>"
        )
        legend_html += f"""
            <li>{icon_html} {item["text"]}</li>
        """
    legend_html += "</ul></div>"
    return legend_html


def _build_generic_points_layer(
    df: gpd.GeoDataFrame, icon: str, color: str, tooltip_cols: List[str]
) -> Any:
    """Generic helper to build a FastMarkerCluster layer."""
    df = df.copy()
    if df.empty:
        return flm.FeatureGroup()

    # Robustness: Check if geometry is available
    if hasattr(df, "geometry") and df.geometry.name in df.columns:
        df["lat"] = df.geometry.y
        df["lon"] = df.geometry.x
    elif "lat" in df.columns and "lon" in df.columns:
        # Use existing lat/lon if geometry is missing (e.g. DataFrame instead of GeoDataFrame)
        pass
    else:
        logging.error(
            "_build_generic_points_layer: No geometry or lat/lon columns found."
        )
        return flm.FeatureGroup()

    df.dropna(subset=["lat", "lon"], inplace=True)

    # Prepare data for map layer

    # 🧪 SOTA: Using a plain FeatureGroup instead of MarkerCluster for maximum st-folium compatibility
    # plugins like MarkerCluster can sometimes interfere with incremental updates
    fg = flm.FeatureGroup()

    for _, row in df.iterrows():
        popup_content = "<br>".join(
            [f"<b>{col.capitalize()}</b>: {row.get(col, '')}" for col in tooltip_cols]
        )
        flm.Marker(
            location=[row["lat"], row["lon"]],
            popup=flm.Popup(popup_content, max_width=300),
            icon=flm.Icon(color=color, icon=icon, prefix="fa"),
        ).add_to(fg)

    return fg


def build_ecoles_layer(
    pois: gpd.GeoDataFrame, target_codgeos: Set[str], config: SearchCriterias
) -> flm.FeatureGroup:
    """Builds the map layer for schools using unified POIs."""
    fg = flm.FeatureGroup(name="Établissements Scolaires")

    # Filter by category and codgeo
    filtered = pois[
        (pois["category"] == "education") & (pois["codgeo"].isin(target_codgeos))
    ].copy()

    if not config.classe_enfants or filtered.empty:
        logging.info("build_ecoles_layer: No children or no schools found.")
        return fg  # No kids or no schools

    logging.info(
        f"build_ecoles_layer: Found {len(filtered)} schools before filtering by type."
    )

    # Standardized type matching from clean_bpe
    is_maternelle = filtered["type"].isin(["École Maternelle", "École Primaire"])
    is_elementaire = filtered["type"].isin(["École Élémentaire", "École Primaire"])
    is_college = filtered["type"] == "Collège"
    is_lycee = filtered["type"].isin(
        [
            "Lycée Général/Tech",
            "Lycée Professionnel",
            "Lycée Agricole",
            "Section Enseignement Pro",
        ]
    )
    is_creche = filtered["type"].isin(
        [
            "Crèche / EAJE",
            "Micro-crèche",
            "Relais Petite Enfance",
            "Accueil de loisirs (ALSH)",
        ]
    )

    niveaux_map = {
        "Maternelle": is_maternelle,
        "Elémentaire": is_elementaire,
        "Collège": is_college,
        "Lycée": is_lycee,
        "Crèche / Assistante Maternelle": is_creche,
    }

    mask = pd.Series(False, index=filtered.index)
    for niveau in config.classe_enfants:
        if niveau in niveaux_map:
            mask |= niveaux_map[niveau]

    filtered = filtered[mask]
    logging.info(
        f"build_ecoles_layer: {len(filtered)} schools remaining after type filtering."
    )

    if filtered.empty:
        return fg

    cluster = _build_generic_points_layer(
        filtered, icon="pencil", color="green", tooltip_cols=["name", "type"]
    )
    cluster.add_to(fg)
    return fg


def build_sante_layer(
    pois: gpd.GeoDataFrame, target_codgeos: Set[str], config: SearchCriterias
) -> flm.FeatureGroup:
    """Builds the map layer for health facilities using unified POIs."""
    fg = flm.FeatureGroup(name="Établissements de Santé")

    filtered = pois[
        (pois["category"] == "sante") & (pois["codgeo"].isin(target_codgeos))
    ].copy()

    besoin_sante_list = getattr(config, "besoin_sante", [])
    if filtered.empty or not besoin_sante_list:
        return fg

    # Mapping of needs to POI types (type column)
    sante_type_map = {
        "Hôpital": "Hôpital",
        "Maternité": "Maternité",
        "Soutien Psychologique": "Soutien Psychologique",
        "Dialyse": "Dialyse",
        "Maison de santé": "Maison de santé",
        "Addictologie": "Addictologie",
        "Santé maternelle et infantile (PMI)": "Santé maternelle et infantile (PMI)",
    }

    # Filter the types corresponding to the chosen needs
    allowed_types = [
        sante_type_map[b] for b in besoin_sante_list if b in sante_type_map
    ]
    if not allowed_types:
        return fg

    mask = filtered["type"].isin(allowed_types)

    if not mask.any():
        return fg

    cluster = _build_generic_points_layer(
        filtered[mask], icon="plus", color="blue", tooltip_cols=["name", "type"]
    )
    cluster.add_to(fg)
    return fg


def build_mairies_layer(
    pois: gpd.GeoDataFrame, target_codgeos: Set[str]
) -> flm.FeatureGroup:
    """Builds the map layer for mairies using unified POIs, rendering always-on dark yellow dots above the chloropleth."""
    fg = flm.FeatureGroup(name="Mairies")

    filtered = pois[
        (pois["category"] == "mairie") & (pois["codgeo"].isin(target_codgeos))
    ].copy()

    if filtered.empty:
        return fg

    for _, row in filtered.iterrows():
        popup_content = f"<b>{row['name']}</b><br>Type: {row['type']}"
        tooltip_content = f"<b>Mairie</b><br>{row['name']}"

        # SOTA: Render as Marker with DivIcon to force rendering on Leaflet markerPane (above choropleth overlayPane)
        marker_html = """
        <div style="background-color: #F5D819; border: 1.5px solid #1B4429; border-radius: 50%; width: 8px; height: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.4);"></div>
        """

        flm.Marker(
            location=[row["lat"], row["lon"]],
            icon=flm.features.DivIcon(
                icon_size=(11, 11),
                icon_anchor=(5, 5),
                html=marker_html,
            ),
            popup=flm.Popup(popup_content, max_width=300),
            tooltip=flm.Tooltip(tooltip_content, sticky=True),
        ).add_to(fg)

    return fg


def build_services_layer(
    pois: gpd.GeoDataFrame, target_codgeos: Set[str], config: SearchCriterias
) -> flm.FeatureGroup:
    """Builds the map layer for inclusion services using unified POIs."""
    fg = flm.FeatureGroup(name="Services d'inclusion")

    if not config.inc_services_selection:
        return fg

    filtered = pois[
        (pois["category"] == "incl_services") & (pois["codgeo"].isin(target_codgeos))
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
    if isinstance(config.inc_services_selection, list):
        slugs = []
        for item in config.inc_services_selection:
            slugs.append(item.code if hasattr(item, "code") else item)
        mask = filtered["type"].isin(slugs)
    elif isinstance(config.inc_services_selection, dict):
        # Backward compatibility if it's still a dict (keys are categories?)
        # Or keys are slugs? The old code used keys() as categories.
        # Let's assume keys are categories if it's a dict, but user said it's a list of slugs.
        # If it was a dict of slugs, we'd check keys.
        # But old code split type to get category.
        # Let's just try to match type against keys if it's a dict, or split.
        # Safest is to log warning and try exact match.
        logging.warning(
            "build_services_layer: inc_services_selection is a dict, using keys as slugs."
        )
        mask = filtered["type"].isin(config.inc_services_selection.keys())
    else:
        mask = pd.Series(False, index=filtered.index)

    logging.info(f"build_services_layer: {mask.sum()} services match the selection.")

    if not mask.any():
        return fg

    cluster = _build_generic_points_layer(
        filtered[mask], icon="heart", color="purple", tooltip_cols=["name", "type"]
    )
    cluster.add_to(fg)
    return fg
