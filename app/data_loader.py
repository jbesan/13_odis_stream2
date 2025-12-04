import streamlit as st
import pandas as pd
import geopandas as gpd
import shapely.wkb as wkb
import os
import yaml
import logging
from typing import Dict, Any, Tuple
import config as cfg
import copy

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def apply_demo_data_if_present(defaults: Dict[str, Any]) -> None:
    """
    Checks query params for 'demo' and updates defaults with demo scenario.
    """
    query_params = st.query_params
    if 'demo' in query_params:
        demo_id = query_params['demo']
        # If demo_id is empty or 'true', use default demo profile (or scenario 1)
        if not demo_id or demo_id == 'true':
            # Use defaults from config (already in defaults) or a specific scenario
            # Let's assume scenario 1 is the default demo
            scenario = cfg.DEMO_SCENARIOS.get("1", {})
        else:
            scenario = cfg.DEMO_SCENARIOS.get(demo_id, {})
            
        # Update defaults with scenario values
        for key, value in scenario.items():
            if key in defaults:
                defaults[key] = value
        
        st.toast(f"Mode Démo activé (Scénario {demo_id if demo_id != 'true' else 'Défaut'})", icon="ℹ️")

def session_states_init(defaults: Dict[str, Any]) -> None:
    """
    Initializes session state with defaults if not already set.
    """
    for key, value in defaults.items():
        # UI keys usually prefixed with 'ui_' in some apps, but here defaults seem to match state keys
        # Check if we need to map keys. 1_Accueil.py uses st.session_state.ui_nom for example.
        # But defaults has 'nom'.
        # Let's assume keys in defaults map to 'ui_' + key for widgets, or just key for state.
        # Looking at 1_Accueil.py: st.session_state.ui_nom = person_name_input
        # So 'nom' -> 'ui_nom'.
        
        # Map keys to UI state keys if needed
        ui_key = f"ui_{key}"
        if ui_key not in st.session_state:
            st.session_state[ui_key] = value
            
        # Also set the raw key if used elsewhere
        if key not in st.session_state:
            st.session_state[key] = value

@st.cache_data
def load_scores_config_as_df(config_path: str) -> pd.DataFrame:
    """Loads the scores configuration YAML as a DataFrame."""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    data = []
    scores_list = config.get('scores', [])
    for item in scores_list:
        data.append({
            'category': item.get('category'),
            'score': item.get('id'),
            'label': item.get('display', {}).get('name', item.get('id')),
            'description': item.get('display', {}).get('tooltip', ''),
            'weight': item.get('weight', 1.0),
            'min_bound': item.get('min_bound'),
            'max_bound': item.get('max_bound')
        })
    return pd.DataFrame(data)

@st.cache_data
def load_parquet_dataset(path: str, columns: list = None) -> pd.DataFrame:
    """Generic loader for parquet datasets with caching."""
    try:
        if columns:
            return pd.read_parquet(path, columns=columns)
        return pd.read_parquet(path)
    except FileNotFoundError:
        logger.error(f"File not found: {path}")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"Error loading {path}: {e}")
        return pd.DataFrame()

def get_pois_by_category(pois_df: pd.DataFrame, category: str) -> pd.DataFrame:
    """Filters POIs by category and returns a copy."""
    if pois_df.empty:
        return pd.DataFrame()
    return pois_df[pois_df['category'] == category].copy()

@st.cache_data
def init_datasets() -> Dict[str, Any]:
    """
    Initializes and loads all necessary datasets for the application.
    Returns a dictionary containing all loaded dataframes.
    """
    base_path = cfg.get_data_path()
    logger.info(f"Loading datasets from: {base_path}")

    # 1. Load Main ODIS Communes Data
    # Define columns to load to save memory
    columns_to_load = [
        'codgeo', 'libgeo', 'polygon', 'dep_code', 'reg_code', 'epci_code', 'epci_nom', 'codgeo_voisins',
        'population', 'bassin_de_vie', # Added bassin_de_vie
        'met_scaled', 'log_vac_scaled', 'inc_lien_social_score', 'inc_population_scaled', 'inc_pol_scaled', 'log_occup_scaled',
        'log_soc_inoc_scaled', 'edu_classes_ferm_scaled', 'edu_petite_enfance_scaled',
        'edu_maternelle_scaled', 'edu_elementaire_scaled', 'edu_college_scaled', 'edu_lycee_scaled',
        'sante_hopital_scaled', 'sante_maternite_scaled', 'sante_psy_scaled',
        'metiers_offres_top5'
    ]
    
    odis_path = os.path.join(base_path, cfg.ODIS_FILE)
    logger.info(f"Loading ODIS from: {odis_path}")
    
    try:
        odis = pd.read_parquet(odis_path, columns=columns_to_load)
        
        # Geometry processing
        if 'polygon' in odis.columns:
            odis['polygon'] = odis.polygon.apply(wkb.loads)
            odis = gpd.GeoDataFrame(odis, geometry='polygon', crs='EPSG:4326')
            odis.set_geometry('polygon', inplace=True)
            # Fix invalid geometries
            odis['polygon'] = odis.polygon.buffer(0)
            odis.polygon = odis.polygon.set_precision(grid_size=0.001, mode='valid_output')
            odis = odis[~odis.polygon.isna()]
            
            # Centroid
            odis['centroid'] = odis.to_crs(epsg=2154).centroid.to_crs(odis.crs)
        
        odis.set_index('codgeo', inplace=True)
        
        # Optimize types
        if 'population' in odis.columns:
            odis['population'] = odis['population'].astype('int32')
            
        float_cols = [c for c in columns_to_load if 'scaled' in c or 'score' in c]
        for col in float_cols:
            if col in odis.columns:
                odis[col] = odis[col].astype('float32')
                
        cat_cols = ['dep_code', 'reg_code', 'epci_code']
        for col in cat_cols:
            if col in odis.columns:
                odis[col] = odis[col].astype('category')

    except Exception as e:
        logger.error(f"Failed to load ODIS data: {e}")
        st.error("Erreur critique: Impossible de charger les données principales.")
        return {}

    # 2. Load POIs
    pois_path = os.path.join(base_path, cfg.POIS_FILE)
    logger.info(f"Loading POIs from: {pois_path}")
    pois_df = load_parquet_dataset(pois_path)
    
    # Split POIs
    # Map to expected variable names for compatibility
    annuaire_ecoles = get_pois_by_category(pois_df, 'education')
    annuaire_sante = get_pois_by_category(pois_df, 'sante')
    annuaire_inclusion = get_pois_by_category(pois_df, 'incl_services') # Note: check category name in build.py

    # Optimize POI Geometries (if needed for map display)
    # The app seems to use lat/lon columns directly for some maps, or geometry for others.
    # pois.parquet has lat/lon.
    # If we need geometry column:
    for df in [annuaire_ecoles, annuaire_sante, annuaire_inclusion]:
        if not df.empty and 'lat' in df.columns and 'lon' in df.columns:
             df['geometry'] = gpd.points_from_xy(df.lon, df.lat)
             df = gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')

    # 3. Load Referentiels (FAP, etc.)
    ref_path = os.path.join(base_path, cfg.REFERENTIELS_FILE)
    logger.info(f"Loading Referentiels from: {ref_path}")
    refs_df = load_parquet_dataset(ref_path)
    
    codfap_index = pd.DataFrame()
    if not refs_df.empty:
        fap_df = refs_df[refs_df['key'] == 'fap_codes']
        if not fap_df.empty:
            # Reconstruct expected format for FAP index
            # Expected: index=code, columns=[libelle, ...]
            # refs_df has 'code', 'label', 'metadata'
            codfap_index = fap_df[['code', 'label']].rename(columns={'label': 'libelle'}).set_index('code')

    # 4. Load Vertical Data
    bmo_vertical_path = os.path.join(base_path, cfg.REL_METIERS_FILE) # Was BMO_VERTICAL_FILE
    bmo_vertical = load_parquet_dataset(bmo_vertical_path)
    
    associations_path = os.path.join(base_path, cfg.REL_ASSOCIATIONS_FILE)
    associations_data = load_parquet_dataset(associations_path)

    # 5. Load Configs
    app_dir = os.path.dirname(os.path.abspath(__file__))
    scores_cat = load_scores_config_as_df(os.path.join(app_dir, cfg.SCORES_CAT_FILE))
    
    # Placeholders for missing/removed files (to avoid breaking unpacking)
    codformations_index = pd.DataFrame() # Removed formations.csv
    incl_index = pd.DataFrame() # Removed inclusion ref csv
    global_score_stats = {} # Was calculated in legacy loader, maybe needed?
    
    # Calculate global stats if needed
    if not odis.empty:
        # Example: Calculate quantiles for scores if used for relative coloring
        pass

    return {
        'odis': odis,
        'scores_cat': scores_cat,
        'codfap_index': codfap_index,
        'codformations_index': codformations_index,
        'annuaire_ecoles': annuaire_ecoles,
        'annuaire_sante': annuaire_sante,
        'annuaire_inclusion': annuaire_inclusion,
        'incl_index': incl_index,
        'associations_data': associations_data,
        'global_score_stats': global_score_stats,
        'pois': pois_df,
        'bmo_vertical': bmo_vertical
    }