
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
            'unit': item.get('display', {}).get('unit', ''),
            'scaling_type': item.get('scaling_type', 'linear'),
            'mu': item.get('mu'),
            'sigma': item.get('sigma')
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

def apply_search_criteria_to_ui(criteria: Any) -> None:
    """
    Maps a SearchCriterias model (from AI extraction) to the ui_ session states.
    Uses dynamic iteration over model fields to ensure 100% parity with UI variables.
    """
    if not criteria:
        return

    # Convert model to dict - ONLY include fields that were explicitly set by the AI
    # This prevents default values (like weights=0.0) from overwriting profile values.
    crit_dict = criteria.model_dump(exclude_unset=True) if hasattr(criteria, 'model_dump') else criteria.__dict__

    # 1. Generic flattening (extract code/label from CriteriaItems)
    def flatten_val(key, v):
        if isinstance(v, dict) and 'code' in v and 'label' in v:
            # We want the label for commune input, but codes for everything else
            return v['label'] if key == 'commune_actuelle' else v['code']
        if isinstance(v, list):
            return [flatten_val(key, item) for item in v]
        return v

    flat_crit = {}
    for k, v in crit_dict.items():
        if v is not None:
            flat_crit[k] = flatten_val(k, v)

    # 2. Iterate and set all ui_ dynamically
    for k, v in flat_crit.items():
        st.session_state[f"ui_{k}"] = v
        
    # Mapping specific values that are used directly in component initialization
    if 'commune_actuelle' in flat_crit:
        st.session_state['ui_commune'] = flat_crit['commune_actuelle']
        # Try to infer department from the original code ONLY if it looks like an INSEE code (5 digits)
        code = criteria.commune_actuelle.code
        if code and len(code) == 5 and code.isdigit():
            st.session_state['ui_departement'] = code[:2]
        elif code and len(code) == 5 and code[:2].isdigit(): # Handle 2A/2B
            st.session_state['ui_departement'] = code[:2]

    # Handle 'sante' field properly
    if 'besoin_sante' in flat_crit:
        st.session_state['ui_besoin_sante'] = flat_crit['besoin_sante'] or "Aucun"
    elif 'sante' in flat_crit:
        st.session_state['ui_besoin_sante'] = flat_crit['sante'] or "Aucun"

    # 3. Handle specific lists mapping that have index suffixes (e.g. metiers_adult_0)
    for key_base, crit_key in [('ui_classe_enfant', 'classe_enfants'), 
                               ('ui_metiers_adult', 'codes_metiers'), 
                               ('ui_formations_adult', 'codes_formations')]:
        if crit_key in flat_crit and isinstance(flat_crit[crit_key], list):
             for i, val in enumerate(flat_crit[crit_key]):
                 st.session_state[f"{key_base}_{i}"] = val

    # 4. Handle Mobility Form special case (which deviates from 1-to-1 parsing)
    loc_area = flat_crit.get('loc_search_area')
    loc_code = flat_crit.get('loc_search_code') or [] # Now a list
    
    if loc_area == 'france':
        st.session_state['ui_france_search'] = True
        st.session_state['ui_region_search'] = False
    elif loc_area == 'region':
        st.session_state['ui_france_search'] = False
        st.session_state['ui_region_search'] = True
        if loc_code:
            st.session_state['ui_mobility_region'] = loc_code[0] if isinstance(loc_code, list) else loc_code
    elif loc_area == 'departement' and loc_code:
        st.session_state['ui_france_search'] = False
        st.session_state['ui_region_search'] = False
        
        # loc_code is a list of department codes
        st.session_state['ui_mobility_dept'] = loc_code if isinstance(loc_code, list) else [loc_code]
        
        # Infer region from the first department
        first_dept = loc_code[0] if isinstance(loc_code, list) else loc_code
        app_data = st.session_state.get('app_data', {})
        dept_details = app_data.get('dept_details', {})
        reg_code = dept_details.get(first_dept, {}).get('reg_code')
        if reg_code:
            st.session_state['ui_mobility_region'] = reg_code
        
    # 5. Handle notes_qualitatives (UI expects a string, model provides a list of strings)
    if 'notes_qualitatives' in flat_crit:
        val = flat_crit['notes_qualitatives']
        if isinstance(val, list):
            st.session_state['ui_notes_qualitatives'] = "\n".join(val)
        else:
            st.session_state['ui_notes_qualitatives'] = str(val) if val else ""

    # 6. Handle Weight Profile & Weights (F-15 & User Feedback)
    # If a profile is selected, we MUST set the individual ui_poids_... keys
    # because Streamlit widgets don't trigger on_change when set programmatically.
    profile = flat_crit.get('weight_profile')
    if profile in cfg.WEIGHT_PROFILES:
        profile_weights = cfg.WEIGHT_PROFILES[profile]
        for pw_key, pw_val in profile_weights.items():
            # Profiles in config are already 0-100
            st.session_state[f"ui_{pw_key}"] = pw_val
    
    # Finally, if any explicit weights were extracted (higher priority), apply them
    # Now unified: everything is 0.0-1.0
    has_custom_weights = False
    for k, v in flat_crit.items():
        if k.startswith('poids_'):
            st.session_state[f"ui_{k}"] = float(v)
            has_custom_weights = True
            
    # If custom weights are present, activate the "Expert Weights" toggle
    if has_custom_weights:
        st.session_state['ui_expert_weights'] = True

    # 7. Town Size Reverse Lookup (Sync Radio Button with Mu/Sigma)
    target_pop = flat_crit.get('target_population')
    target_sigma = flat_crit.get('target_population_sigma')
    if target_pop and target_sigma:
        for label, mapping in cfg.CITY_SIZE_MAPPING.items():
            if mapping['mu'] == target_pop and mapping['sigma'] == target_sigma:
                st.session_state["ui_target_city_size_label"] = label
                break

    # 8. Inclusion Services Sync (Checkboxes + Multiselect)
    # inc_services_add_selection in flat_crit is a list of CODES
    inc_codes = flat_crit.get('inc_services_add_selection', [])
    if inc_codes:
        # Standard list for the composite key
        st.session_state['ui_inc_services_add_selection'] = inc_codes
        
        # Checkboxes sync
        checkbox_slugs = set(cfg.INC_SERVICES_CHECKBOX_MAPPING.keys())
        for slug in checkbox_slugs:
            cb_key = f"ui_cb_inc_{slug.replace('-', '_')}"
            st.session_state[cb_key] = slug in inc_codes
            
        # Multiselect sync (Labels)
        inclusion_index = app_data.get('inclusion_services_index', pd.DataFrame())
        multi_labels = []
        if not inclusion_index.empty:
            for c in inc_codes:
                if c in inclusion_index.index and c not in checkbox_slugs:
                    multi_labels.append(inclusion_index.loc[c, 'label'])
        st.session_state['ui_inc_services_multi_only'] = multi_labels

    # 9. Inclusion Associations Sync
    asso_codes = flat_crit.get('inc_asso_add_selection', [])
    if asso_codes:
        st.session_state['ui_inc_asso_add_selection_raw'] = asso_codes

def ensure_data_initialized() -> None:
    """Ensures that the session state and datasets are initialized."""
    # Force re-initialization IF a demo parameter is present in query string
    # This allows Deep-linking scenarios like ?demo=3 to work even if already on the page.
    force_demo_refresh = 'demo' in st.query_params
    
    if 'demo_data' not in st.session_state or force_demo_refresh:
        defaults = copy.deepcopy(cfg.DEMO_DATA_DEFAULT)
        # Only overwrite defaults if demo is in query params
        apply_demo_data_if_present(defaults)
        st.session_state['demo_data'] = defaults
        
    # Always ensure session states are initialized if missing
    session_states_init(st.session_state['demo_data'])
    
    # If we just loaded a demo, or on first run, we dispatch the model to the UI
    # This is the "Model-First" injection point.
    if force_demo_refresh:
        from core.models import SearchCriterias
        try:
            # We use SearchCriterias to benefit from its validators (strings -> CriteriaItems)
            criteria = SearchCriterias(**st.session_state['demo_data'])
            apply_search_criteria_to_ui(criteria)
        except Exception as e:
            logger.error(f"Failed to apply demo via SearchCriterias: {e}")
            # Fallback to manual init if model fails

    # Ensure global cache is warm
    get_app_data()

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

    # --- J'Accueille BigQuery Fetch (Managed via cache) ---
    # We no longer store it in session_state here, as it's fetched via load_all_data_raw -> fetch_jaccueille_data_bq (cached)
    pass

def _fetch_jaccueille_data_bq_logic() -> pd.DataFrame:
    """
    Internal logic to fetch J'Accueille host counts from BigQuery.
    Implements a persistent local cache to avoid redundant BQ hits.
    """
    import time
    # Use a private data folder to avoid tracking in git
    cache_dir = os.path.join(cfg.PROJECT_ROOT, "data_private")
    cache_path = os.path.join(cache_dir, "jaccueille_hosts_cache.parquet")
    ttl_seconds = 30 * 24 * 3600  # 30 days (1 month)


    # 1. Try to load from persistent local cache
    if os.path.exists(cache_path):
        mtime = os.path.getmtime(cache_path)
        if (time.time() - mtime) < ttl_seconds:
            try:
                # logger.info("📂 [J'ACCUEILLE] Loading host counts from local cache...")
                return pd.read_parquet(cache_path, engine='fastparquet')
            except Exception as e:
                logger.warning(f"Failed to read J'Accueille cache: {e}")

    # 2. Fetch from BigQuery if cache is missing or stale
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project="odis-stream2")
        query = "SELECT bassin_de_vie, heb_accueillants_count FROM `odis-stream2.jaccueille.jaccueille_accueillants_bdv`"
        logger.info("📡 [J'ACCUEILLE] Fetching host counts from BigQuery (Cache stale or missing)...")
        df_jacc = client.query(query).to_dataframe()
        
        # Save to local cache for future use
        if not df_jacc.empty:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                df_jacc.to_parquet(cache_path, engine='fastparquet')
            except Exception as e:
                logger.warning(f"Failed to save J'Accueille cache: {e}")
                
        return df_jacc
    except Exception as e:
        logger.error(f"J'Accueille BQ fetch failed: {e}")
        return pd.DataFrame(columns=['bassin_de_vie', 'heb_accueillants_count'])


@st.cache_data(ttl=3600)
def fetch_jaccueille_data_bq_cached() -> pd.DataFrame:
    """Cached version of J'Accueille fetch for Streamlit."""
    return _fetch_jaccueille_data_bq_logic()

def fetch_jaccueille_data_bq() -> pd.DataFrame:
    """
    Fetches J'Accueille host counts, using Streamlit cache if context is available,
    otherwise fetching directly (useful for background threads or MCP).
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx():
            return fetch_jaccueille_data_bq_cached()
    except ImportError:
        pass
    return _fetch_jaccueille_data_bq_logic()

def _enrich_waldec_index(waldec_index: pd.DataFrame, associations_data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Enriches the WALDEC index with association counts and returns both the full 
    sorted index and the top items list.
    """
    if waldec_index.empty or associations_data.empty:
        return waldec_index, waldec_index.head(500)

    # 1. Aggregate counts by id_waldec
    topo_assos = associations_data.groupby('id_waldec')['count'].sum().to_frame()
    
    # 2. Join to waldec_index
    enriched_waldec = waldec_index.copy()
    enriched_waldec = enriched_waldec.join(topo_assos, how='left')
    enriched_waldec['count'] = enriched_waldec['count'].fillna(0).astype(int)
    
    # 3. Sort by count desc, then by label alpha
    enriched_waldec = enriched_waldec.sort_values(by=['count', 'label'], ascending=[False, True])
    
    # 4. Create Top 500 (Larger than ROME as associations are more diverse)
    waldec_top_index = enriched_waldec.head(500)
    
    return enriched_waldec, waldec_top_index

def _enrich_rome_index(rome_index: pd.DataFrame, live_jobs_data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Enriches the ROME index with job offer counts and returns both the full 
    sorted index and the top items list.
    """
    if rome_index.empty or live_jobs_data.empty:
        return rome_index, rome_index.head(200)

    # 1. Aggregate total_postes by romeCode
    # Note: romeCode and romeLibelle are guaranteed in live_jobs_data as per user snippet
    jobs_top = live_jobs_data.groupby('romeCode')['total_postes'].sum().to_frame()
    
    # 2. Join to rome_index
    enriched_rome = rome_index.copy()
    enriched_rome = enriched_rome.join(jobs_top, how='left')
    enriched_rome['total_postes'] = enriched_rome['total_postes'].fillna(0)
    
    # 3. Sort by total_postes desc, then by label alpha
    enriched_rome = enriched_rome.sort_values(by=['total_postes', 'label'], ascending=[False, True])
    
    # 4. Create Top 200
    rome_top_index = enriched_rome.head(200)
    
    return enriched_rome, rome_top_index

def _load_parquet(path: str, columns: Optional[list] = None, error_list: Optional[list] = None) -> pd.DataFrame:
    """Internal non-cached loader with error tracking."""
    if not os.path.exists(path):
        fname = os.path.basename(path)
        logger.error(f"File not found: {path} (Critical for this feature)")
        if error_list is not None:
            error_list.append(fname)
        return pd.DataFrame()
    if columns:
        return pd.read_parquet(path, engine='fastparquet', columns=columns)
    return pd.read_parquet(path, engine='fastparquet')

@st.cache_resource
def load_parquet_dataset(path: str, columns: Optional[list] = None) -> pd.DataFrame:
    """Generic loader for parquet datasets with caching."""
    return _load_parquet(path, columns)

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

    print("################### DATA RELOADED ###################")
    base_path = cfg.get_data_path()
    logger.info(f"Loading datasets from: {base_path}")

    # 1. Load Main ODIS Communes Data
    odis_path = os.path.join(base_path, cfg.ODIS_FILE)
    
    try:
        # Load all columns first to identify what we need
        temp_df = pd.read_parquet(odis_path, engine='fastparquet')
        all_cols = temp_df.columns.tolist()
        del temp_df
        
        essential_cols = {
            'codgeo', 'polygon', 'dep_code', 'reg_code', 'epci_code', 'epci_nom',
            'population', 'bassin_de_vie',
            'centroid_lon', 'centroid_lat',
            'youth_growth_rate', 'workclass_growth_rate',
            'count_hopital', 'count_maternite', 'count_psy',
            'log_priv_vacant_plus_2ans', 'log_total', # For vacancy tests
            'nb_stops_bus', 'nb_stops_tram', 'nb_stops_metro', 'nb_stops_train', 'nb_stops_total'
        }
        
        columns_to_load = {
            c for c in all_cols 
            if c in essential_cols or c.endswith('_scaled') or c.startswith('inc_rna_') or c == 'inc_asso_refug_count'
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

        odis = pd.read_parquet(odis_path, engine='fastparquet', columns=list(columns_to_load))
        
        # Geometry processing (JIT DEHYDRATION: Store as raw WKB bytes mapping)
        # This prevents massive memory bloat by not instantiating thousands of Shapely objects at startup.
        odis_geo = pd.Series(dtype='object')
        if 'polygon' in odis.columns:
            logger.info("Dehydrating geometries to odis_geo (Lazy Load pattern)...")
            odis_geo = odis[['codgeo', 'polygon']].set_index('codgeo')['polygon']
            
            # Remove heavy WKB columns from the main odis dataframe to save RAM
            odis.drop(columns=['polygon'], inplace=True)
            if 'centroid' in odis.columns:
                odis.drop(columns=['centroid'], inplace=True)
        
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


    live_jobs_data = _load_parquet(os.path.join(base_path, cfg.LIVE_JOBS_FILE), error_list=load_errors)
    associations_data = _load_parquet(os.path.join(base_path, cfg.AGG_ASSOCIATIONS_FILE), error_list=load_errors)
    refugee_associations_data = _load_parquet(os.path.join(base_path, cfg.REFUGEE_ASSOCIATIONS_FILE), error_list=load_errors)
    formations_data = _load_parquet(os.path.join(base_path, cfg.AGG_FORMATIONS_FILE), error_list=load_errors)
    
    if not formations_data.empty and 'formation_code' in formations_data.columns:
        formations_data['formation_code'] = formations_data['formation_code'].astype(str).str.replace(r'\.0$', '', regex=True)

    structures_ccas = _load_parquet(os.path.join(base_path, cfg.CCAS_FILE), error_list=load_errors)
    
    siae_jobs_data = _load_parquet(os.path.join(base_path, cfg.SIAE_JOBS_FILE), error_list=load_errors)
    
    scores_cat = load_scores_config_as_df(os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE))

    # --- Enrichment: Index Sorting & Truncation ---
    rome_index, rome_top_index = _enrich_rome_index(rome_index, live_jobs_data)
    waldec_index, waldec_top_index = _enrich_waldec_index(waldec_index, associations_data)

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

        # Drop redundant columns that are NOT needed for scoring and cause overhead when merged as _bdv
        cols_to_drop = ['polygon', 'centroid', 'libgeo']
        bv_geo = bv_geo.drop(columns=[c for c in cols_to_drop if c in bv_geo.columns], errors='ignore')

        if 'libgeo' not in bv_geo.columns:
            # We still might want to map the name to a column called 'libelle_bassin_de_vie' 
            # if we didn't have it, but usually it's already in the main odis dataset.
            pass

    # --- 5b. Enrich with dynamic J'Accueille data (Cached) ---
    df_jacc = fetch_jaccueille_data_bq()

    if df_jacc is not None and not df_jacc.empty:
        # Join to BV
        if not bv_geo.empty:
            bv_geo = bv_geo.reset_index()
            df_jacc['bassin_de_vie'] = df_jacc['bassin_de_vie'].astype(str)
            bv_geo = bv_geo.merge(df_jacc, on='bassin_de_vie', how='left')
            bv_geo['heb_accueillants_count'] = bv_geo['heb_accueillants_count'].fillna(0)
            # Re-calculate heb_jaccueille_score dynamically
            bv_geo['heb_jaccueille_score'] = (bv_geo['heb_accueillants_count'] > 0).astype(float)
            bv_geo = bv_geo.set_index('bassin_de_vie')
            
        # Join to ODIS (for detailed city display)
        if not odis.empty:
            odis = odis.reset_index()
            # We already have bassin_de_vie in odis
            odis = odis.merge(df_jacc, on='bassin_de_vie', how='left')
            odis['heb_accueillants_count'] = odis['heb_accueillants_count'].fillna(0)
            odis['heb_jaccueille_score'] = (odis['heb_accueillants_count'] > 0).astype(float)
            odis = odis.set_index('codgeo')


    return {
        'odis': odis,
        'odis_geo': odis_geo,
        'scores_cat': scores_cat,
        'rome_index': rome_index,
        'rome_top_index': rome_top_index,
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

        'live_jobs_data': live_jobs_data,
        'structures_ccas': structures_ccas,
        'pois': pois_df,
        'referentiels_raw': refs_df,
        'regions_names': regions_names,
        'departements_names': departements_names,
        'dept_details': dept_details,
        'refugee_associations_data': refugee_associations_data,
        'waldec_index': waldec_index,
        'waldec_top_index': waldec_top_index,
        'siae_jobs_data': siae_jobs_data,
        '_load_errors': load_errors
    }

def get_data_mtime() -> float:
    """Returns the maximum mtime of critical data files to invalidate cache."""
    base_path = cfg.get_data_path()
    critical_files = [
        os.path.join(base_path, cfg.ODIS_FILE),
        os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE)
    ]
    mtimes = []
    for f in critical_files:
        if os.path.exists(f):
            mtimes.append(os.path.getmtime(f))
    return max(mtimes) if mtimes else 0.0

def get_app_data() -> Dict[str, Any]:
    """
    Universal entry point to get the shared datasets (cached).
    Returns the immutable global app_data dictionary.
    """
    return init_datasets(get_data_mtime())

@st.cache_resource
def init_datasets(data_hash: float) -> Dict[str, Any]:
    """Cached wrapper for Streamlit, invalidated by data_hash (mtime)."""
    return load_all_data_raw()
