from shapely import wkb
import logging
import pandas as pd
import geopandas as gpd
import numpy as np
from typing import Dict, Any

from pipeline.common import (
    PipelineLogger, load_config,
    CONFIG_FILE, CACHE_DIR, OUTPUT_DIR, CLEAN_DIR, STATUS_FILE
)
import app.config as cfg

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def apply_prescoring(config: Dict[str, Any], logger: PipelineLogger):
    """Applies pre-scoring logic (ratios, densities, scaling) to odis_communes."""
    logger.log_step("apply_prescoring", "STARTED")
    try:
        communes_path = OUTPUT_DIR / "odis_communes.parquet"
        if not communes_path.exists():
             logger.error(f"Input file not found: {communes_path}")
             logger.log_step("apply_prescoring", "FAILED", {"reason": "Input file not found"})
             return

        # Read as standard Parquet (WKB)
        communes_df = pd.read_parquet(communes_path)
        
        # Convert WKB to Geometry
        if 'polygon' in communes_df.columns:
            communes_df['geometry'] = communes_df['polygon'].apply(lambda x: wkb.loads(bytes(x)))
            communes_gdf = gpd.GeoDataFrame(communes_df, geometry='geometry', crs=cfg.PROJECTED_CRS)
        else:
            # Fallback
            communes_gdf = gpd.GeoDataFrame(communes_df, geometry='geometry')

        logger.log_step("apply_prescoring_load", "LOADED", {"rows": len(communes_gdf)})

        # --- Calculated Columns ---
        
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
        if 'bassin_emploi' in communes_gdf.columns and 'pop_active' in communes_gdf.columns:
             pop_active_be = communes_gdf.groupby('bassin_emploi', observed=True)['pop_active'].transform('sum')
             
             # metiers_offres_ratio
             # metiers_offres_diff is total offers in BE.
             if 'metiers_offres_diff' in communes_gdf.columns:
                 communes_gdf['metiers_offres_ratio'] = np.where(
                     pop_active_be > 0,
                     communes_gdf['metiers_offres_diff'] / pop_active_be,
                     0.0
                 )
                 # Drop metiers_offres_diff as requested
                 communes_gdf.drop(columns=['metiers_offres_diff'], inplace=True)
        
        if 'pop_active' in communes_gdf.columns and 'pop_chomeurs' in communes_gdf.columns:
            communes_gdf['pop_chomage_ratio'] = np.where(
                communes_gdf['pop_active'] > 0,
                communes_gdf['pop_chomeurs'] / communes_gdf['pop_active'],
                0.0
            )

        # --- Pre-calculate Ratios and Scaled Scores (Optimization) ---
        
        # 1. Metiers Ratio (Offers per 1000 active)
        if 'metiers_offres_ratio' in communes_gdf.columns:
            communes_gdf['met_ratio'] = communes_gdf['metiers_offres_ratio'] * 1000
        
        # 2. Logement Vacant Structurel Ratio
        # 2. Logement Vacant Structurel Ratio
        if 'log_priv_total' in communes_gdf.columns and 'log_priv_vacant_plus_2ans' in communes_gdf.columns:
            communes_gdf['log_vac_struct_ratio'] = np.where(
                communes_gdf['log_priv_total'] > 0,
                communes_gdf['log_priv_vacant_plus_2ans'] / communes_gdf['log_priv_total'],
                0.0
            )
        
        # 3. Lien Social Density (Associations per 1000 hab)
        if 'population' in communes_gdf.columns and 'lien_social_count' in communes_gdf.columns:
            communes_gdf['lien_social_density'] = np.where(
                communes_gdf['population'] > 0,
                (communes_gdf['lien_social_count'] * 1000) / communes_gdf['population'],
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
        
        # --- Load Configuration ---
        import yaml
        from pathlib import Path
        
        # Load App Config for Scores (Source of Truth)
        # We access the sibling 'app' directory
        app_config_path = Path(__file__).parent.parent / "app" / "scores_config.yaml"
        scores_config = {}
        socle_admin_list = []
        
        if app_config_path.exists():
            with open(app_config_path, 'r') as f:
                full_config = yaml.safe_load(f)
                # Parse scores config into a dict for easy lookup: id -> {min: x, max: y}
                if 'scores' in full_config:
                    for s in full_config['scores']:
                        scores_config[s['id']] = {
                            'min': s.get('min_bound'), 
                            'max': s.get('max_bound')
                        }
        else:
             logging.warning(f"App config not found at {app_config_path}")

        # Note: socle_admin list is separate... currently in prescoring_config or app?
        # User wants unified. socle_admin is in app/config.py usually, not scores_config.yaml.
        # But we previously created prescoring_config.yaml for it.
        # We will keep prescoring_config ONLY for socle_admin if it's not in scores_config?
        # Or better: Read it from prescoring_config if exists, else empty?
        # User said "Pipeline vs App configs" split was bad.
        # Let's check prescoring_config.yaml content. It has socle_admin.
        # We can keep prescoring_config.yaml JUST for pipeline-specific inputs that aren't scores.
        # OR put socle_admin in scores_config? No, it's a list of slugs.
        # Let's keep prescoring_config.yaml for INPUTS (socle slugs) but use scores_config.yaml for BOUNDS.
        
        prescoring_conf_path = Path(__file__).parent / "prescoring_config.yaml"
        if prescoring_conf_path.exists():
             with open(prescoring_conf_path, 'r') as f:
                 prescoring_conf = yaml.safe_load(f)
             default_socle_admin = prescoring_conf.get('socle_admin', [])
        else:
             default_socle_admin = []


        # --- Scaling ---
        def get_min_max(series):
            return series.quantile(0.01), series.quantile(0.99)
            
        def scale_series(series, min_b, max_b, inverted=False):
            if max_b is None or min_b is None:
                 # Fallback to auto-detection (should check caller, but safe here)
                 min_b, max_b = get_min_max(series)
                 
            if max_b == min_b: return 1.0 if inverted else 0.0
            
            # Cast bounds to float just in case
            min_b, max_b = float(min_b), float(max_b)
            
            denom = max_b - min_b
            if denom == 0:
                scaled = pd.Series(0.0 if not inverted else 1.0, index=series.index)
            else:
                scaled = (series - min_b) / denom
                
            if inverted and denom != 0:
                scaled = 1.0 - scaled
            return scaled.clip(0, 1)

        # Helper to get bounds from config or auto-calc
        def process_scaling(df, col_name, output_col, inverted=False):
            if col_name not in df.columns: return
            
            conf = scores_config.get(output_col, {})
            c_min, c_max = conf.get('min'), conf.get('max')
            
            # If config has bounds, use them. Else auto-calc.
            if c_min is not None and c_max is not None:
                min_b, max_b = c_min, c_max
            else:
                min_b, max_b = get_min_max(df[col_name])
                
            df[output_col] = scale_series(df[col_name], min_b, max_b, inverted)

        
        process_scaling(communes_gdf, 'met_ratio', 'met_scaled')
        
        # loyer_abordable_scaled (Lower is Better)
        # Custom logic for this one? Or standard inverted?
        # It was: ((max_b - val) / (max - min)). clip(0,1). This is exactly inverted MinMax.
        process_scaling(communes_gdf, 'loyer_app_m2', 'loyer_abordable_scaled', inverted=True)

        process_scaling(communes_gdf, 'log_vac_struct_ratio', 'log_vac_scaled')
        process_scaling(communes_gdf, 'lien_social_density', 'inc_lien_social_score')
        process_scaling(communes_gdf, 'population', 'inc_population_scaled')
        
        # inc_pol_scaled (already 0-1)
        if 'pol_num' in communes_gdf.columns:
            communes_gdf['inc_pol_scaled'] = communes_gdf['pol_num']

        process_scaling(communes_gdf, 'log_pp_occup', 'log_occup_scaled')

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
            'total_eleves', 'ecoles_count', 
            'log_total', 'log_soc_total', 'log_soc_inoccupes',
            'pol_num', 'log_priv_vacant_plus_2ans',
            # Unused columns identified in cleanup
            'edu_maternelle_ct', 'edu_elementaire_ct', 'edu_college_ct', 'edu_lycee_ct',
            'svc_incl_count',
            'count_hopital', 'count_psy', 'count_maternite',
            'log_soc_inoc_ratio', 'log_pp_occup',
            'metiers_offres_ratio', 'pop_chomage_ratio', 'met_ratio',
            'log_vac_struct_ratio', 'lien_social_density', 'risque_fermeture_ratio', 'bpe_creches_density',
            'edu_pe_tx_couverture', 'bpe_creches_count', # Dropped after use in scaling
            'lien_social_count', # Dropped after use in scaling
            'pop_active', 'pop_employes', 'pop_chomeurs' # Dropped after use in ratios
        ]
        
        # --- Socle Administratif (Pre-calculated) ---
        # Load POIs to get inclusion services
        pois_path = OUTPUT_DIR / "pois.parquet"
        if pois_path.exists():
            try:
                # Import prescoring config
                import yaml
                from pathlib import Path
                prescoring_conf_path = Path(__file__).parent / "prescoring_config.yaml"
                if prescoring_conf_path.exists():
                     with open(prescoring_conf_path, 'r') as f:
                         prescoring_conf = yaml.safe_load(f)
                     default_socle_admin = prescoring_conf.get('socle_admin', [])
                else:
                     logging.warning("prescoring_config.yaml not found, using empty socle admin.")
                     default_socle_admin = []

                
                pois_df = pd.read_parquet(pois_path)
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
                        communes_gdf['inc_socle_admin_score'] = communes_gdf['codgeo'].map(socle_scores)
                        communes_gdf['inc_socle_admin_score'] = communes_gdf['inc_socle_admin_score'].fillna(0.0)
                    else:
                         communes_gdf['inc_socle_admin_score'] = 0.0

                    logger.log_step("inc_socle_admin_score", "CALCULATED")
                else:
                    communes_gdf['inc_socle_admin_score'] = 0.0
                    
            except Exception as e:
                logging.error(f"Failed to calculate socle admin score at line {e.__traceback__.tb_lineno}: {e}")
                import traceback
                traceback.print_exc()
                communes_gdf['inc_socle_admin_score'] = 0.0
        else:
             logging.warning("pois.parquet not found, skipping socle admin score")
             communes_gdf['inc_socle_admin_score'] = 0.0

        communes_gdf.drop(columns=[c for c in cols_to_drop if c in communes_gdf.columns], inplace=True)
        
        # Additional drop request from user
        more_cols_to_drop = [
            'pop_jeune_2016', 'pop_jeune_2022', 'pop_active_2016', 'pop_active_2022',
            'libelle_bassin_de_vie', 'loyer_app_m2', 'has_gare', 'gare_count', 
            'risky_schools_count', 'log_priv_total'
        ]
        communes_gdf.drop(columns=[c for c in more_cols_to_drop if c in communes_gdf.columns], inplace=True)

        # Optimization: Cast floats to float32 (float16 caused UI issues and overflow)
        exclude_cols = {'population', 'plm'}
        for col in communes_gdf.select_dtypes(include=['float64']).columns:
             # We generally want everything float32
             communes_gdf[col] = communes_gdf[col].astype('float32')
        
        if 'inc_socle_admin_score' not in communes_gdf.columns:
            communes_gdf['inc_socle_admin_score'] = 0.0

        # Save
        # Save
        if 'geometry' in communes_gdf.columns:
             communes_gdf['polygon'] = communes_gdf.geometry.to_wkb()
             communes_gdf.drop(columns=['geometry'], inplace=True)
             
        pd.DataFrame(communes_gdf).to_parquet(communes_path)
        logger.log_step("apply_prescoring", "COMPLETED", {"columns": len(communes_gdf.columns), "path": str(communes_path), "rows": len(communes_gdf)})

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
        bv_df = pd.read_parquet(bv_path)
        if 'polygon' in bv_df.columns:
             bv_df['geometry'] = bv_df['polygon'].apply(lambda x: wkb.loads(bytes(x)))
             bv_gdf = gpd.GeoDataFrame(bv_df, geometry='geometry', crs=cfg.PROJECTED_CRS)
        else:
             bv_gdf = gpd.GeoDataFrame(bv_df, geometry='geometry')

        # Read as standard Parquet (WKB) - Communes
        communes_df = pd.read_parquet(communes_path)
        # We don't need geometry for communes here, just scores.

        
        # We need Aggregated Counts which should be in 'bv_gdf' if build.py did its job.
        
        # --- 1. Ratios & Densities ---
        
        # Metiers Ratio (Active Pop / Offers)
        # Note: 'metiers_offres_diff' might be missing if we dropped it in build?
        # build.py aggregates 'metiers_offres_diff'.
        if 'metiers_offres_diff' in bv_gdf.columns and 'pop_active' in bv_gdf.columns:
             bv_gdf['met_ratio'] = np.where(
                 bv_gdf['pop_active'] > 0,
                 bv_gdf['metiers_offres_diff'] / bv_gdf['pop_active'] * 1000,
                 0.0
             )
        
        # Lien Social
        if 'lien_social_count' in bv_gdf.columns and 'population_bv' in bv_gdf.columns:
             bv_gdf['lien_social_density'] = np.where(
                 bv_gdf['population_bv'] > 0,
                 bv_gdf['lien_social_count'] / bv_gdf['population_bv'] * 1000,
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
             
        # --- 2. Scaling ---
        def get_min_max(series):
             return series.quantile(0.01), series.quantile(0.99)
            
        def scale_series(series, min_val, max_val):
             if max_val == min_val: return 0.0
             return ((series - min_val) / (max_val - min_val)).clip(0, 1)

        if 'met_ratio' in bv_gdf.columns:
            min_b, max_b = get_min_max(bv_gdf['met_ratio'])
            bv_gdf['met_scaled'] = scale_series(bv_gdf['met_ratio'], min_b, max_b)

        if 'lien_social_density' in bv_gdf.columns:
            min_b, max_b = get_min_max(bv_gdf['lien_social_density'])
            bv_gdf['inc_lien_social_score'] = scale_series(bv_gdf['lien_social_density'], min_b, max_b)
            
        if 'bpe_creches_density' in bv_gdf.columns:
            min_b, max_b = get_min_max(bv_gdf['bpe_creches_density'])
            bv_gdf['edu_creches_scaled'] = scale_series(bv_gdf['bpe_creches_density'], min_b, max_b)

        # --- 3. Weighted Averages from Communes ---
        metrics_to_avg = [
            'inc_socle_admin_score', 
            'edu_classes_ferm_scaled', 
            'log_vac_scaled', 
            'log_occup_scaled',
            'log_soc_inoc_scaled',
            'edu_petite_enfance_scaled',
            'sante_hopital_scaled', 'sante_maternite_scaled', 'sante_psy_scaled',
            'edu_lycee_scaled', 'edu_college_scaled',
            'edu_maternelle_scaled', 'edu_elementaire_scaled',
            'youth_decline_scaled', 'workclass_decline_scaled'
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
        if 'population_bv' in bv_gdf.columns:
            min_b, max_b = get_min_max(bv_gdf['population_bv'])
            bv_gdf['inc_population_scaled'] = scale_series(bv_gdf['population_bv'], min_b, max_b)
            
        # Clean up
        # Clean up
        if 'geometry' in bv_gdf.columns:
             bv_gdf['polygon'] = bv_gdf.geometry.to_wkb()
             bv_gdf.drop(columns=['geometry'], inplace=True)

        pd.DataFrame(bv_gdf).to_parquet(bv_path)
        logger.log_step("score_bassins_de_vie", "COMPLETED", {"rows": len(bv_gdf)})

    except Exception as e:
        logger.log_step("score_bassins_de_vie", "ERROR", {"error": str(e)})
        logging.error(f"Score BV failed: {e}")

def main():
    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)
    apply_prescoring(config, logger)
    score_bassins_de_vie(config, logger)

if __name__ == "__main__":
    main()
