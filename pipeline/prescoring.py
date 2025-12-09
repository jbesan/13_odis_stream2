import logging
import pandas as pd
import geopandas as gpd
import numpy as np
from typing import Dict, Any

from pipeline.common import (
    PipelineLogger, load_config,
    CONFIG_FILE, CACHE_DIR, OUTPUT_DIR, CLEAN_DIR, STATUS_FILE
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def apply_prescoring(config: Dict[str, Any], logger: PipelineLogger):
    """Applies pre-scoring logic (ratios, densities, scaling) to odis_communes."""
    logger.log_step("apply_prescoring", "STARTED")
    try:
        communes_path = OUTPUT_DIR / "odis_communes.parquet"
        if not communes_path.exists():
            logging.error("odis_communes.parquet not found. Run build first.")
            return

        communes_gdf = gpd.read_parquet(communes_path)
        logging.info(f"Loaded {len(communes_gdf)} communes for prescoring.")

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
             pop_active_be = communes_gdf.groupby('bassin_emploi')['pop_active'].transform('sum')
             
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

        # 4. Risque Fermeture Ratio (Avg School Size)
        if 'total_eleves' in communes_gdf.columns and 'ecoles_count' in communes_gdf.columns:
            communes_gdf['risque_fermeture_ratio'] = np.where(
                communes_gdf['ecoles_count'] > 0,
                communes_gdf['total_eleves'] / communes_gdf['ecoles_count'],
                0.0
            )

        # 5. Creches Density (Places/Structures per 1000 hab - using count of structures for now)
        if 'population' in communes_gdf.columns and 'bpe_creches_count' in communes_gdf.columns:
            communes_gdf['bpe_creches_density'] = np.where(
                communes_gdf['population'] > 0,
                (communes_gdf['bpe_creches_count'] * 1000) / communes_gdf['population'],
                0.0
            )
        
        # --- Scaling ---
        def get_min_max(series):
            return series.quantile(0.01), series.quantile(0.99)
            
        def scale_series(series, min_val, max_val):
            if max_val == min_val: return 0.0
            return ((series - min_val) / (max_val - min_val)).clip(0, 1)

        if 'met_ratio' in communes_gdf.columns:
            min_b, max_b = get_min_max(communes_gdf['met_ratio'])
            communes_gdf['met_scaled'] = scale_series(communes_gdf['met_ratio'], min_b, max_b)

        # loyer_abordable_scaled (Lower is Better)
        if 'loyer_app_m2' in communes_gdf.columns:
            min_b, max_b = get_min_max(communes_gdf['loyer_app_m2'])
            # Inverted scale: (max - val) / (max - min)
            if max_b > min_b:
                communes_gdf['loyer_abordable_scaled'] = ((max_b - communes_gdf['loyer_app_m2']) / (max_b - min_b)).clip(0, 1)
            else:
                communes_gdf['loyer_abordable_scaled'] = 0.5 # Default if no variance
        
        # log_vac_scaled
        if 'log_vac_struct_ratio' in communes_gdf.columns:
            min_b, max_b = get_min_max(communes_gdf['log_vac_struct_ratio'])
            communes_gdf['log_vac_scaled'] = scale_series(communes_gdf['log_vac_struct_ratio'], min_b, max_b)
        
        # inc_lien_social_score
        if 'lien_social_density' in communes_gdf.columns:
            min_b, max_b = get_min_max(communes_gdf['lien_social_density'])
            communes_gdf['inc_lien_social_score'] = scale_series(communes_gdf['lien_social_density'], min_b, max_b)
        
        # inc_population_scaled
        if 'population' in communes_gdf.columns:
            min_b, max_b = get_min_max(communes_gdf['population'])
            communes_gdf['inc_population_scaled'] = scale_series(communes_gdf['population'], min_b, max_b)
        
        # inc_pol_scaled (already 0-1)
        if 'pol_num' in communes_gdf.columns:
            communes_gdf['inc_pol_scaled'] = communes_gdf['pol_num']

        # log_occup_scaled
        if 'log_pp_occup' in communes_gdf.columns:
            min_b, max_b = get_min_max(communes_gdf['log_pp_occup'])
            communes_gdf['log_occup_scaled'] = scale_series(communes_gdf['log_pp_occup'], min_b, max_b)

        # log_soc_inoc_scaled
        if 'log_soc_inoc_ratio' in communes_gdf.columns:
            min_b, max_b = get_min_max(communes_gdf['log_soc_inoc_ratio'])
            communes_gdf['log_soc_inoc_scaled'] = scale_series(communes_gdf['log_soc_inoc_ratio'], min_b, max_b)

        # edu_classes_ferm_scaled
        if 'risque_fermeture_ratio' in communes_gdf.columns:
            min_b, max_b = get_min_max(communes_gdf['risque_fermeture_ratio'])
            communes_gdf['edu_classes_ferm_scaled'] = scale_series(communes_gdf['risque_fermeture_ratio'], min_b, max_b)

        # edu_creches_scaled
        if 'bpe_creches_density' in communes_gdf.columns:
            min_b, max_b = get_min_max(communes_gdf['bpe_creches_density'])
            communes_gdf['edu_creches_scaled'] = scale_series(communes_gdf['bpe_creches_density'], min_b, max_b)

        # edu_petite_enfance_scaled
        if 'edu_pe_tx_couverture' in communes_gdf.columns:
            # Only scale valid values (not 0 if 0 means missing? No, 0 is 0 coverage. NaN is missing.)
            # build.py fills NaNs with 0 for this column? Line 130: 'edu_pe_tx_couverture' in numeric_cols.
            # So it is 0.
            min_b, max_b = get_min_max(communes_gdf['edu_pe_tx_couverture'])
            communes_gdf['edu_petite_enfance_scaled'] = scale_series(communes_gdf['edu_pe_tx_couverture'], min_b, max_b)

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
        
        logging.info(f"Columns after static scoring: {[c for c in communes_gdf.columns if 'scaled' in c]}")

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
                # Import config from app to get DEFAULT_SOCLE_ADMIN
                import sys
                import os
                sys.path.append(os.getcwd())
                from app import config as app_cfg
                
                pois_df = pd.read_parquet(pois_path)
                incl_pois = pois_df[pois_df['category'] == 'incl_services'].copy()
                
                if not incl_pois.empty:
                    # Parse 'type' column (stringified list)
                    import ast
                    def parse_types(x):
                        try:
                            return ast.literal_eval(x)
                        except:
                            return []
                    
                    # Ensure 'type' is string (not categorical) before apply
                    incl_pois['services'] = incl_pois['type'].astype(str).apply(parse_types)
                    incl_pois = incl_pois.explode('services')
                    
                    # Drop NaNs and ensure strings to avoid unhashable type error
                    incl_pois = incl_pois.dropna(subset=['services'])
                    incl_pois = incl_pois[incl_pois['services'].apply(lambda x: isinstance(x, str))]
                    
                    # Group by codgeo
                    commune_services = incl_pois.groupby('codgeo')['services'].apply(set).to_dict()
                    
                    needed_services = set(app_cfg.DEFAULT_SOCLE_ADMIN)
                    
                    # Ensure we are using codgeo as index (Critical for map)
                    if 'codgeo' in communes_gdf.columns:
                        communes_gdf.set_index('codgeo', inplace=True, drop=False)
                    
                    def calculate_socle_score(codgeo):
                        available = commune_services.get(codgeo, set())
                        matches = 0
                        for needed in needed_services:
                            # Check if needed slug is strictly present OR is a substring of any available service key
                            if any(needed in av for av in available):
                                matches += 1
                        return matches / len(needed_services) if needed_services else 0.0

                    communes_gdf['inc_socle_admin_score'] = communes_gdf.index.map(calculate_socle_score).fillna(0.0)
                    logging.info("Calculated inc_socle_admin_score")
                    
            except Exception as e:
                logging.error(f"Failed to calculate socle admin score: {e}")
                communes_gdf['inc_socle_admin_score'] = 0.0
        else:
             logging.warning("pois.parquet not found, skipping socle admin score")
             communes_gdf['inc_socle_admin_score'] = 0.0

        communes_gdf.drop(columns=[c for c in cols_to_drop if c in communes_gdf.columns], inplace=True)
        
        # Save
        communes_gdf.to_parquet(communes_path)
        logger.log_step("apply_prescoring", "UPDATED", {"path": str(communes_path), "rows": len(communes_gdf)})

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

        bv_gdf = gpd.read_parquet(bv_path)
        communes_df = pd.read_parquet(communes_path)
        
        # We need Aggregated Counts which should be in 'bv_gdf' if build.py did its job.
        # Check columns
        logging.info(f"BV Columns: {bv_gdf.columns}")
        
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
            'edu_maternelle_scaled', 'edu_elementaire_scaled'
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
            
            grouped = communes_subset.groupby('bassin_de_vie')
            
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
        logger.log_step("score_bassins_de_vie", "UPDATED", {"path": str(bv_path)})
        bv_gdf.to_parquet(bv_path)

    except Exception as e:
        logger.log_step("score_bassins_de_vie", "ERROR", {"error": str(e)})

def main():
    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)
    apply_prescoring(config, logger)
    score_bassins_de_vie(config, logger)

if __name__ == "__main__":
    main()
