
import streamlit as st
import pandas as pd
import geopandas as gpd
import shapely.wkb as wkb
import os
import yaml
import logging
from typing import Dict, Any, List, Optional
import config as cfg
import copy
import gc

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
            'bdv_factor': item.get('bdv_factor', 0.0),
            'metric': item.get('source_metric'),
            'computation': item.get('computation', 'live'),
            'display_factor': item.get('display', {}).get('display_factor', 1.0),
            'unit': item.get('display', {}).get('unit', '')
        })
    return pd.DataFrame(data)

def apply_demo_data_if_present(defaults: Dict[str, Any]) -> None:
    """Checks query params for 'demo' and updates defaults with demo scenario."""
    query_params = st.query_params
    if 'demo' in query_params:
        demo_id = query_params['demo']
        if not demo_id or demo_id == 'true':
            scenario = cfg.DEMO_SCENARIOS.get("1", {})
        else:
            scenario = cfg.DEMO_SCENARIOS.get(demo_id, {})
            
        for key, value in scenario.items():
            if key in defaults:
                defaults[key] = value
        
        st.toast(f"Mode Démo activé (Scénario {demo_id if demo_id != 'true' else 'Défaut'})", icon="ℹ️")

def session_states_init(defaults: Dict[str, Any]) -> None:
    """Initializes session state with defaults if not already set."""
    if 'demo_data' not in st.session_state:
        st.session_state['demo_data'] = defaults

    key_mapping = {
        'commune_actuelle': 'ui_commune',
        'departement_actuel': 'ui_departement'
    }

    for key, value in defaults.items():
        ui_key = key_mapping.get(key, f"ui_{key}")
        if ui_key not in st.session_state:
            st.session_state[ui_key] = value
        if key not in st.session_state:
            st.session_state[key] = value

    # List inputs
    for key_base, key_in_defaults in [('ui_classe_enfant', 'classe_enfants'), ('ui_metiers_adult', 'codes_metiers'), ('ui_formations_adult', 'codes_formations')]:
        if key_in_defaults in defaults and isinstance(defaults[key_in_defaults], list):
             for i, val in enumerate(defaults[key_in_defaults]):
                 k = f"{key_base}_{i}"
                 if k not in st.session_state: st.session_state[k] = val

def ensure_data_initialized() -> None:
    """Ensures that the session state and datasets are initialized."""
    if 'demo_data' not in st.session_state:
        defaults = copy.deepcopy(cfg.DEMO_DATA_DEFAULT)
        apply_demo_data_if_present(defaults)
        session_states_init(defaults)

    if 'app_data' not in st.session_state:
        st.session_state['app_data'] = init_datasets()

    # --- RNA RAG Initialization (New) ---
    if 'rna_rag_service' not in st.session_state:
        try:
            from services.rna_rag import RNARagService
            st.session_state['rna_rag_service'] = RNARagService()
            st.session_state['rna_rag_status'] = "connected"
        except Exception as e:
            st.session_state['rna_rag_status'] = "failed"
            st.error(
                f"🚨 **Erreur de connexion BigQuery/Vertex AI** : {e}\n\n"
                "Le service de recherche sémantique (RAG) ne sera pas disponible. "
                "Assurez-vous d'avoir configuré vos identifiants GCP (gcloud auth application-default login)."
            )
            logger.error(f"RNARagService init failed: {e}")

    # Show warning if some data failed to load
    load_errors = st.session_state['app_data'].get('_load_errors', [])
    if load_errors:
        st.toast(
            f"⚠️ Attention: Certains jeux de données ({len(load_errors)}) n'ont pas pu être chargés. "
            f"Les résultats peuvent être incomplets.",
            icon="⚠️"
        )
        for err in load_errors:
            logger.error(f"Missing required data file: {err}")

    scores_path = os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE)
    st.session_state['app_data']['scores_cat'] = load_scores_config_as_df(scores_path)

def _load_parquet(path: str, columns: Optional[list] = None, error_list: Optional[list] = None) -> pd.DataFrame:
    """Internal non-cached loader with error tracking."""
    if not os.path.exists(path):
        fname = os.path.basename(path)
        logger.error(f"File not found: {path} (Critical for this feature)")
        if error_list is not None:
            error_list.append(fname)
        return pd.DataFrame()
    if columns:
        return pd.read_parquet(path, columns=columns)
    return pd.read_parquet(path)

@st.cache_resource
def load_parquet_dataset(path: str, columns: Optional[list] = None) -> pd.DataFrame:
    """Generic loader for parquet datasets with caching."""
    return _load_parquet(path, columns)

def _load_ccas(base_path: str) -> pd.DataFrame:
    """Internal non-cached CCAS loader."""
    path = os.path.join(base_path, cfg.CCAS_FILE)
    if os.path.exists(path):
         return pd.read_parquet(path)
    return pd.DataFrame()

@st.cache_resource
def load_ccas_structures(base_path: str) -> pd.DataFrame:
    """Loads CCAS structures."""
    return _load_ccas(base_path)

def get_pois_by_category(pois_df: pd.DataFrame, category: str) -> pd.DataFrame:
    """Filters POIs by category and returns a copy."""
    if pois_df.empty:
        return pd.DataFrame()
    return pois_df[pois_df['category'] == category].copy()

def load_all_data_raw() -> Dict[str, Any]:
    """
    Initializes and loads all necessary datasets for the application.
    (Non-cached version for MCP usage)
    """
    base_path = cfg.get_data_path()
    logger.info(f"Loading datasets from: {base_path}")

    # 1. Load Main ODIS Communes Data
    odis_path = os.path.join(base_path, cfg.ODIS_FILE)
    
    try:
        # Load all columns first to identify what we need
        temp_df = pd.read_parquet(odis_path)
        all_cols = temp_df.columns.tolist()
        del temp_df
        gc.collect()
        
        essential_cols = {
            'codgeo', 'polygon', 'dep_code', 'reg_code', 'epci_code', 'epci_nom',
            'population', 'bassin_de_vie',
            'youth_growth_rate', 'workclass_growth_rate',
            'count_hopital', 'count_maternite', 'count_psy',
            'log_priv_vacant_plus_2ans', 'log_total', # For vacancy tests
            'nb_stops_bus', 'nb_stops_tram', 'nb_stops_metro', 'nb_stops_train', 'nb_stops_total'
        }
        
        columns_to_load = {
            c for c in all_cols 
            if c in essential_cols or c.endswith('_scaled')
        }

        # Load metrics from config (Robustness)
        try:
             scores_path = os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE)
             if os.path.exists(scores_path):
                 sc_df = load_scores_config_as_df(scores_path)
                 raw_metrics = sc_df['metric'].dropna().unique().tolist()
                 for m in raw_metrics:
                     if m in all_cols:
                         columns_to_load.add(m)
        except Exception as e:
            logger.warning(f"Could not load raw metrics from config: {e}")

        odis = pd.read_parquet(odis_path, columns=list(columns_to_load))
        
        # Geometry processing
        if 'polygon' in odis.columns:
            odis['polygon'] = odis.polygon.apply(wkb.loads)
            odis = gpd.GeoDataFrame(odis, geometry='polygon', crs='EPSG:2154')
            odis.set_geometry('polygon', inplace=True)
            # Try to believe ETL data is valid. Only minimal fix.
            # odis['polygon'] = odis.polygon.buffer(0) 
            
            if 'centroid' not in odis.columns:
                 odis['centroid'] = odis.geometry.centroid
        
        odis.set_index('codgeo', inplace=True)
        
        # Optimize types
        if 'population' in odis.columns:
            odis['population'] = odis['population'].astype('int32')
            
        float_cols = [c for c in columns_to_load if 'scaled' in c or 'score' in c]
        for col in float_cols:
            if col in odis.columns:
                odis[col] = odis[col].astype('float32')
        
        for col in ['dep_code', 'reg_code', 'epci_code', 'bassin_de_vie']:
            if col in odis.columns:
                odis[col] = odis[col].astype(str)

    except Exception as e:
        logger.error(f"Failed to load ODIS data: {e}")
        raise e 

    # 2. Load POIs
    pois_path = os.path.join(base_path, cfg.POIS_FILE)
    pois_df = _load_parquet(pois_path)
    if not pois_df.empty and 'lat' in pois_df.columns and 'lon' in pois_df.columns:
        pois_df['geometry'] = gpd.points_from_xy(pois_df.lon, pois_df.lat)
        pois_df = gpd.GeoDataFrame(pois_df, geometry='geometry', crs='EPSG:4326')

    annuaire_ecoles = get_pois_by_category(pois_df, 'education')
    annuaire_sante = get_pois_by_category(pois_df, 'sante')
    annuaire_inclusion = get_pois_by_category(pois_df, 'incl_services') 
    
    if not annuaire_inclusion.empty:
        annuaire_inclusion = annuaire_inclusion.rename(columns={
            'type': 'categorie', 'name': 'label', 'category': 'service'
        })
        annuaire_inclusion['thematiques'] = annuaire_inclusion.get('categorie', '')
        if 'service' not in annuaire_inclusion.columns:
             annuaire_inclusion['service'] = 'Service d\'inclusion'

    # 3. Load Referentiels
    ref_path = os.path.join(base_path, cfg.REFERENTIELS_FILE)
    refs_df = _load_parquet(ref_path)

    # Extract Lookups
    commune_names = {}
    bv_names = {}
    regions_names = {}
    departements_names = {}
    dept_details = {}

    if not refs_df.empty:
         c_ref = refs_df[refs_df['key'] == 'communes']
         if not c_ref.empty: commune_names = c_ref.set_index('code')['label'].to_dict()
         
         bv_ref = refs_df[refs_df['key'] == 'bassins_de_vie']
         if not bv_ref.empty: bv_names = bv_ref.set_index('code')['label'].to_dict()

         reg_ref = refs_df[refs_df['key'] == 'regions']
         if not reg_ref.empty: regions_names = reg_ref.set_index('code')['label'].to_dict()

         dep_ref = refs_df[refs_df['key'] == 'departements']
         if not dep_ref.empty:
             departements_names = dep_ref.set_index('code')['label'].to_dict()
             # Only include reg_code if available
             cols_to_dict = ['label']
             if 'reg_code' in dep_ref.columns:
                 cols_to_dict.append('reg_code')
             dept_details = dep_ref.set_index('code')[cols_to_dict].to_dict(orient='index')

    if 'libgeo' not in odis.columns:
        odis['libgeo'] = odis.index.map(commune_names)
        odis['libgeo'] = odis['libgeo'].fillna(odis.index.to_series())

    if 'bassin_de_vie' in odis.columns:
        odis['libelle_bassin_de_vie'] = odis['bassin_de_vie'].astype(str).map(bv_names)
        odis['libelle_bassin_de_vie'] = odis['libelle_bassin_de_vie'].fillna(odis['bassin_de_vie'])

    rome_index = pd.DataFrame()
    codformations_index = pd.DataFrame(columns=['label']) 
    inclusion_services_index = pd.DataFrame(columns=['label'])

    if not refs_df.empty:
        rome_ref_df = refs_df[refs_df['key'] == 'rome_codes']
        if not rome_ref_df.empty:
            rome_index = rome_ref_df[['code', 'label']].drop_duplicates(subset=['code']).set_index('code')
        else:
            rome_index = pd.DataFrame(columns=['label'])
            
        form_ref_df = refs_df[refs_df['key'] == 'formation_codes']
        if not form_ref_df.empty:
            codformations_index = form_ref_df[['code', 'label']].set_index('code')
            
        incl_ref_df = refs_df[refs_df['key'] == 'inclusion_services']
        if not incl_ref_df.empty:
            inclusion_services_index = incl_ref_df[['code', 'label']].set_index('code')
            
        waldec_ref_df = refs_df[refs_df['key'] == 'waldec_codes']
        if not waldec_ref_df.empty:
            waldec_index = waldec_ref_df[['code', 'label']].set_index('code')
        else:
            waldec_index = pd.DataFrame(columns=['label'])
    else:
        waldec_index = pd.DataFrame(columns=['label'])

    incl_index = pd.DataFrame()
    if not annuaire_inclusion.empty:
        annuaire_inclusion['slug'] = annuaire_inclusion['categorie']
        incl_index = annuaire_inclusion.groupby('codgeo', observed=False)['slug'].apply(set).rename('key').to_frame()

    # Safe subsetting of odis columns
    depcom_cols = [c for c in ['libgeo', 'dep_code'] if c in odis.columns]
    depcom_df = odis[depcom_cols].copy()
    
    coddep_set = sorted(odis['dep_code'].dropna().unique().tolist()) if 'dep_code' in odis.columns else []

    # 4. Vertical Data
    # List to track all loading errors in this session
    load_errors = []

    # bmo_vertical = _load_parquet(os.path.join(base_path, cfg.AGG_METIERS_FILE), error_list=load_errors) # Deprecated
    bmo_vertical = pd.DataFrame()

    live_jobs_data = _load_parquet(os.path.join(base_path, cfg.LIVE_JOBS_FILE), error_list=load_errors)
    associations_data = _load_parquet(os.path.join(base_path, cfg.AGG_ASSOCIATIONS_FILE), error_list=load_errors)
    refugee_associations_data = _load_parquet(os.path.join(base_path, cfg.REFUGEE_ASSOCIATIONS_FILE), error_list=load_errors)
    formations_data = _load_parquet(os.path.join(base_path, cfg.AGG_FORMATIONS_FILE), error_list=load_errors)
    
    if not formations_data.empty and 'formation_code' in formations_data.columns:
        formations_data['formation_code'] = formations_data['formation_code'].astype(str).str.replace(r'\.0$', '', regex=True)

    structures_ccas = _load_ccas(base_path)
    
    siae_jobs_data = _load_parquet(os.path.join(base_path, cfg.SIAE_JOBS_FILE), error_list=load_errors)
    
    scores_cat = load_scores_config_as_df(os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE))

    # 5. Bassins de Vie Geo
    bv_path = os.path.join(base_path, cfg.BV_FILE)
    bv_geo = _load_parquet(bv_path, error_list=load_errors)
    if not bv_geo.empty:
        if 'polygon' in bv_geo.columns:
             if isinstance(bv_geo['polygon'].iloc[0], bytes):
                 bv_geo['polygon'] = bv_geo['polygon'].apply(wkb.loads)
             bv_geo = gpd.GeoDataFrame(bv_geo, geometry='polygon', crs=cfg.PROJECTED_CRS)
             # bv_geo['polygon'] = bv_geo.polygon.buffer(0)
             if 'centroid' not in bv_geo.columns:
                 bv_geo['centroid'] = bv_geo.geometry.centroid

        key_col = cfg.BV_CODE_COL if cfg.BV_CODE_COL in bv_geo.columns else 'bassin_de_vie'
        if key_col in bv_geo.columns:
            bv_geo.set_index(key_col, inplace=True)
            if cfg.BV_CODE_COL != 'bassin_de_vie': bv_geo.index.name = cfg.BV_CODE_COL

        if 'libgeo' not in bv_geo.columns:
            bv_geo['libgeo'] = bv_geo.index.astype(str).map(bv_names)
            bv_geo['libgeo'] = bv_geo['libgeo'].fillna(bv_geo.index.to_series())

    # 6. Area Geo
    area_dfs = []
    if not odis.empty and isinstance(odis, gpd.GeoDataFrame):
        try:
            deps = odis.dissolve(by='dep_code')[['polygon']]
            deps['type'] = 'departement'
            deps = deps.reset_index().rename(columns={'dep_code': 'code'})
            area_dfs.append(deps)
            
            regs = odis.dissolve(by='reg_code')[['polygon']]
            regs['type'] = 'region'
            regs = regs.reset_index().rename(columns={'reg_code': 'code'})
            area_dfs.append(regs)
        except Exception as e:
            logger.error(f"Failed to generate area geometries: {e}")

    area_geo = pd.concat(area_dfs).set_index(['type', 'code']) if area_dfs else gpd.GeoDataFrame()
    del area_dfs
    gc.collect()

    return {
        'odis': odis,
        'scores_cat': scores_cat,
        'rome_index': rome_index,
        'codformations_index': codformations_index,
        'inclusion_services_index': inclusion_services_index,
        'annuaire_ecoles': annuaire_ecoles,
        'annuaire_sante': annuaire_sante,
        'annuaire_inclusion': annuaire_inclusion,
        'incl_index': incl_index,
        'associations_data': associations_data,
        'formations_data': formations_data,
        'depcom_df': depcom_df,
        'coddep_set': coddep_set,
        'bv_geo': bv_geo,
        'bv_data': bv_geo,
        'area_geo': area_geo,
        'bmo_vertical': bmo_vertical,
        'live_jobs_data': live_jobs_data,
        'structures_ccas': structures_ccas,
        'pois': pois_df,
        'referentiels_raw': refs_df,
        'regions_names': regions_names,
        'departements_names': departements_names,
        'dept_details': dept_details,
        'refugee_associations_data': refugee_associations_data,
        'waldec_index': waldec_index,
        'siae_jobs_data': siae_jobs_data,
        '_load_errors': load_errors
    }

@st.cache_resource
def init_datasets() -> Dict[str, Any]:
    """Cached wrapper for Streamlit."""
    return load_all_data_raw()
