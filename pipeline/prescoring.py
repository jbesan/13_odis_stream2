import logging
import pandas as pd
import geopandas as gpd
import numpy as np
import yaml
from shapely import wkb
from typing import Dict, Any, Optional
from pathlib import Path

from pipeline.common import (
    PipelineLogger, load_config,
    CONFIG_FILE, CACHE_DIR, OUTPUT_DIR, CLEAN_DIR, STATUS_FILE
)
import app.config as cfg

# Global Scores Config cache
_scores_config_cache = {}

def get_scores_config():
    global _scores_config_cache
    if _scores_config_cache:
        return _scores_config_cache
        
    app_config_path = Path(__file__).parent.parent / "app" / "scores_config.yaml"
    
    if app_config_path.exists():
        with open(app_config_path, 'r') as f:
            full_config = yaml.safe_load(f)
            if 'scores' in full_config:
                for s in full_config['scores']:
                    _scores_config_cache[s['id']] = {
                        'min': s.get('min_bound'), 
                        'max': s.get('max_bound'),
                        'scaling_type': s.get('scaling_type', 'linear'),
                        'mu': s.get('mu'),
                        'sigma': s.get('sigma')
                    }
    else:
        logging.warning(f"App config not found at {app_config_path}")
    return _scores_config_cache

def scale_series(series, min_b, max_b, inverted=False):
    if series.empty: return series
    denom = max_b - min_b
    if denom == 0:
        scaled = pd.Series(0.0 if not inverted else 1.0, index=series.index)
    else:
        scaled = (series - min_b) / denom
        
    if inverted and denom != 0:
        scaled = 1.0 - scaled
    return scaled.clip(0, 1)

def get_min_max(series):
    if series.empty: return 0.0, 1.0
    return float(series.min()), float(series.max())

def process_scaling(df, col_name, output_col, inverted=False):
    if col_name not in df.columns: return
    
    scores_config = get_scores_config()
    conf = scores_config.get(output_col, {})
    scaling_type = conf.get('scaling_type', 'linear')
    
    if scaling_type == 'gaussian':
        mu = float(conf.get('mu', 50000))
        sigma = float(conf.get('sigma', 40000))
        logging.info(f"Applying Gaussian scaling to {col_name} -> {output_col} (mu={mu}, sigma={sigma})")
        df[output_col] = np.exp(-0.5 * ((df[col_name] - mu) / sigma)**2)
        return

    c_min, c_max = conf.get('min'), conf.get('max')
    
    if c_min is not None and c_max is not None:
        min_b, max_b = c_min, c_max
    else:
        min_b, max_b = get_min_max(df[col_name])
        
    df[output_col] = scale_series(df[col_name], min_b, max_b, inverted)


def aggregate_plm(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Aggregates data from arrondissements to the global commune for PLM."""
    plm_mapping = {
        '75056': [str(x) for x in range(75101, 75121)], # Paris
        '13055': [str(x) for x in range(13201, 13217)], # Marseille
        '69123': [str(x) for x in range(69381, 69390)]  # Lyon
    }
    
    # Columns to aggregate (sum)
    cols_to_sum = [
        'population', 'pop_active', 'pop_chomeurs', 'pop_employes',
        'edu_maternelle_ct', 'edu_elementaire_ct', 'edu_college_ct', 'edu_lycee_ct',
        'count_hopital', 'count_maternite', 'count_psy',
        'lien_social_count', 'inc_asso_refug_count', 'bpe_creches_count', 'risky_schools_count',
        'log_priv_total', 'log_priv_vacant_plus_2ans',
        'log_soc_total', 'log_soc_inoccupes',
        'total_eleves', 'ecoles_count',
        'socle_match_count',
        'heb_centres_heb_cap', 'heb_foyers_count', 
        'heb_loc_iml_count', 'heb_habitant_count'
    ]
    
    # Socle calculation is done AFTER this function in apply_prescoring (lines 350+).
    # So we don't need to aggregate socle_match_count here because it's not yet calculated!
    # Correct. aggregate_plm is called at the TOP.
    
    for global_code, arrondissements in plm_mapping.items():
        if global_code in gdf['codgeo'].values:
            # Check if we have data for arrondissements
            mask_arr = gdf['codgeo'].isin(arrondissements)
            if mask_arr.any():
                # For each column, sum values from arrondissements
                for col in cols_to_sum:
                    if col in gdf.columns:
                        # Sum, treating NaN as 0
                        total_val = gdf.loc[mask_arr, col].sum(min_count=0) # min_count=0 -> 0 if all nan? No, sum returns 0 if empty.
                        # Update global row
                        # Only update if > 0 or if we want to force 0?
                        # If arrondissements have data, we want the sum.
                        gdf.loc[gdf['codgeo'] == global_code, col] = total_val
                        
                logging.info(f"Aggregated PLM for {global_code} from {mask_arr.sum()} arrondissements.")
            else:
                logging.warning(f"No arrdt data found for {global_code}")
        else:
             logging.warning(f"Global code {global_code} not found in GDF")
             
    return gdf

def apply_prescoring(config: Dict[str, Any], logger: PipelineLogger):
    """Applies pre-scoring logic (ratios, densities, scaling) to odis_communes."""
    logger.log_step("apply_prescoring", "STARTED")
    try:
        input_path = OUTPUT_DIR / "odis_communes_pre.parquet"
        output_path = OUTPUT_DIR / "odis_communes.parquet"
        
        if not input_path.exists():
             logger.error(f"Input file not found: {input_path}")
             logger.log_step("apply_prescoring", "FAILED", {"reason": "Input file not found"})
             return

        # Read as standard Parquet (WKB)
        communes_df = pd.read_parquet(input_path, engine='fastparquet')
        
        # Convert WKB to Geometry
        if 'polygon' in communes_df.columns:
            communes_df['geometry'] = communes_df['polygon'].apply(lambda x: wkb.loads(bytes(x)))
            communes_gdf = gpd.GeoDataFrame(communes_df, geometry='geometry', crs='EPSG:4326')
        else:
            # Fallback
            communes_gdf = gpd.GeoDataFrame(communes_df, geometry='geometry')

        logger.log_step("apply_prescoring_load", "LOADED", {"rows": len(communes_gdf)})

        # --- PLM Aggregation ---
        communes_gdf = aggregate_plm(communes_gdf)
        
        # --- Calculated Columns ---
        
        # --- Fill NaNs for Raw Metrics (Fix N/A display) ---
        raw_metrics_to_fill = [
            'edu_maternelle_ct', 'edu_elementaire_ct', 'edu_college_ct', 'edu_lycee_ct',
            'count_hopital', 'count_maternite', 'count_psy',
            'risky_schools_count', 'lien_social_count', 'inc_asso_refug_count', 'bpe_creches_count',
            'edu_pe_tx_couverture', 'pop_chomeurs', 'log_priv_vacant_plus_2ans',
            'pol_num', 'log_vac_struct_ratio',
            'heb_centres_heb_cap', 'heb_foyers_count', 
            'heb_loc_iml_count', 'heb_habitant_count'
        ]
        # Robust fill for RNA Category counts (inc_rna_..._count)
        rna_cols = [c for c in communes_gdf.columns if c.startswith('inc_rna_') and c.endswith('_count')]
        raw_metrics_to_fill.extend(rna_cols)
        for col in raw_metrics_to_fill:
            if col in communes_gdf.columns:
                 communes_gdf[col] = communes_gdf[col].fillna(0)
        # 0. Load Associations for Lien Social Score (moved to build.py)
        # Block removed.

        # log_soc_inoc_ratio
        if 'log_soc_total' in communes_gdf.columns and 'log_soc_inoccupes' in communes_gdf.columns:
            communes_gdf['log_soc_inoc_ratio'] = np.where(
                communes_gdf['log_soc_total'] > 0,
                communes_gdf['log_soc_inoccupes'] / communes_gdf['log_soc_total'],
                0.0
            )

        # log_pp_occup (Weighted Average of Occupancy)
        # Weights:
        # SEV_OVER_OCC: 0.0
        # MOD_OVER_OCC: 0.25
        # STD_OCC: 0.5
        # MOD_UNDER_OCC: 0.75
        # SEV_UNDER_OCC: 1.0
        # VSEV_UNDER_OCC: 1.0
        
        occup_cols = ['SEV_OVER_OCC', 'MOD_OVER_OCC', 'STD_OCC', 'MOD_UNDER_OCC', 'SEV_UNDER_OCC', 'VSEV_UNDER_OCC']
        # Ensure columns exist (should be filled in build, but good to check)
        for col in occup_cols:
            if col not in communes_gdf.columns:
                communes_gdf[col] = 0.0
        
        total_occup_households = communes_gdf[occup_cols].sum(axis=1)
        communes_gdf['log_total'] = total_occup_households # Use as log_total (RP)
        
        weighted_sum_occup = (
            communes_gdf['SEV_OVER_OCC'] * 0.0 +
            communes_gdf['MOD_OVER_OCC'] * 0.25 +
            communes_gdf['STD_OCC'] * 0.5 +
            communes_gdf['MOD_UNDER_OCC'] * 0.75 +
            communes_gdf['SEV_UNDER_OCC'] * 1.0 +
            communes_gdf['VSEV_UNDER_OCC'] * 1.0
        )
        
        communes_gdf['log_pp_occup'] = np.where(
            total_occup_households > 0,
            weighted_sum_occup / total_occup_households,
            0.0 # Default if no data
        )

        # metiers_offres_ratio and pop_chomage_ratio
        # Requires pop_active_be
        # pop_chomage_ratio (Still useful as a general indicator of local economy)
        
        if 'pop_active' in communes_gdf.columns and 'pop_chomeurs' in communes_gdf.columns:
            communes_gdf['pop_chomage_ratio'] = np.where(
                communes_gdf['pop_active'] > 0,
                communes_gdf['pop_chomeurs'] / communes_gdf['pop_active'],
                0.0
            )

        # --- Pre-calculate Ratios and Scaled Scores (Optimization) ---
        
        
        # 2. Logement Vacant Structurel Ratio
        # 2. Logement Vacant Structurel Ratio
        if 'log_priv_total' in communes_gdf.columns and 'log_priv_vacant_plus_2ans' in communes_gdf.columns:
            communes_gdf['log_vac_struct_ratio'] = np.where(
                communes_gdf['log_priv_total'] > 0,
                communes_gdf['log_priv_vacant_plus_2ans'] / communes_gdf['log_priv_total'],
                0.0
            )
        
            communes_gdf['lien_social_density'] = np.where(
                communes_gdf['population'] > 0,
                (communes_gdf['lien_social_count'] * 1000) / communes_gdf['population'],
                0.0
            )

        # SIAE Associations Density (New F-39)
        if 'population' in communes_gdf.columns and 'inc_siae_count' in communes_gdf.columns:
            communes_gdf['inc_siae_density'] = np.where(
                communes_gdf['population'] > 0,
                (communes_gdf['inc_siae_count'] * 1000) / communes_gdf['population'],
                0.0
            )

        # 4. Risque Fermeture (Count of schools with < 20 students/class)
        # We use the count directly. Lower is better.
        if 'risky_schools_count' in communes_gdf.columns:
            communes_gdf['risque_fermeture_ratio'] = communes_gdf['risky_schools_count'].fillna(0)
        else:
            communes_gdf['risque_fermeture_ratio'] = 0.0

        # ... (Creches Density)
        if 'population' in communes_gdf.columns and 'bpe_creches_count' in communes_gdf.columns:
            communes_gdf['bpe_creches_density'] = np.where(
                communes_gdf['population'] > 0,
                (communes_gdf['bpe_creches_count'] * 1000) / communes_gdf['population'],
                0.0
            )
        
        # Hebergement Densities (New F-42)
        if 'population' in communes_gdf.columns:
            if 'heb_centres_heb_cap' in communes_gdf.columns:
                communes_gdf['heb_centres_heb_density'] = np.where(
                    communes_gdf['population'] > 0,
                    (communes_gdf['heb_centres_heb_cap'] * 1000) / communes_gdf['population'],
                    0.0
                )
            if 'inc_asso_refug_count' in communes_gdf.columns:
                communes_gdf['inc_asso_refug_density'] = np.where(
                    communes_gdf['population'] > 0,
                    (communes_gdf['inc_asso_refug_count'] * 1000) / communes_gdf['population'],
                    0.0
                )
            if 'heb_foyers_count' in communes_gdf.columns:
                communes_gdf['heb_foyers_density'] = np.where(
                    communes_gdf['population'] > 0,
                    (communes_gdf['heb_foyers_count'] * 1000) / communes_gdf['population'],
                    0.0
                )
            if 'heb_loc_iml_count' in communes_gdf.columns:
                communes_gdf['heb_loc_iml_density'] = np.where(
                    communes_gdf['population'] > 0,
                    (communes_gdf['heb_loc_iml_count'] * 1000) / communes_gdf['population'],
                    0.0
                )
            if 'heb_habitant_count' in communes_gdf.columns:
                communes_gdf['heb_habitant_density'] = np.where(
                    communes_gdf['population'] > 0,
                    (communes_gdf['heb_habitant_count'] * 1000) / communes_gdf['population'],
                    0.0
                )
        
        # Load App Config for Scores (Source of Truth)
        scores_config = get_scores_config()
        socle_admin_list = []
        
        
        # Updated Housing Rent Scaling (ODACE source)
        # Using concise names as per user request: appt_all, appt_t1_t2, appt_t3_p, house_all
        # We KEEP the raw data (euros/m2) and add the _scaled suffix
        logging.info(f"DEBUG: communes_gdf cols before scaling: {[c for c in communes_gdf.columns if 'loyer' in c]}")
        for col, target in [
            ('loyer_m2_moy_appt_all', 'log_loyer_moyen_appt_all_scaled'),
            ('loyer_m2_moy_appt_t1_t2', 'log_loyer_moyen_appt_t1_t2_scaled'),
            ('loyer_m2_moy_appt_t3_p', 'log_loyer_moyen_appt_t3_p_scaled'),
            ('loyer_m2_moy_house_all', 'log_loyer_moyen_house_all_scaled')
        ]:
            if col in communes_gdf.columns:
                process_scaling(communes_gdf, col, target, inverted=True)
            else:
                logging.warning(f"ODACE Rent column {col} missing for scaling.")

        process_scaling(communes_gdf, 'log_vac_scaled', 'log_vac_scaled') # wait, log_vac_scaled vs log_vac_struct_ratio?
        # Fixed logic:
        process_scaling(communes_gdf, 'log_vac_struct_ratio', 'log_vac_scaled')
        process_scaling(communes_gdf, 'lien_social_density', 'inc_asso_core_scaled')
        process_scaling(communes_gdf, 'inc_asso_refug_density', 'inc_asso_refug_scaled')
        process_scaling(communes_gdf, 'inc_siae_density', 'inc_siae_density_scaled')
        
        
        # inc_pol_scaled (already 0-1)
        if 'pol_num' in communes_gdf.columns:
            communes_gdf['inc_pol_scaled'] = communes_gdf['pol_num']

        process_scaling(communes_gdf, 'log_pp_occup', 'log_occup_scaled')

        # Hebergement Scaling (New F-42)
        process_scaling(communes_gdf, 'heb_centres_heb_density', 'heb_centres_heb_scaled')
        process_scaling(communes_gdf, 'heb_foyers_density', 'heb_foyers_scaled')
        process_scaling(communes_gdf, 'heb_loc_iml_density', 'heb_loc_iml_scaled')
        process_scaling(communes_gdf, 'heb_habitant_density', 'heb_asso_habitant_scaled')

        # Population Decline (Inverted logic handled in process_scaling)
        if 'pop_jeune_2016' in communes_gdf.columns and 'pop_jeune_2022' in communes_gdf.columns:
             communes_gdf['youth_growth_rate'] = np.where(
                communes_gdf['pop_jeune_2016'] > 0,
                (communes_gdf['pop_jeune_2022'] - communes_gdf['pop_jeune_2016']) / communes_gdf['pop_jeune_2016'].replace(0, np.nan),
                0.0
             ).astype(float)
             communes_gdf['youth_growth_rate'] = communes_gdf['youth_growth_rate'].fillna(0.0)
             
        if 'pop_active_2016' in communes_gdf.columns and 'pop_active_2022' in communes_gdf.columns:
             communes_gdf['workclass_growth_rate'] = np.where(
                communes_gdf['pop_active_2016'] > 0,
                (communes_gdf['pop_active_2022'] - communes_gdf['pop_active_2016']) / communes_gdf['pop_active_2016'].replace(0, np.nan),
                0.0
             ).astype(float) # Ensure float type
             # Fill nan back to 0.0 if any division resulted in NaN
             communes_gdf['workclass_growth_rate'] = communes_gdf['workclass_growth_rate'].fillna(0.0)

        if 'youth_growth_rate' in communes_gdf.columns:
            process_scaling(communes_gdf, 'youth_growth_rate', 'youth_decline_scaled', inverted=True)
            
        if 'workclass_growth_rate' in communes_gdf.columns:
            process_scaling(communes_gdf, 'workclass_growth_rate', 'workclass_decline_scaled', inverted=True)

        if 'log_soc_total' in communes_gdf.columns and 'log_soc_inoccupes' in communes_gdf.columns:
             communes_gdf['log_soc_inoc_ratio'] = np.where(
                 communes_gdf['log_soc_total'] > 0,
                 communes_gdf['log_soc_inoccupes'] / communes_gdf['log_soc_total'],
                 0.0
             )
             process_scaling(communes_gdf, 'log_soc_inoc_ratio', 'log_soc_inoc_scaled')
        
        # edu_classes_ferm_scaled
        # Logic was: max count -> 1.0 (inverted=False in previous edit).
        # User said: "schools with classes at risk are closing are more likely to welcome new families -> higher is better"
        # So Higher Ratio (Risk Count) -> Higher Score. Standard scaling.
        # But wait, previous edit said: inverted=False.
        # Let's keep it standard.
        process_scaling(communes_gdf, 'risque_fermeture_ratio', 'edu_classes_ferm_scaled')

        process_scaling(communes_gdf, 'bpe_creches_density', 'edu_creches_scaled')
        process_scaling(communes_gdf, 'edu_pe_tx_couverture', 'edu_petite_enfance_scaled') # Usually 0-100? or 0-1?


        # mob_gare_scaled
        if 'has_gare' in communes_gdf.columns:
            # Binary score: 1 if present, 0 if not
            communes_gdf['mob_gare_scaled'] = communes_gdf['has_gare'].fillna(0).astype(float)
        
        # Static Boolean Scores (Education)
        for col, score_col in [
            ('edu_maternelle_ct', 'edu_maternelle_scaled'),
            ('edu_elementaire_ct', 'edu_elementaire_scaled'),
            ('edu_college_ct', 'edu_college_scaled'),
            ('edu_lycee_ct', 'edu_lycee_scaled')
        ]:
            if col in communes_gdf.columns:
                communes_gdf[score_col] = (communes_gdf[col] > 0).astype(float)

        # Static Boolean Scores (Sante)
        # Note: Health columns are not merged in build.py yet? 
        # I need to check if they are merged.
        # build.py merges 'finess' or 'sante'?
        # It merges 'finess' but I don't see a merge for counts in build.py lines 60-80.
        # I see 'merge_clean("education", ...)' but not health counts.
        # Let's check build.py again.
        
        for col, score_col in [
            ('count_hopital', 'sante_hopital_scaled'),
            ('count_maternite', 'sante_maternite_scaled'),
            ('count_psy', 'sante_psy_scaled')
        ]:
            if col in communes_gdf.columns:
                communes_gdf[score_col] = (communes_gdf[col] > 0).astype(float)
            else:
                logging.warning(f"Column {col} missing for {score_col}")
               # 2. Add static scores that don't need calc (just rename/copy effectively, but already done in build?)
        # Actually most are calculated. 
        # But 'inc_population_scaled' etc are done above.
        
        # --- Drop Unused Columns ---
        cols_to_drop = [
            'MOD_OVER_OCC', 'MOD_UNDER_OCC', 'SEV_OVER_OCC', 'SEV_UNDER_OCC', 'STD_OCC', 'VSEV_UNDER_OCC', # *_OCC
            # 'total_eleves', 'ecoles_count', # KEEP for details
            'log_total', 'log_soc_total', 'log_soc_inoccupes',
            # 'pol_num', #'log_priv_vacant_plus_2ans', # KEEP for details
            # 'edu_maternelle_ct', 'edu_elementaire_ct', 'edu_college_ct', 'edu_lycee_ct', # KEEP
            'svc_incl_count',
            # 'count_hopital', 'count_psy', 'count_maternite', # KEEP for details
            # 'log_soc_inoc_ratio', 'log_pp_occup', # KEEP for details
            # 'metiers_offres_ratio', 'pop_chomage_ratio', # KEEP
            # 'log_vac_struct_ratio', 'risque_fermeture_ratio', 'bpe_creches_density', # 'lien_social_density', # KEEP
            #'edu_pe_tx_couverture', # 'bpe_creches_count', # KEEP
            # 'lien_social_count', # KEEP
            # 'pop_active', 'pop_employes', 'pop_chomeurs' # KEEP
        ]
        
        # --- Socle Administratif (Pre-calculated) ---
        # Load POIs to get inclusion services
        pois_path = OUTPUT_DIR / "odis_pois.parquet"
        if pois_path.exists():
            try:
                default_socle_admin = cfg.DEFAULT_INC_SERVICES_CORE

                
                pois_df = pd.read_parquet(pois_path, engine='fastparquet')
                incl_pois = pois_df[pois_df['category'] == 'incl_services'].copy()
                
                if not incl_pois.empty:
                    import ast
                    def parse_types(x):
                        if not isinstance(x, str): return []
                        x = x.strip()
                        if not x: return []
                        try:
                            val = ast.literal_eval(x)
                            if isinstance(val, list): return val
                            return [str(val)]
                        except (ValueError, SyntaxError):
                            # It's a raw string slug
                            return [x]

                    # Explode types for analysis
                    incl_pois['services_list'] = incl_pois['type'].astype(str).apply(parse_types)
                    exploded = incl_pois.explode('services_list')
                    
                    socle_slugs = set(default_socle_admin)
                    if socle_slugs:
                        exploded['is_socle'] = exploded['services_list'].isin(socle_slugs)
                        socle_presence = exploded[exploded['is_socle']].groupby('codgeo', observed=True)['services_list'].nunique()
                    
                        max_score = len(socle_slugs)
                        socle_scores = (socle_presence / max_score)
                        
                        # Assign using map on codgeo
                        communes_gdf['inc_services_core_scaled'] = communes_gdf['codgeo'].map(socle_scores)
                        communes_gdf['inc_services_core_scaled'] = communes_gdf['inc_services_core_scaled'].fillna(0.0)

                        # Save Raw Count
                        communes_gdf['socle_match_count'] = communes_gdf['codgeo'].map(socle_presence).fillna(0).astype(int)
                        communes_gdf['inc_siae_count'] = communes_gdf['inc_siae_count'].fillna(0) # Safety
                    else:
                         communes_gdf['inc_services_core_scaled'] = 0.0
                         communes_gdf['socle_match_count'] = 0

                    logger.log_step("inc_services_core_scaled", "CALCULATED")
                else:
                    communes_gdf['inc_services_core_scaled'] = 0.0
                    
            except Exception as e:
                logging.error(f"Failed to calculate socle admin score at line {e.__traceback__.tb_lineno}: {e}")
                import traceback
                traceback.print_exc()
                communes_gdf['inc_services_core_scaled'] = 0.0
        else:
             logging.warning("pois.parquet not found, skipping socle admin score")
             communes_gdf['inc_services_core_scaled'] = 0.0

        communes_gdf.drop(columns=[c for c in cols_to_drop if c in communes_gdf.columns], inplace=True)
        
        
        # Additional drop request from user
        more_cols_to_drop = [
            'pop_jeune_2016', 'pop_jeune_2022', 'pop_active_2016', 'pop_active_2022',
            'libelle_bassin_de_vie', 'has_gare', 'inc_siae_count', #'gare_count', # KEEP
            #'risky_schools_count', # KEEP
            'log_priv_total'
        ]
        communes_gdf.drop(columns=[c for c in more_cols_to_drop if c in communes_gdf.columns], inplace=True)

        # Optimization: Cast floats to float32 (float16 caused UI issues and overflow)
        exclude_cols = {'population', 'plm'}
        for col in communes_gdf.select_dtypes(include=['float64']).columns:
             # We generally want everything float32
             communes_gdf[col] = communes_gdf[col].astype('float32')
        
        if 'inc_services_core_scaled' not in communes_gdf.columns:
            communes_gdf['inc_services_core_scaled'] = 0.0
        # Save
        if 'geometry' in communes_gdf.columns:
            # SOTA: Keep only metric numerical coordinates in the massive `odis` dataframe to avoid geometry overhead for fast Euclidean distance computations
            # LAMBERT-93 (EPSG:2154)
            metric_geo = communes_gdf.geometry.to_crs('EPSG:2154')
            cents = metric_geo.centroid
            communes_gdf['centroid_lon'] = cents.x.values
            communes_gdf['centroid_lat'] = cents.y.values

            # Ensure we are in EPSG:4326 (WGS84) before serializing polygons to WKB for the UI
            if communes_gdf.crs != 'EPSG:4326':
                temp_gdf = communes_gdf.to_crs('EPSG:4326')
                communes_gdf['polygon'] = temp_gdf.geometry.to_wkb()
            else:
                communes_gdf['polygon'] = communes_gdf.geometry.to_wkb()
            
            # Drop the heavy metric geometry to keep the dataframe lightweight
            communes_gdf.drop(columns=['geometry'], inplace=True)
            
        pd.DataFrame(communes_gdf).to_parquet(output_path, compression='brotli', index=False, engine='fastparquet')
        logger.log_step("apply_prescoring", "COMPLETED", {"columns": len(communes_gdf.columns), "path": str(output_path), "rows": len(communes_gdf)})

    except Exception as e:
        logger.log_step("apply_prescoring", "ERROR", {"error": str(e)})
        logging.error(f"Prescoring failed: {e}")
        raise e


def score_bassins_de_vie(config: Dict[str, Any], logger: PipelineLogger):
    """Calculates scores for Bassins de Vie."""
    logger.log_step("score_bassins_de_vie", "STARTED")
    try:
        bv_path = OUTPUT_DIR / "odis_bassins_de_vie.parquet"
        communes_path = OUTPUT_DIR / "odis_communes.parquet"
        
        if not bv_path.exists() or not communes_path.exists():
             logging.error("BV or Communes parquet not found.")
             return

        # Read as standard Parquet (WKB) - BV
        bv_df = pd.read_parquet(bv_path, engine='fastparquet')
        if 'polygon' in bv_df.columns:
             bv_df['geometry'] = bv_df['polygon'].apply(lambda x: wkb.loads(bytes(x)))
             bv_gdf = gpd.GeoDataFrame(bv_df, geometry='geometry', crs=cfg.PROJECTED_CRS)
        else:
             bv_gdf = gpd.GeoDataFrame(bv_df, geometry='geometry')

        # Read as standard Parquet (WKB) - Communes
        communes_df = pd.read_parquet(communes_path, engine='fastparquet')
        # We don't need geometry for communes here, just scores.

        
        # We need Aggregated Counts which should be in 'bv_gdf' if build.py did its job.
        
        # --- 1. Ratios & Densities ---
        
        
        # Lien Social
        bv_gdf['lien_social_density'] = np.where(
            bv_gdf['population_bv'] > 0,
            bv_gdf['lien_social_count'] / bv_gdf['population_bv'] * 1000,
            0.0
        )
        
        # SIAE Density (New F-39)
        if 'inc_siae_count' in bv_gdf.columns and 'population_bv' in bv_gdf.columns:
             bv_gdf['inc_siae_density'] = np.where(
                 bv_gdf['population_bv'] > 0,
                 bv_gdf['inc_siae_count'] / bv_gdf['population_bv'] * 1000,
                 0.0
             )
        
        # Refugee Associations (Inclusion)
        if 'inc_asso_refug_count' in bv_gdf.columns and 'population_bv' in bv_gdf.columns:
             bv_gdf['inc_asso_refug_density'] = np.where(
                 bv_gdf['population_bv'] > 0,
                 bv_gdf['inc_asso_refug_count'] / bv_gdf['population_bv'] * 1000,
                 0.0
             )

        # Creches
        # Note: 'population_bv' is the sum of population.
        if 'bpe_creches_count' in bv_gdf.columns and 'population_bv' in bv_gdf.columns:
             bv_gdf['bpe_creches_density'] = np.where(
                 bv_gdf['population_bv'] > 0,
                 bv_gdf['bpe_creches_count'] / bv_gdf['population_bv'] * 1000,
                 0.0
             )
             
        # J'Accueille (Binary Score) - MOVED TO DYNAMIC CALCULATION IN APP
        # bv_gdf['heb_jaccueille_score'] = (bv_gdf['heb_jaccueille_count'] > 0).astype(float)
             
        # --- 2. Scaling ---
        def get_min_max(series):
             return series.quantile(0.01), series.quantile(0.99)
            
        def scale_series(series, min_val, max_val):
             if max_val == min_val: return 0.0
             return ((series - min_val) / (max_val - min_val)).clip(0, 1)


        if 'lien_social_density' in bv_gdf.columns:
            min_b, max_b = get_min_max(bv_gdf['lien_social_density'])
            bv_gdf['inc_asso_core_scaled'] = scale_series(bv_gdf['lien_social_density'], min_b, max_b)

        if 'inc_asso_refug_density' in bv_gdf.columns:
            min_b, max_b = get_min_max(bv_gdf['inc_asso_refug_density'])
            bv_gdf['inc_asso_refug_scaled'] = scale_series(bv_gdf['inc_asso_refug_density'], min_b, max_b)
        
        if 'inc_siae_density' in bv_gdf.columns:
            min_b, max_b = get_min_max(bv_gdf['inc_siae_density'])
            bv_gdf['inc_siae_density_scaled'] = scale_series(bv_gdf['inc_siae_density'], min_b, max_b)
            
        if 'bpe_creches_density' in bv_gdf.columns:
            min_b, max_b = get_min_max(bv_gdf['bpe_creches_density'])
            bv_gdf['edu_creches_scaled'] = scale_series(bv_gdf['bpe_creches_density'], min_b, max_b)

        # --- 3. Weighted Averages from Communes ---
        metrics_to_avg = [
            'inc_services_core_scaled', 
            'inc_asso_refug_scaled',
            'inc_siae_density_scaled',
            'edu_classes_ferm_scaled', 
            'log_vac_scaled', 
            'log_occup_scaled',
            'log_soc_inoc_scaled',
            'edu_petite_enfance_scaled',
            'sante_hopital_scaled', 'sante_maternite_scaled', 'sante_psy_scaled',
            'edu_lycee_scaled', 'edu_college_scaled',
            'edu_maternelle_scaled', 'edu_elementaire_scaled',
            'youth_decline_scaled', 'workclass_decline_scaled',
            'heb_centres_heb_scaled', 'heb_foyers_scaled', 
            'heb_loc_iml_scaled', 'heb_asso_habitant_scaled'
        ]
        
        # Idempotency: Drop existing metrics to prevent duplication during merge
        cols_to_drop_bv = [col for col in metrics_to_avg if col in bv_gdf.columns]
        # Also drop _x, _y variants if they exist from failed runs
        for col in metrics_to_avg:
            if f"{col}_x" in bv_gdf.columns: cols_to_drop_bv.append(f"{col}_x")
            if f"{col}_y" in bv_gdf.columns: cols_to_drop_bv.append(f"{col}_y")
            
        if cols_to_drop_bv:
            bv_gdf.drop(columns=cols_to_drop_bv, inplace=True)
        
        communes_subset = communes_df[['codgeo', 'bassin_de_vie', 'population'] + [m for m in metrics_to_avg if m in communes_df.columns]].copy()
        
        if 'bassin_de_vie' in communes_subset.columns:
            for metric in metrics_to_avg:
                if metric in communes_subset.columns:
                    # weighted average
                    communes_subset[f'{metric}_w'] = communes_subset[metric] * communes_subset['population']
            
            grouped = communes_subset.groupby('bassin_de_vie', observed=True)
            
            bv_aggs = pd.DataFrame(index=grouped.groups.keys())
            
            sum_pop = grouped['population'].sum()
            
            for metric in metrics_to_avg:
                if metric in communes_subset.columns:
                    bv_aggs[metric] = grouped[f'{metric}_w'].sum() / sum_pop
            
            # Merge back
            if 'bassin_de_vie' in bv_gdf.columns:
                bv_gdf = bv_gdf.merge(bv_aggs, left_on='bassin_de_vie', right_index=True, how='left')
            else:
                 # assume index matches if sorted? Safe to use merge if we have key.
                 # If bv_gdf has 'bassin_de_vie' as column.
                 pass
                 
        # --- 4. Special cases ---
        # Clean up
        # Clean up
        if 'geometry' in bv_gdf.columns:
             bv_gdf['polygon'] = bv_gdf.geometry.to_wkb()
             bv_gdf.drop(columns=['geometry'], inplace=True)

        # Robust index reset to avoid level_0 duplication
        bv_export = pd.DataFrame(bv_gdf)
        if 'level_0' in bv_export.columns:
            bv_export.drop(columns=['level_0'], inplace=True)
            
        bv_export.reset_index().to_parquet(bv_path, compression='brotli', index=False, engine='fastparquet')
        logger.log_step("score_bassins_de_vie", "COMPLETED", {"rows": len(bv_gdf)})

    except Exception as e:
        logger.log_step("score_bassins_de_vie", "ERROR", {"error": str(e)})
        logging.error(f"Score BV failed: {e}")

def main(argv=None):
    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)
    apply_prescoring(config, logger)
    score_bassins_de_vie(config, logger)

if __name__ == "__main__":
    main()
