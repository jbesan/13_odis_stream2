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
    # Store the defaults dict itself as 'demo_data' for reference
    if 'demo_data' not in st.session_state:
        st.session_state['demo_data'] = defaults

    # Key Mappings for UI widgets (Default Key -> UI Key)
    key_mapping = {
        'commune_actuelle': 'ui_commune',
        'departement_actuel': 'ui_departement'
    }

    for key, value in defaults.items():
        # Determine the UI key
        ui_key = key_mapping.get(key, f"ui_{key}")
        
        if ui_key not in st.session_state:
            st.session_state[ui_key] = value
            
        # Also set the raw key if used elsewhere
        if key not in st.session_state:
            st.session_state[key] = value

def ensure_data_initialized() -> None:
    """
    Ensures that the session state and datasets are initialized.
    This is useful for pages other than the main entry point.
    """
    # 1. Initialize Session State Defaults (Demo Data)
    if 'demo_data' not in st.session_state:
        defaults = copy.deepcopy(cfg.DEMO_DATA_DEFAULT)
        apply_demo_data_if_present(defaults)
        session_states_init(defaults)

    # 2. Initialize Datasets
    if 'app_data' not in st.session_state:
        st.session_state['app_data'] = init_datasets()

@st.cache_data
def load_scores_config_as_df(config_path: str) -> pd.DataFrame:
    """Loads the scores configuration YAML as a DataFrame."""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    data = []
    scores_list = config.get('scores', [])
    for item in scores_list:
        data.append({
            'cat': item.get('category'),
            'score': item.get('id'),
            'label': item.get('display', {}).get('name', item.get('id')),
            'description': item.get('display', {}).get('tooltip', ''),
            'weight': item.get('weight', 1.0),
            'min_bound': item.get('min_bound'),
            'max_bound': item.get('max_bound'),
            'score_affichage': item.get('display', {}).get('strong_point_text', ''),
            'incl_binome': item.get('include_in_binom', False),
            'metric': item.get('source_metric'),
            'computation': item.get('computation', 'live')
        })
    return pd.DataFrame(data)

@st.cache_data
def load_parquet_dataset(path: str, columns: list = None) -> pd.DataFrame:
    """Generic loader for parquet datasets with caching."""
    if columns:
        return pd.read_parquet(path, columns=columns)
    return pd.read_parquet(path)

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
        'population', 'bassin_de_vie', 'libelle_bassin_de_vie', # Added bassin_de_vie and label
        'met_scaled', 'log_vac_scaled', 'inc_lien_social_score', 'inc_population_scaled', 'inc_pol_scaled', 'log_occup_scaled',
        'log_soc_inoc_scaled', 'edu_classes_ferm_scaled', 'edu_petite_enfance_scaled',
        'edu_maternelle_scaled', 'edu_elementaire_scaled', 'edu_college_scaled', 'edu_lycee_scaled',
        'sante_hopital_scaled', 'sante_maternite_scaled', 'sante_psy_scaled'
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
        st.error(f"Erreur critique: Impossible de charger les données principales. Détails: {e}")
        raise e # Re-raise to see the error in Streamlit traceback

    # 2. Load POIs
    pois_path = os.path.join(base_path, cfg.POIS_FILE)
    logger.info(f"Loading POIs from: {pois_path}")
    pois_df = load_parquet_dataset(pois_path)
    
    # Split POIs
    # Map to expected variable names for compatibility
    # Note: We re-filter later after converting to GDF, so we skip detailed processing here
    # to avoid duplication.
    pass

    # Optimize POI Geometries (if needed for map display)
    # The app seems to use lat/lon columns directly for some maps, or geometry for others.
    # pois.parquet has lat/lon.
    
    # Convert main pois_df to GeoDataFrame
    if not pois_df.empty and 'lat' in pois_df.columns and 'lon' in pois_df.columns:
        pois_df['geometry'] = gpd.points_from_xy(pois_df.lon, pois_df.lat)
        pois_df = gpd.GeoDataFrame(pois_df, geometry='geometry', crs='EPSG:4326')

    # Clean Inclusion Slugs in main POIs DataFrame
    # 'type' column for inclusion services often contains stringified lists e.g. "['slug']"
    def clean_slug_global(val):
        try:
            if isinstance(val, str) and val.startswith('[') and val.endswith(']'):
                import ast
                l = ast.literal_eval(val)
                if isinstance(l, list) and len(l) > 0:
                    return l[0]
            return val
        except:
            return val

    if not pois_df.empty and 'category' in pois_df.columns:
        # Convert to object to avoid Categorical errors when setting new values
        if isinstance(pois_df['type'].dtype, pd.CategoricalDtype):
            pois_df['type'] = pois_df['type'].astype(str)
            
        mask_incl = pois_df['category'] == 'incl_services'
        if mask_incl.any():
            pois_df.loc[mask_incl, 'type'] = pois_df.loc[mask_incl, 'type'].apply(clean_slug_global)

    # Update subsets to be GeoDataFrames as well (slices of the GeoDataFrame)
    # Note: get_pois_by_category returns a copy, so we need to convert them if we want them to be GDFs
    # Or we can just re-filter from the now-GDF pois_df
    annuaire_ecoles = get_pois_by_category(pois_df, 'education')
    annuaire_sante = get_pois_by_category(pois_df, 'sante')
    annuaire_inclusion = get_pois_by_category(pois_df, 'incl_services') 
    
    # Re-apply renaming for inclusion if needed (since we re-filtered)
    if not annuaire_inclusion.empty:
        annuaire_inclusion = annuaire_inclusion.rename(columns={
            'type': 'categorie', 
            'name': 'label',
            'category': 'service'
        })
        if 'thematiques' not in annuaire_inclusion.columns:
             annuaire_inclusion['thematiques'] = annuaire_inclusion['categorie']
        if 'service' not in annuaire_inclusion.columns:
             annuaire_inclusion['service'] = 'Service d\'inclusion'

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
            codfap_index = fap_df[['code', 'label']].drop_duplicates(subset=['code']).set_index('code')

    # 4. Load Vertical Data
    bmo_vertical_path = os.path.join(base_path, cfg.REL_METIERS_FILE) # Was BMO_VERTICAL_FILE
    bmo_vertical = load_parquet_dataset(bmo_vertical_path)
    
    associations_path = os.path.join(base_path, cfg.REL_ASSOCIATIONS_FILE)
    associations_data = load_parquet_dataset(associations_path)

    formations_path = os.path.join(base_path, cfg.REL_FORMATIONS_FILE)
    formations_data = load_parquet_dataset(formations_path)

    # 5. Load Configs
    app_dir = os.path.dirname(os.path.abspath(__file__))
    scores_cat = load_scores_config_as_df(os.path.join(app_dir, cfg.SCORES_CAT_FILE))
    
    # Placeholders for missing/removed files (to avoid breaking unpacking)
    codformations_index = pd.DataFrame(columns=['label']) 
    codformations_index = pd.DataFrame(columns=['label']) 
    inclusion_services_index = pd.DataFrame(columns=['label'])
    
    if not refs_df.empty:
        form_ref_df = refs_df[refs_df['key'] == 'formation_codes']
        if not form_ref_df.empty:
            codformations_index = form_ref_df[['code', 'label']].set_index('code')
            
        # Load Inclusion Services Referentiel
        incl_ref_df = refs_df[refs_df['key'] == 'inclusion_services']
        if not incl_ref_df.empty:
            inclusion_services_index = incl_ref_df[['code', 'label']].set_index('code')

    # Build incl_index from annuaire_inclusion
    incl_index = pd.DataFrame()
    if not annuaire_inclusion.empty:
        # 'categorie' (mapped from 'type') is now already cleaned in pois_df
        annuaire_inclusion['slug'] = annuaire_inclusion['categorie']
        
        # Group by codgeo and aggregate slugs into a set
        # We use 'key' as the column name to match scoring.py expectation
        incl_index = annuaire_inclusion.groupby('codgeo')['slug'].apply(set).rename('key').to_frame()

    # Generate helper structures for UI
    depcom_df = odis[['libgeo', 'dep_code']].copy()
    coddep_set = sorted(odis['dep_code'].dropna().unique().tolist())

    # 6. Load Bassins de Vie Geometry
    bv_path = os.path.join(base_path, cfg.BV_FILE)
    logger.info(f"Loading BV Geo from: {bv_path}")
    bv_geo = load_parquet_dataset(bv_path)
    
    if not bv_geo.empty:
        # Ensure geometry
        if 'polygon' in bv_geo.columns:
             if isinstance(bv_geo['polygon'].iloc[0], bytes):
                 bv_geo['polygon'] = bv_geo['polygon'].apply(wkb.loads)
             bv_geo = gpd.GeoDataFrame(bv_geo, geometry='polygon', crs='EPSG:4326')
             
             # Fix invalid geometries
             bv_geo['polygon'] = bv_geo.polygon.buffer(0)
             
             # Calculate centroid
             bv_geo['centroid'] = bv_geo.to_crs(epsg=2154).centroid.to_crs(bv_geo.crs)
        
        # Set index
        # We prefer cfg.BV_CODE_COL, but fallback to 'bassin_de_vie'
        if cfg.BV_CODE_COL in bv_geo.columns:
            bv_geo.set_index(cfg.BV_CODE_COL, inplace=True)
        elif 'bassin_de_vie' in bv_geo.columns:
            bv_geo.set_index('bassin_de_vie', inplace=True)
            # Ensure consistency if config expects a different name
            if cfg.BV_CODE_COL != 'bassin_de_vie':
                bv_geo.index.name = cfg.BV_CODE_COL

    # 7. Generate Area Geometries (Departments & Regions)
    area_dfs = []
    if not odis.empty and isinstance(odis, gpd.GeoDataFrame):
        try:
            # Departments
            deps = odis.dissolve(by='dep_code')[['polygon']]
            deps['type'] = 'departement'
            deps = deps.reset_index().rename(columns={'dep_code': 'code'})
            area_dfs.append(deps)
            
            # Regions
            regs = odis.dissolve(by='reg_code')[['polygon']]
            regs['type'] = 'region'
            regs = regs.reset_index().rename(columns={'reg_code': 'code'})
            area_dfs.append(regs)
        except Exception as e:
            logger.error(f"Failed to generate area geometries: {e}")

    if area_dfs:
        area_geo = pd.concat(area_dfs)
        # Set MultiIndex (type, code)
        area_geo = area_geo.set_index(['type', 'code'])
    else:
        area_geo = gpd.GeoDataFrame()


    return {
        'odis': odis,
        'scores_cat': scores_cat,
        'codfap_index': codfap_index,
        'codformations_index': codformations_index,
        'inclusion_services_index': inclusion_services_index, # NEW
        'annuaire_ecoles': annuaire_ecoles,
        'annuaire_sante': annuaire_sante,
        'annuaire_inclusion': annuaire_inclusion,
        'incl_index': incl_index,
        'associations_data': associations_data,
        'formations_data': formations_data,
        'depcom_df': depcom_df,
        'coddep_set': coddep_set,
        'bv_geo': bv_geo,
        'area_geo': area_geo,
        'bmo_vertical': bmo_vertical,
        'pois': pois_df
    }