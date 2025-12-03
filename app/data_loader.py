import streamlit as st
import os
import logging
from typing import Dict, Any, List, Tuple, Set, Optional
import pandas as pd
import numpy as np
import geopandas as gpd
import shapely as shp
import yaml
import config as cfg # Import config for constants and ScoringConfig

def load_scores_config_as_df(filepath: str) -> pd.DataFrame:
    """Loads the scores configuration from a YAML file and transforms it into a DataFrame."""
    with open(filepath, 'r') as f:
        config = yaml.safe_load(f)

    records = []
    for score_data in config['scores']:
        flat_record = {
            'score': score_data['id'],
            'cat': score_data['category'],
            'metric': score_data.get('source_metric'),
            'incl_binome': score_data['include_in_binom'],
            'score_name': score_data['display']['name'],
            'score_affichage': score_data['display']['strong_point_text'],
            'high_value_adj': score_data['display']['high_value_adjective'],
            'show_metric': score_data['display']['show'],
            'unit': score_data['display'].get('unit'),
            'display_factor': score_data['display']['display_factor'],
            'display_factor': score_data['display']['display_factor'],
            'tooltip': score_data['display']['tooltip'],
            'weight': score_data.get('weight', 1.0), # F-15
            'min_bound': score_data.get('min_bound'),
            'max_bound': score_data.get('max_bound'),
        }
        records.append(flat_record)

    df = pd.DataFrame(records)
    # Ensure correct data types, similar to the original loader
    df = df.astype({'score': str, 'metric': str})
    return df


@st.cache_data
def load_bassin_de_vie_data(file_path: str) -> pd.DataFrame:
    """Loads the 'bassin de vie' dataset."""
    """Loads the 'bassin de vie' dataset."""
    if file_path.endswith('.parquet'):
        df = pd.read_parquet(file_path)
        # Ensure columns are strings
        if 'CODGEO' in df.columns:
            df['CODGEO'] = df['CODGEO'].astype(str)
        if cfg.BV_CODE_COL in df.columns:
            df[cfg.BV_CODE_COL] = df[cfg.BV_CODE_COL].astype(str)
    else:
        df = pd.read_csv(file_path, dtype={'CODGEO': str, cfg.BV_CODE_COL: str})
    return df

def apply_demo_data_if_present(data: Dict[str, Any]) -> None:
    """Updates the data dictionary with demo data if a 'demo' query param is present."""
    if len(st.query_params) > 0 and 'demo' in st.query_params:
        demo_id = st.query_params.get('demo')
        if demo_id in cfg.DEMO_SCENARIOS:
            logging.info(f"--- Loading Demo Mode {demo_id} ---")
            data.update(cfg.DEMO_SCENARIOS[demo_id])

def session_states_init(defaults: Dict[str, Any]) -> None:
    """Initializes all necessary keys in Streamlit's session state."""
    is_demo = 'demo' in st.query_params

    if 'app_data' not in st.session_state:
        st.session_state['app_data'] = {}
    if 'config' not in st.session_state or is_demo:
        st.session_state['config'] = None
    if "processed_gdf" not in st.session_state or is_demo:
        st.session_state['processed_gdf'] = None
    if "selected_geo" not in st.session_state or is_demo:
        st.session_state['selected_geo'] = None
    if "highlighted_result" not in st.session_state or is_demo:
        st.session_state['highlighted_result'] = [False, None]
    if 'fg_dict_ref' not in st.session_state or is_demo:
        st.session_state['fg_dict_ref'] = {}
    if 'fgs_to_show' not in st.session_state or is_demo:
        st.session_state['fgs_to_show'] = set()
    if "zoom" not in st.session_state or is_demo:
        st.session_state['zoom'] = cfg.DEFAULT_MAP_ZOOM
    if "center" not in st.session_state or is_demo:
        st.session_state['center'] = cfg.DEFAULT_MAP_CENTER
    if 'demo_data' not in st.session_state or is_demo:
        st.session_state['demo_data'] = defaults
    # if 'form_page' not in st.session_state:
    st.session_state['form_page'] = 'localisation'

    ui_keys_map = {
        'ui_nom': 'nom',
        'ui_departement': 'departement_actuel',
        'ui_commune': 'commune_actuelle',
        'ui_poids_education': 'poids_education',
        'ui_poids_emploi': 'poids_emploi',
        'ui_poids_logement': 'poids_logement',
        'ui_poids_inclusion': 'poids_inclusion',
        'ui_poids_sante': 'poids_sante',
        'ui_poids_mobilité': 'poids_mobilité',
        'ui_socle_admin_selection': 'socle_admin_selection',
        'ui_affinite_selection': 'affinite_selection',
        'ui_penalite_binome': ('binome_penalty', lambda x: int(x * 100)),
        'ui_pop_min': 'pop_min',
        'ui_nb_adultes': 'nb_adultes',
        'ui_nb_enfants': 'nb_enfants',
        'ui_loc_distance_km': 'loc_distance_km',
        'ui_hebergement': 'hebergement',
        'ui_logement': 'logement',
        'ui_besoin_sante': 'sante',
        'ui_besoins_autres': 'besoins_autres',
        'ui_codes_metiers': 'codes_metiers',
        'ui_codes_formations': 'codes_formations',
        'ui_classe_enfants': 'classe_enfants'
    }

    for ui_key, config_key in ui_keys_map.items():
        if ui_key not in st.session_state or is_demo:
            if isinstance(config_key, tuple):
                base_key, transform = config_key
                st.session_state[ui_key] = transform(defaults[base_key])
            else:
                # config_key is definitely a string here
                st.session_state[ui_key] = defaults[str(config_key)]

    # Handle list-based UI keys separately
    for i in range(defaults.get('nb_adultes', 2)):
        if f'ui_metiers_adult_{i}' not in st.session_state or is_demo:
            st.session_state[f'ui_metiers_adult_{i}'] = defaults['codes_metiers'][i] if i < len(defaults['codes_metiers']) else []
        if f'ui_formations_adult_{i}' not in st.session_state or is_demo:
            st.session_state[f'ui_formations_adult_{i}'] = defaults['codes_formations'][i] if i < len(defaults['codes_formations']) else []
    
    for i in range(defaults.get('nb_enfants', 5)):
        if f'ui_classe_enfant_{i}' not in st.session_state or is_demo:
            st.session_state[f'ui_classe_enfant_{i}'] = defaults['classe_enfants'][i] if i < len(defaults['classe_enfants']) else 'Maternelle'

def get_data_path() -> str:
    """
    Returns the appropriate data path based on the environment.
    Checks for the K_SERVICE environment variable to detect Cloud Run.
    """
    if 'K_SERVICE' in os.environ:
        return str(cfg.GCS_BUCKET_PATH)
    else:
        return str(cfg.LOCAL_CSV_PATH)

def compute_global_score_stats(df: pd.DataFrame, scores_cat: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    """Computes global min/max stats for continuous scores."""
    stats = {}
    # Filter for scores that do NOT have hardcoded bounds
    # We check if min_bound is null (NaN)
    continuous_scores = scores_cat[scores_cat['min_bound'].isna()]
    
    for _, row in continuous_scores.iterrows():
        metric = row['metric']
        # Check if metric is valid and exists in df
        if pd.notna(metric) and metric in df.columns:
            # Compute 1st and 99th percentiles to be robust to outliers
            # We use quantile which handles NaNs gracefully
            min_val = float(df[metric].quantile(0.01))
            max_val = float(df[metric].quantile(0.99))
            
            # Ensure min < max to avoid division by zero (though scaler handles it, good to be safe)
            if min_val == max_val:
                # If constant, we can't really scale. 
                # But let's just store what we found.
                pass
            
            stats[row['score']] = {'min': min_val, 'max': max_val}
            
    return stats

def load_all_datasets(
    odis_file: str,
    bv_file: str,
    scores_cat_file: str,
    metiers_file: str,
    formations_file: str,
    ecoles_file: str,
    maternites_file: str,
    sante_file: str,
    inclusion_file: str,
    caf_file: str, # Added argument
    lovac_file: str, # Added argument
    inclusion_ref_file: str, # Added argument
    pois_file: str = "pois.parquet",
    bmo_vertical_file: str = "bmo_vertical.parquet"
) -> Tuple[gpd.GeoDataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, pd.DataFrame, Dict[str, Dict[str, float]], gpd.GeoDataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Loads all necessary datasets from specified file paths.
    This function acts as a facade, calling specific loading functions for each dataset.
    """
    base_path = get_data_path()

    # The YAML config file is located relative to the app directory, not the data directory.
    # We construct an absolute path to it to prevent FileNotFoundError.
    app_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(app_dir, scores_cat_file)

    # Define the specific columns to load from the main dataset to save memory
    columns_to_load = [
        'codgeo', 'libgeo', 'polygon', 'dep_code', 'reg_code', 'epci_code', 'epci_nom', 'codgeo_voisins',
        'population', 'pop_active',
        'log_soc_inoccupes',
        'log_soc_total', 'log_priv_vacant_plus_2ans', 'log_total',
        'svc_incl_count', 'pol_num',
        'MOD_OVER_OCC', 'MOD_UNDER_OCC', 'SEV_OVER_OCC', 'SEV_UNDER_OCC', 'STD_OCC', 'VSEV_UNDER_OCC', 'log_pp_occup',
        'total_eleves', 'ecoles_count', 'lien_social_count',
        'metiers_offres_ratio'
    ]
    odis = pd.read_parquet(base_path + odis_file, columns=columns_to_load)
    odis['polygon'] = odis.polygon.apply(shp.from_wkb)
    odis = gpd.GeoDataFrame(odis, geometry='polygon', crs='EPSG:4326')
    odis.set_geometry('polygon', inplace=True)
    # Fix invalid geometries before setting precision
    odis['polygon'] = odis.polygon.buffer(0)
    # odis.polygon = shp.set_precision(odis.polygon, grid_size=0.001)
    odis.polygon = odis.polygon.set_precision(grid_size=0.001, mode='valid_output')
    odis = odis[~odis.polygon.isna()]
    
    # Add a centroid column for distance calculations
    # Reproject to a projected CRS before calculating the centroid to avoid warning and get accurate results
    odis['centroid'] = odis.to_crs(epsg=2154).centroid.to_crs(odis.crs)
    
    # --- Bassin de Vie Integration ---
    bassin_de_vie = load_bassin_de_vie_data(base_path + bv_file)
    odis = pd.merge(odis, bassin_de_vie[['CODGEO', cfg.BV_CODE_COL, cfg.BV_NAME_COL]], left_on='codgeo', right_on='CODGEO', how='left')
    odis.drop(columns='CODGEO', inplace=True)
    
    odis.set_index('codgeo', inplace=True)

    # --- Optimize Data Types ---
    # Downcast integer columns to save memory
    odis['population'] = odis['population'].astype('int32')
    odis['svc_incl_count'] = odis['svc_incl_count'].astype(pd.Int16Dtype())

    # Downcast float columns to save memory
    float_cols = ['log_soc_inoccupes',
                  'log_soc_total', 'log_priv_vacant_plus_2ans', 'log_total', 'pol_num']
    for col in float_cols:
        if col in odis.columns:
            odis[col] = odis[col].astype('float32')

    # Convert object columns to category to save memory
    cat_cols = ['dep_code', 'reg_code', 'epci_code']
    for col in cat_cols:
        if col in odis.columns:
            odis[col] = odis[col].astype('category')

    # Index of all scores and their explanations.
    scores_cat = load_scores_config_as_df(config_path)

    #Later we need the code FAP <-> FAP Name used to classify jobs
    codfap_index = pd.read_csv(base_path + metiers_file, delimiter=';', encoding='utf-8-sig')

    # Later we need the code formation <-> Formation Name used to classify trainings
    # source: https://www.data.gouv.fr/fr/datasets/liste-publique-des-organismes-de-formation-l-6351-7-1-du-code-du-travail/
    codformations_index = pd.read_csv(base_path + formations_file, dtype={'codformation': str}, encoding='utf-8-sig').set_index('codformation')

    # Etablissements scolaires
    ecoles_cols_to_load = ['code_commune', 'nom_etablissement', 'type_etablissement', 'ecole_maternelle', 'ecole_elementaire', 'geometry']
    annuaire_ecoles = pd.read_parquet(base_path + ecoles_file, columns=ecoles_cols_to_load)

    # Optimize data types
    annuaire_ecoles['type_etablissement'] = annuaire_ecoles['type_etablissement'].astype('category')
    annuaire_ecoles['ecole_maternelle'] = annuaire_ecoles['ecole_maternelle'].astype(pd.Int8Dtype())
    annuaire_ecoles['ecole_elementaire'] = annuaire_ecoles['ecole_elementaire'].astype(pd.Int8Dtype())

    annuaire_ecoles.geometry = annuaire_ecoles.geometry.apply(shp.from_wkb)
    annuaire_ecoles = gpd.GeoDataFrame(annuaire_ecoles, geometry='geometry', crs='EPSG:4326')

    # --- Pre-process school counts for scoring ---
    # 1. Create boolean flags for each type
    # Note: 'Ecole' usually covers Maternelle and Elementaire, distinguished by specific flags
    annuaire_ecoles['is_maternelle'] = ((annuaire_ecoles['type_etablissement'] == 'Ecole') & (annuaire_ecoles['ecole_maternelle'] == 1)).astype(int)
    annuaire_ecoles['is_elementaire'] = ((annuaire_ecoles['type_etablissement'] == 'Ecole') & (annuaire_ecoles['ecole_elementaire'] == 1)).astype(int)
    annuaire_ecoles['is_college'] = (annuaire_ecoles['type_etablissement'] == 'Collège').astype(int)
    annuaire_ecoles['is_lycee'] = (annuaire_ecoles['type_etablissement'] == 'Lycée').astype(int)

    # 2. Aggregate by commune
    school_counts = annuaire_ecoles.groupby('code_commune').agg({
        'is_maternelle': 'sum',
        'is_elementaire': 'sum',
        'is_college': 'sum',
        'is_lycee': 'sum'
    }).rename(columns={
        'is_maternelle': 'count_maternelle',
        'is_elementaire': 'count_elementaire',
        'is_college': 'count_college',
        'is_lycee': 'count_lycee'
    })

    # 3. Merge into odis (which is indexed by codgeo)
    # school_counts index is 'code_commune', which matches 'codgeo'
    odis = odis.join(school_counts, how='left')
    
    # Fill NaN with 0 for these counts and optimize types
    for col in ['count_maternelle', 'count_elementaire', 'count_college', 'count_lycee']:
        odis[col] = odis[col].fillna(0).astype('int16')

    #Annuaire Maternités
    annuaire_maternites = pd.read_csv(base_path + maternites_file, delimiter=';', encoding='utf-8-sig')
    annuaire_maternites.drop_duplicates(subset=['FI_ET'], keep='last', inplace=True)

    # Annuaire etablissements santé
    annuaire_sante = pd.read_parquet(base_path + sante_file)
    annuaire_sante = annuaire_sante[annuaire_sante.LibelleSph == 'Etablissement public de santé']
    annuaire_sante['geometry'] = gpd.points_from_xy(annuaire_sante.coordxet, annuaire_sante.coordyet, crs=cfg.PROJECTED_CRS)
    annuaire_sante = gpd.GeoDataFrame(annuaire_sante, geometry='geometry', crs=cfg.PROJECTED_CRS)
    annuaire_sante = annuaire_sante.to_crs('EPSG:4326')
    annuaire_sante = pd.merge(annuaire_sante, annuaire_maternites[['FI_ET']], left_on='nofinesset', right_on='FI_ET', how='left', indicator="maternite")
    annuaire_sante.drop(columns=['FI_ET'], inplace=True)
    annuaire_sante.maternite = np.where(annuaire_sante.maternite == 'both', True, False)
    annuaire_sante['codgeo'] = annuaire_sante.Departement + annuaire_sante.Commune

    # --- Pre-process health counts for scoring ---
    # 1. Create boolean flags for each type
    annuaire_sante['is_hopital'] = annuaire_sante['LibelleCategorieAgregat'].isin([
        'Centres Hospitaliers', 
        'Centres Hospitaliers Régionaux', 
        'Hôpitaux Locaux'
    ]).astype(int)
    
    annuaire_sante['is_psy'] = annuaire_sante['LibelleCategorieAgregat'].isin([
        'Centres Hospitaliers Spécialisés Lutte Maladies Mentales', 
        'Autres Etablissements de Lutte contre les Maladies Mentales'
    ]).astype(int)
    
    annuaire_sante['is_maternite'] = annuaire_sante['maternite'].astype(int)
    
    # 2. Aggregate by codgeo
    health_counts = annuaire_sante.groupby('codgeo').agg({
        'is_hopital': 'sum',
        'is_psy': 'sum',
        'is_maternite': 'sum'
    }).rename(columns={
        'is_hopital': 'count_hopital',
        'is_psy': 'count_psy',
        'is_maternite': 'count_maternite'
    })
    
    # 3. Merge into odis
    odis = odis.join(health_counts, how='left')
    for col in ['count_hopital', 'count_psy', 'count_maternite']:
        odis[col] = odis[col].fillna(0).astype('int16')

    # Annuaire des services d'inclusion
    # Pre-process inclusion data for faster lookup
    # We now load 'thematiques' which contains the full slug (category--service)
    inclusion_cols_to_load = ['nom', 'codgeo', 'thematiques', 'geometry']
    annuaire_inclusion = pd.read_parquet(base_path + inclusion_file, columns=inclusion_cols_to_load)

    # Load the reference file for labels
    # CSV format: Nom,Label
    ref_inclusion = pd.read_csv(base_path + inclusion_ref_file)
    # Create a mapping dictionary: Nom (slug) -> Label
    label_map = ref_inclusion.set_index('Nom')['Label'].to_dict()

    # Map the labels
    annuaire_inclusion['label'] = annuaire_inclusion['thematiques'].map(label_map).fillna(annuaire_inclusion['thematiques'])
    
    # Extract category and service from thematiques for backward compatibility or display grouping if needed
    # thematiques format: category--service
    # We can split it efficiently
    split_thematiques = annuaire_inclusion['thematiques'].str.split('--', n=1, expand=True)
    annuaire_inclusion['categorie'] = split_thematiques[0].astype('category')
    annuaire_inclusion['service'] = split_thematiques[1].fillna('-').astype('category')

    annuaire_inclusion.geometry = annuaire_inclusion.geometry.apply(shp.from_wkb)
    annuaire_inclusion = gpd.GeoDataFrame(annuaire_inclusion, geometry='geometry', crs='EPSG:4326')
    
    # Update index to use the full slug (thematiques) as the key
    incl_index = annuaire_inclusion[['codgeo', 'thematiques']].drop_duplicates()
    incl_index = incl_index.groupby('codgeo').agg({'thematiques': lambda x: set(x)})
    # Rename column to 'key' to match expected interface if needed, or just use 'thematiques'
    incl_index.rename(columns={'thematiques': 'key'}, inplace=True)

    # --- Associations (RNA) ---
    # Load and pre-process association data
    # We need to count associations by WALDEC code per commune
    rna_df = pd.read_csv(base_path + 'rna_waldec_20250901_mini_odis.csv', sep=';', dtype={'adrs_codeinsee': str, 'id_waldec': str, 'objet_social2': str}, encoding='latin-1')
    rna_df = rna_df.rename(columns={'adrs_codeinsee': 'codgeo', 'objet_social1': 'id_waldec'})
    
    # Pad id_waldec with zeros to ensure 6 digits for prefix matching
    rna_df['id_waldec'] = rna_df['id_waldec'].astype(str).str.zfill(6)
    
    # Group by codgeo and id_waldec to get counts
    # Result: index=(codgeo, id_waldec), value=count
    associations_counts = rna_df.groupby(['codgeo', 'id_waldec']).size().rename('count')
    
    # We might want to unstack this to have codgeo as index and waldec codes as columns, 
    # but that might be too sparse/large.
    # Keeping it as a Series with MultiIndex is efficient for lookups.
    # To make it easier to use in scoring, we can group by codgeo and aggregate into a dict or similar structure.
    # Actually, for scoring, we will need to sum counts for specific sets of WALDEC codes.
    # Let's keep it as a DataFrame with MultiIndex for now.
    associations_data = associations_counts.reset_index()

    # --- CAF Petite Enfance ---
    # Load CAF data
    # CSV format: ,codgeo,taux_accueil_collectif,taux_accueil_individuel,taux_accueil_total,annee
    caf_df = pd.read_csv(base_path + caf_file, delimiter=',', dtype={'codgeo': str})
    
    # Rename the total coverage column to the expected name
    if 'taux_accueil_total' in caf_df.columns:
        caf_df.rename(columns={'taux_accueil_total': 'taux_couverture'}, inplace=True)
    
    # Merge into odis
    # Use join to preserve odis index (codgeo)
    caf_subset = caf_df[['codgeo', 'taux_couverture']].set_index('codgeo')
    odis = odis.join(caf_subset, how='left')
    odis['taux_couverture'] = odis['taux_couverture'].fillna(0).astype('float32')

    # --- LOVAC Vacance Structurelle ---
    # Load LOVAC data
    # CSV format: CODGEO_25;LIBGEO_25;pp_vacant_25;pp_vacant_plus_2ans_25;...
    lovac_df = pd.read_csv(base_path + lovac_file, delimiter=';', dtype={'CODGEO_25': str}, encoding='latin-1')
    
    # Clean 's' values (secret statistique) -> treat as NaN or 0? 
    # For vacancy, 's' usually means small numbers, so 0 might be safer than NaN to avoid dropping data,
    # but strictly it's unknown. Let's convert 's' to NaN first, then decide.
    # Actually, if we want to calculate a ratio, 0 vacancy is better than NaN.
    # Let's replace 's' with 0 for now, assuming suppressed values are negligible for the score.
    lovac_df['pp_vacant_plus_2ans_25'] = pd.to_numeric(lovac_df['pp_vacant_plus_2ans_25'].replace('s', 0), errors='coerce').fillna(0)
    
    # Merge into odis
    lovac_subset = lovac_df[['CODGEO_25', 'pp_vacant_plus_2ans_25']].set_index('CODGEO_25')
    odis = odis.join(lovac_subset, how='left')
    odis['pp_vacant_plus_2ans_25'] = odis['pp_vacant_plus_2ans_25'].fillna(0).astype('float32')

    # --- Pre-calculate Ratios for Scoring ---
    # Emploi
    # Handle division by zero/NaNs
    # metiers_offres_ratio is already calculated in build.py as (offers / pop_active_be)
    # We just need to scale it to per 1000 inhabitants if that's what the score expects.
    # The original calculation was 1000 * met / pop_active.
    odis['met_ratio'] = (1000 * odis['metiers_offres_ratio']).replace([np.inf, -np.inf], np.nan)
    
    # Logement
    # odis['log_vac_ratio'] = (odis['log_vac'] / odis['log_total']).replace([np.inf, -np.inf], np.nan)
    # New structural vacancy ratio
    # We use log_total from ODIS as the denominator.
    odis['log_vac_struct_ratio'] = (odis['pp_vacant_plus_2ans_25'] / odis['log_total']).replace([np.inf, -np.inf], np.nan)
    
    odis['log_soc_inoc_ratio'] = (odis['log_soc_inoccupes'] / odis['log_soc_total']).replace([np.inf, -np.inf], np.nan)
    # odis['log_5p_ratio'] = (odis['rp_5+pieces'] / odis['log_rp']).replace([np.inf, -np.inf], np.nan)
    
    # Education
    # Risque fermeture: Average school size. Higher is better (less risk).
    odis['risque_fermeture_ratio'] = (odis['total_eleves'] / odis['ecoles_count']).replace([np.inf, -np.inf], 0).fillna(0)
    
    # Inclusion: Lien Social
    core_codes_prefixes = tuple(cfg.WALDEC_CORE_INCLUSION)
    # associations_data has MultiIndex (codgeo, id_waldec) -> count. Reset index in load_all_datasets makes it a DF.
    # We need to filter rows.
    core_assos = associations_data[associations_data['id_waldec'].astype(str).str.startswith(core_codes_prefixes, na=False)]
    core_counts = core_assos.groupby('codgeo')['count'].sum()
    # odis = odis.join(core_counts.rename('lien_social_count'), how='left')
    # odis['lien_social_count'] = odis['lien_social_count'].fillna(0)
    odis['lien_social_density'] = ((odis['lien_social_count'] * 1000) / odis['population']).replace([np.inf, -np.inf], np.nan)

    # --- Compute Global Stats ---
    global_score_stats = compute_global_score_stats(odis, scores_cat)

    # --- BMO Vertical Integration ---
    bmo_vertical = pd.read_parquet(base_path + bmo_vertical_file)
    
    # Aggregate top metiers for scoring (list of FAP codes per commune)
    # We need this in 'odis' for the scoring engine
    if not bmo_vertical.empty:
        top_metiers = bmo_vertical.groupby('codgeo')['fap_code'].apply(list).rename('metiers_offres_top5')
        odis = odis.join(top_metiers, how='left')

    # --- Load Unified POIs for Maps ---
    pois = pd.read_parquet(base_path + pois_file)
    # Parse metadata if needed, but for maps we might just use it as is or parse on the fly
    # Ensure geometry
    pois = gpd.GeoDataFrame(
        pois,
        geometry=gpd.points_from_xy(pois.lon, pois.lat),
        crs="EPSG:4326"
    )

    return (
        odis,
        scores_cat,
        codfap_index, # This was 'metiers' in the instruction, but 'codfap_index' is used in init_datasets
        codformations_index, # This was 'formations' in the instruction, but 'codformations_index' is used in init_datasets
        annuaire_ecoles, # This was 'annuaire_education' in the instruction, but 'annuaire_ecoles' is used in init_datasets
        annuaire_sante,
        annuaire_inclusion,
        incl_index, # This was 'codfap_index' in the instruction, but 'incl_index' is used in init_datasets
        associations_data, # This was removed in the instruction, but is used in init_datasets
        global_score_stats,
        pois,
        bmo_vertical
    )

@st.cache_data
def load_area_geodata(_communes_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Creates a GeoDataFrame of departements and regions by dissolving commune geometries.
    """
    communes_gdf = _communes_gdf.copy()
    
    # Dissolve by departement
    dep_geo = communes_gdf.dissolve(by='dep_code')
    dep_geo['area_type'] = 'departement'
    dep_geo = dep_geo.reset_index().rename(columns={'dep_code': 'area_code'})
    
    # Dissolve by region
    reg_geo = communes_gdf.dissolve(by='reg_code')
    reg_geo['area_type'] = 'region'
    reg_geo = reg_geo.reset_index().rename(columns={'reg_code': 'area_code'})
    
    # Combine and set index
    area_geo = pd.concat([dep_geo, reg_geo], ignore_index=True)
    area_geo = area_geo.set_index(['area_type', 'area_code'])

    # Explicitly ensure it's a GeoDataFrame with the geometry column set
    area_geo = gpd.GeoDataFrame(area_geo, geometry='polygon', crs=communes_gdf.crs)
    
    return area_geo

@st.cache_data
def load_bassin_de_vie_geodata(_communes_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Creates a GeoDataFrame of 'bassins de vie' with their dissolved geometries and centroids.
    """
    communes_gdf = _communes_gdf.copy()
    
    # Dissolve commune polygons into 'bassin de vie' polygons
    # This logic is copied from maps.dissolve_communes_to_bassins_de_vie to avoid circular imports
    bv_geo = gpd.GeoDataFrame(
        {cfg.BV_CODE_COL: communes_gdf[cfg.BV_CODE_COL]},
        geometry=communes_gdf.geometry,
        crs="EPSG:4326"
    ).dissolve(by=cfg.BV_CODE_COL)

    # Calculate centroids for the new BV polygons
    # Reproject to a projected CRS before calculating the centroid to avoid warning and get accurate results
    bv_geo['centroid'] = bv_geo.to_crs(epsg=2154).centroid.to_crs(bv_geo.crs)
    
    # Get the names for each BV
    bv_names = communes_gdf[[cfg.BV_CODE_COL, cfg.BV_NAME_COL]].drop_duplicates().set_index(cfg.BV_CODE_COL)
    
    # Merge names back into the dissolved geodataframe
    bv_geo = bv_geo.merge(bv_names, left_index=True, right_index=True, how='left')
    
    return bv_geo

@st.cache_resource
def init_datasets() -> Dict[str, Any]:
    """Loads all datasets and returns them in a structured dictionary."""
    logging.info("--- Loading all datasets... ---")
    odis, scores_cat, codfap_index, codformations_index, annuaire_ecoles, annuaire_sante, annuaire_inclusion, incl_index, associations_data, global_score_stats, pois, bmo_vertical = load_all_datasets(
        odis_file=cfg.ODIS_FILE,
        bv_file=cfg.BV_FILENAME,
        scores_cat_file=cfg.SCORES_CAT_FILE,
        metiers_file=cfg.METIERS_FILE,
        formations_file=cfg.FORMATIONS_FILE,
        ecoles_file=cfg.ECOLES_FILE,
        maternites_file=cfg.MATERNITE_FILE,
        sante_file=cfg.SANTE_FILE,
        inclusion_file=cfg.INCLUSION_FILE,
        caf_file=cfg.CAF_FILE,
        lovac_file=cfg.LOVAC_FILE,
        inclusion_ref_file=cfg.INCLUSION_REF_FILE,
        pois_file=cfg.POIS_FILE if hasattr(cfg, 'POIS_FILE') else "pois.parquet",
        bmo_vertical_file=cfg.BMO_VERTICAL_FILE
    )
    
    bv_geo = load_bassin_de_vie_geodata(odis)
    area_geo = load_area_geodata(odis)
    
    return {
        "odis": odis,
        "bv_geo": bv_geo,
        "area_geo": area_geo,
        "scores_cat": scores_cat,
        "codfap_index": codfap_index,
        "codformations_index": codformations_index,
        "annuaire_ecoles": annuaire_ecoles,
        "annuaire_sante": annuaire_sante,
        "annuaire_inclusion": annuaire_inclusion,
        "incl_index": incl_index,
        "associations_data": associations_data,
        "global_score_stats": global_score_stats,
        "pois": pois,
        "bmo_vertical": bmo_vertical,
        "coddep_set": sorted(set(odis['dep_code'])),
        "depcom_df": odis[['dep_code','libgeo']].sort_values('libgeo'),
    }