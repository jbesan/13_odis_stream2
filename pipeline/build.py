import argparse
import logging
import pandas as pd
import geopandas as gpd
import json
import logging
import shutil
import numpy as np
from pathlib import Path
from typing import Dict, Any

from pipeline.common import (
    PipelineLogger, load_config, load_dataset,
    CONFIG_FILE, CACHE_DIR, CLEAN_DIR, OUTPUT_DIR, STATUS_FILE
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def build_communes(config: Dict[str, Any], logger: PipelineLogger) -> gpd.GeoDataFrame:
    """Builds the main ODIS Communes dataset."""
    logger.log_step("build_communes", "STARTED")
    try:
        # 1. Load Base Communes (Clean)
        communes_path = CLEAN_DIR / "communes.parquet"
        if not communes_path.exists():
            logging.error("Clean Communes file not found. Run ingest first.")
            return gpd.GeoDataFrame()
            
        communes_gdf = gpd.read_parquet(communes_path)
        
        # 2. Merge Indicators
        # Helper to merge
        def merge_clean(name: str, cols: list = None):
            nonlocal communes_gdf
            path = CLEAN_DIR / f"{name}.parquet"
            if path.exists():
                df = pd.read_parquet(path)
                if cols:
                    # Ensure codgeo is present
                    cols_to_use = ['codgeo'] + [c for c in cols if c in df.columns and c != 'codgeo']
                    df = df[cols_to_use]
                communes_gdf = communes_gdf.merge(df, on='codgeo', how='left')
            else:
                logging.warning(f"Clean {name} file not found.")

        # Merge BMO (Stats only + code_be)
        merge_clean("bmo_stats", ['metiers_offres_diff', 'code_be'])
        
        # Merge Population
        merge_clean("population", ['population'])
        
        # Merge Population Active
        merge_clean("population_active", ['pop_active', 'pop_employes', 'pop_chomeurs'])
        
        # Merge LOVAC
        merge_clean("lovac", ['pp_vacant_plus_2ans_25'])
        
        # Merge RPLS
        merge_clean("rpls", ['log_soc_total', 'log_soc_inoccupes'])
        
        # Merge CAF
        merge_clean("caf", ['taux_couverture'])
        
        # Merge Education
        merge_clean("education", ['edu_maternelle_ct', 'edu_elementaire_ct', 'edu_college_ct', 'edu_lycee_ct'])
        
        # Merge Inclusion
        merge_clean("inclusion", ['svc_incl_count'])
        
        # Merge Political
        merge_clean("political", ['pol_num'])
        
        # Merge Housing Occupation
        merge_clean("housing_occupation", ['MOD_OVER_OCC', 'MOD_UNDER_OCC', 'SEV_OVER_OCC', 'SEV_UNDER_OCC', 'STD_OCC', 'VSEV_UNDER_OCC'])
        
        # Merge School Effectifs
        merge_clean("school_effectifs", ['total_eleves', 'ecoles_count'])
        
        # Merge Associations (Vertical, so we only merge count if needed, or skip here)
        # Actually, we might want a count of associations per commune in the main table?
        # The user said "vertical lookup tables dedicated... rather than adding columns".
        # But a total count is useful. Let's keep 'lien_social_count' if we can derive it from vertical or if we still have it.
        # In ingest.py, I changed clean_associations to output vertical. I didn't keep a count file.
        # So I should probably load the vertical file and aggregate it here to get a count, OR just skip it in the main table.
        # Let's skip it in the main table for now as requested, or calculate it on the fly.
        # I'll calculate it on the fly from vertical if possible, or just leave it out if the user wants purely vertical.
        # "je pense qu'il faudrait des tables de lookup dédiées 'verticales' plutot que d'ajouter des colonnes avec supplémentaires"
        # This likely refers to the LIST columns. A simple count is probably fine.
        # Let's try to load vertical and count.
        assoc_path = CLEAN_DIR / "associations_vertical.parquet"
        if assoc_path.exists():
            assoc_df = pd.read_parquet(assoc_path)
            assoc_count = assoc_df.groupby('codgeo').size().rename('lien_social_count').reset_index()
            communes_gdf = communes_gdf.merge(assoc_count, on='codgeo', how='left')
            
        # Merge Top Metiers (from bmo_vertical)
        # We need a list of top metiers per commune for scoring (metiers_offres_top5)
        bmo_vert_path = CLEAN_DIR / "bmo_vertical.parquet"
        if bmo_vert_path.exists():
            bmo_vert = pd.read_parquet(bmo_vert_path)
            # Group by codgeo and aggregate fap_code into list
            top_metiers = bmo_vert.groupby('codgeo')['fap_code'].apply(list).rename('metiers_offres_top5').reset_index()
            communes_gdf = communes_gdf.merge(top_metiers, on='codgeo', how='left')
            
        # Merge Voisins
        merge_clean("voisins", ['codgeo_voisins'])
        
        # 3. Fill NaNs for numeric columns
        # 3. Renames and Calculations
        
        # Renames
        rename_map = {
            'taux_couverture': 'edu_pe_tx_couverture',
            'pp_vacant_plus_2ans_25': 'log_priv_vacant_plus_2ans',
            'code_be': 'bassin_emploi',
            'nom': 'libgeo',
            'departement': 'dep_code',
            'region': 'reg_code',
            'epci': 'epci_code'
        }
        communes_gdf.rename(columns=rename_map, inplace=True)
        
        # Fill NaNs
        numeric_cols = [
            'population', 'log_soc_total', 'log_soc_inoccupes', 
            'edu_maternelle_ct', 'edu_elementaire_ct', 'edu_college_ct', 'edu_lycee_ct',
            'lien_social_count', 'svc_incl_count', 
            'pop_active', 'pop_employes', 'pop_chomeurs', 
            'metiers_offres_diff', 'log_priv_vacant_plus_2ans', 'edu_pe_tx_couverture'
        ]
        for col in numeric_cols:
            if col in communes_gdf.columns:
                communes_gdf[col] = communes_gdf[col].fillna(0)
                
        # Ensure epci_nom exists (placeholder if missing)
        if 'epci_nom' not in communes_gdf.columns:
            if 'epci_code' in communes_gdf.columns:
                communes_gdf['epci_nom'] = communes_gdf['epci_code'] # Fallback
            else:
                communes_gdf['epci_nom'] = "Inconnu"
                
        # Calculated Columns
        # log_soc_tx_vacant
        communes_gdf['log_soc_tx_vacant'] = np.where(
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
        
        # Ensure columns exist and fillna
        occup_cols = ['SEV_OVER_OCC', 'MOD_OVER_OCC', 'STD_OCC', 'MOD_UNDER_OCC', 'SEV_UNDER_OCC', 'VSEV_UNDER_OCC']
        for col in occup_cols:
            if col not in communes_gdf.columns:
                communes_gdf[col] = 0.0
            else:
                communes_gdf[col] = communes_gdf[col].fillna(0.0)
                
        # Total households for occupancy (sum of all categories)
        total_occup_households = communes_gdf[occup_cols].sum(axis=1)
        communes_gdf['log_total'] = total_occup_households # Use as log_total (RP)
        
        # Weighted Sum
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
        
        # Rounding
        for col in ['pop_active', 'pop_employes', 'pop_chomeurs']:
            if col in communes_gdf.columns:
                communes_gdf[col] = communes_gdf[col].round(0).astype(int)
        
        # Centroids
        communes_gdf['centroid'] = communes_gdf.geometry.centroid
                
        # 4. Bassins de Vie Mapping (for pop_be)
        # We need to load BV mapping. It's in the raw zip usually, but we can extract it or maybe we should have cleaned it?
        # Let's load it from cache as in etl.py
        bv_cfg = config['sources']['bassins_de_vie']
        bv_path = CACHE_DIR / bv_cfg['archive_file']
        
        if bv_path.exists():
            bv_df = load_dataset(bv_path, bv_cfg)
            bv_df = bv_df.rename(columns={'Code géographique': 'CODGEO', 'Bassin de vie 2022': 'bassin_de_vie'})
            if 'CODGEO' in bv_df.columns and 'bassin_de_vie' in bv_df.columns:
                bv_df['CODGEO'] = bv_df['CODGEO'].astype(str).str.zfill(5)
                bv_mapping = bv_df[['CODGEO', 'bassin_de_vie']].set_index('CODGEO')
                communes_gdf = communes_gdf.join(bv_mapping, on='codgeo', how='left')
                
                # Calculate pop_active_be for Ratio
                # We need pop_active per commune first. It is already in communes_gdf.
                # Group by bassin_emploi (which is code_be from BMO, but we just joined BV mapping which is bassin_de_vie)
                # Wait, the user asked for "population active du bassin d'emploi".
                # 'bassin_emploi' comes from BMO stats merge.
                
                if 'bassin_emploi' in communes_gdf.columns:
                     pop_active_be = communes_gdf.groupby('bassin_emploi')['pop_active'].transform('sum')
                     
                     # metiers_offres_ratio
                     # metiers_offres_diff is total offers in BE.
                     # So ratio = Offers in BE / Active Pop in BE
                     communes_gdf['metiers_offres_ratio'] = np.where(
                         pop_active_be > 0,
                         communes_gdf['metiers_offres_diff'] / pop_active_be,
                         0.0
                     )
                     
                     # Drop metiers_offres_diff as requested ("renommer la colonne")
                     # We effectively replaced it.
                     communes_gdf.drop(columns=['metiers_offres_diff'], inplace=True)
                
                # pop_chomage_ratio
                communes_gdf['pop_chomage_ratio'] = np.where(
                    communes_gdf['pop_active'] > 0,
                    communes_gdf['pop_chomeurs'] / communes_gdf['pop_active'],
                    0.0
                )

        # --- Pre-calculate Ratios and Scaled Scores (Optimization) ---
        # Ratios
        communes_gdf['met_ratio'] = np.where(
             communes_gdf['pop_active'] > 0,
             1000 * communes_gdf['metiers_offres_ratio'] * communes_gdf['pop_active'] / communes_gdf['pop_active'], # Re-verify logic: ratio is offers/active. met_ratio in app was 1000 * met / active.
             0.0
        )
        # Wait, metiers_offres_ratio is (offers in BE / active in BE).
        # App used: df['met_ratio'] = 1000 * df['met'] / df['pop_active'] where 'met' was local offers?
        # No, 'met' was BMO offers.
        # If we want to pre-calculate what the app uses:
        # App: df['met_ratio'] = 1000 * df['met'] / df['pop_active']
        # But we replaced 'met' with 'metiers_offres_ratio' which is already a ratio?
        # Let's stick to the requested optimization: "any score that can be min-max scaled before hand... should be there"
        # We need to calculate the raw metric first, then scale it.
        
        # 1. Metiers Ratio (Offers per 1000 active)
        # We already have 'metiers_offres_ratio' = Offers / Active (in BE).
        # So met_ratio = metiers_offres_ratio * 1000.
        communes_gdf['met_ratio'] = communes_gdf['metiers_offres_ratio'] * 1000
        
        # 2. Logement Vacant Structurel Ratio
        communes_gdf['log_vac_struct_ratio'] = np.where(
            communes_gdf['log_total'] > 0,
            communes_gdf['log_priv_vacant_plus_2ans'] / communes_gdf['log_total'],
            0.0
        )
        
        # 3. Lien Social Density (Associations per 1000 hab)
        communes_gdf['lien_social_density'] = np.where(
            communes_gdf['population'] > 0,
            (communes_gdf['lien_social_count'] * 1000) / communes_gdf['population'],
            0.0
        )
        
        # 4. Affinite Density (Associations per 1000 active - wait, app uses active? check scoring.py)
        # scoring.py: df['affinite_density'] = (df['affinite_count'] * 1000) / df['pop_active']
        # We can't pre-calculate affinite_density because it depends on USER SELECTION of interests.
        
        # --- Scaling ---
        # We need global stats to scale.
        # We can compute them on the fly here since we have the full dataset.
        def get_min_max(series):
            return series.quantile(0.01), series.quantile(0.99)
            
        def scale_series(series, min_val, max_val):
            if max_val == min_val: return 0.0
            return ((series - min_val) / (max_val - min_val)).clip(0, 1)

        # met_scaled
        min_b, max_b = get_min_max(communes_gdf['met_ratio'])
        communes_gdf['met_scaled'] = scale_series(communes_gdf['met_ratio'], min_b, max_b)
        
        # log_vac_scaled
        min_b, max_b = get_min_max(communes_gdf['log_vac_struct_ratio'])
        communes_gdf['log_vac_scaled'] = scale_series(communes_gdf['log_vac_struct_ratio'], min_b, max_b)
        
        # inc_lien_social_score
        min_b, max_b = get_min_max(communes_gdf['lien_social_density'])
        communes_gdf['inc_lien_social_score'] = scale_series(communes_gdf['lien_social_density'], min_b, max_b)
        
        # inc_population_scaled
        min_b, max_b = get_min_max(communes_gdf['population'])
        communes_gdf['inc_population_scaled'] = scale_series(communes_gdf['population'], min_b, max_b)
        
        # inc_pol_scaled (already 0-1)
        communes_gdf['inc_pol_scaled'] = communes_gdf['pol_num']

        # --- Drop Unused Columns ---
        cols_to_drop = [
            'MOD_OVER_OCC', 'MOD_UNDER_OCC', 'SEV_OVER_OCC', 'SEV_UNDER_OCC', 'STD_OCC', 'VSEV_UNDER_OCC', # *_OCC
            'total_eleves', 'ecoles_count', 
            'log_total', 'log_soc_total', 'log_soc_inoccupes'
        ]
        # Keep 'log_pp_occup' as it is used for 'log_occup_scaled' (or pre-calculate it?)
        # We can pre-calculate log_occup_scaled too.
        min_b, max_b = get_min_max(communes_gdf['log_pp_occup'])
        communes_gdf['log_occup_scaled'] = scale_series(communes_gdf['log_pp_occup'], min_b, max_b)
        
        # We can drop log_pp_occup if we have the scaled version and don't need raw for anything else.
        # But let's keep it just in case, or drop if requested "unused columns".
        # User said "log_total" specifically.
        
        communes_gdf.drop(columns=[c for c in cols_to_drop if c in communes_gdf.columns], inplace=True)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
        # Rename geometry to polygon to match app expectation
        communes_gdf.rename_geometry('polygon', inplace=True)
        
        output_path = OUTPUT_DIR / "odis_communes.parquet"
        communes_gdf.to_parquet(output_path)
        output_path = OUTPUT_DIR / "odis_communes.parquet"
        communes_gdf.to_parquet(output_path)
        logger.log_step("build_communes", "CREATED", {"path": str(output_path), "rows": len(communes_gdf)})
        
        # Copy bmo_vertical to output
        bmo_vertical_path = CLEAN_DIR / "bmo_vertical.parquet"
        if bmo_vertical_path.exists():
            shutil.copy2(bmo_vertical_path, OUTPUT_DIR / "bmo_vertical.parquet")
            logger.log_step("build_communes", "COPIED", {"file": "bmo_vertical.parquet"})
        
        return communes_gdf

    except Exception as e:
        logger.log_step("build_communes", "ERROR", {"error": str(e)})
        logging.error(f"Build Communes failed: {e}")
        return gpd.GeoDataFrame()

def build_bassins_de_vie(communes_gdf: gpd.GeoDataFrame, config: Dict[str, Any], logger: PipelineLogger):
    """Aggregates Communes to Bassins de Vie."""
    logger.log_step("build_bassins_de_vie", "STARTED")
    try:
        if communes_gdf.empty or 'bassin_de_vie' not in communes_gdf.columns:
            logging.warning("Cannot build BV: Communes empty or missing bassin_de_vie.")
            return

        # Dissolve
        # Fix geometries
        if 'polygon' in communes_gdf.columns:
            communes_gdf = communes_gdf.set_geometry('polygon')
        # communes_gdf['geometry'] = communes_gdf.geometry.buffer(0)
        # communes_gdf['geometry'] = communes_gdf.geometry.make_valid()
        
        from shapely.validation import make_valid
        communes_gdf['geometry'] = communes_gdf.geometry.apply(make_valid)
        
        numeric_cols = [
            'population', 'log_soc_total', 'log_soc_inoccupes', 
            'count_maternelle', 'count_elementaire', 'ecoles_ct',
            'lien_social_count', 'svc_incl_count', 
            'pop_active', 'pop_employes', 'pop_chomeurs', 
            'log_priv_vacant_plus_2ans'
        ]
        # metiers_offres_diff was dropped in build_communes, so we can't sum it here if we load from there.
        # But wait, build_bassins_de_vie takes the returned communes_gdf.
        # If I dropped it, I can't aggregate it.
        # But for BV level, maybe we want the ratio too?
        # The user didn't explicitly ask for ratio in BV dataset, but "renommer la colonne en 'metiers_offres_ratio'" implies globally?
        # Actually, for BV dataset, we aggregate communes.
        # If we want unemployment ratio in BV, we need sum(chomeurs) / sum(active).
        # We have pop_active and pop_chomeurs in numeric_cols.
        
        agg_dict = {col: 'sum' for col in numeric_cols if col in communes_gdf.columns}
        
        bv_gdf = communes_gdf[communes_gdf['bassin_de_vie'].notnull()].dissolve(by='bassin_de_vie', aggfunc=agg_dict)
        bv_gdf.rename(columns={'population': 'population_bv'}, inplace=True)
        
        # Calculate pop_chomage_ratio for BV
        if 'pop_active' in bv_gdf.columns and 'pop_chomeurs' in bv_gdf.columns:
             bv_gdf['pop_chomage_ratio'] = np.where(
                 bv_gdf['pop_active'] > 0,
                 bv_gdf['pop_chomeurs'] / bv_gdf['pop_active'],
                 0.0
             )
        
        # Add Label
        bv_cfg = config['sources']['bassins_de_vie']
        bv_path = CACHE_DIR / bv_cfg['archive_file']
        if bv_path.exists():
            df_bv_source = load_dataset(bv_path, bv_cfg)
            # 'Bassin de vie 2022', 'Libellé géographique du bassin de vie 2022'
            # Rename to match our dissolved index 'bassin_de_vie'
            df_bv_source = df_bv_source.rename(columns={
                'Bassin de vie 2022': 'bassin_de_vie',
                'Libellé géographique du bassin de vie 2022': 'libelle_bassin_de_vie'
            })
            # Deduplicate (one label per BV code)
            labels = df_bv_source[['bassin_de_vie', 'libelle_bassin_de_vie']].drop_duplicates()
            bv_gdf = bv_gdf.merge(labels, on='bassin_de_vie', how='left')
        
        output_path = OUTPUT_DIR / "odis_bassins_de_vie.parquet"
        bv_gdf.to_parquet(output_path)
        logger.log_step("build_bassins_de_vie", "CREATED", {"path": str(output_path), "rows": len(bv_gdf)})

    except Exception as e:
        logger.log_step("build_bassins_de_vie", "ERROR", {"error": str(e)})
        logging.error(f"Build BV failed: {e}")

def build_vertical_tables(config: Dict[str, Any], logger: PipelineLogger):
    """Generates vertical lookup tables."""
    logger.log_step("build_vertical_tables", "STARTED")
    try:
        # 1. Metiers
        bmo_path = CLEAN_DIR / "bmo_vertical.parquet"
        if bmo_path.exists():
            df = pd.read_parquet(bmo_path)
            out = OUTPUT_DIR / "odis_rel_metiers.parquet"
            df.to_parquet(out)
            logger.log_step("build_vertical_tables", "METIERS", {"path": str(out)})
            
        # 2. Associations
        assoc_path = CLEAN_DIR / "associations_vertical.parquet"
        if assoc_path.exists():
            df = pd.read_parquet(assoc_path)
            out = OUTPUT_DIR / "odis_rel_associations.parquet"
            df.to_parquet(out)
            logger.log_step("build_vertical_tables", "ASSOCIATIONS", {"path": str(out)})
            
    except Exception as e:
        logger.log_step("build_vertical_tables", "ERROR", {"error": str(e)})

def generate_pois(config: Dict[str, Any], logger: PipelineLogger):
    """Generates POIs from raw/cache sources."""
    logger.log_step("generate_pois", "STARTED")
    try:
        pois_list = []
        
        # Education
        edu_cfg = config['sources']['education_annuaire']
        edu_path = CACHE_DIR / edu_cfg['local_name']
        if edu_path.exists():
            edu_df = load_dataset(edu_path, edu_cfg)
            # Normalize columns
            edu_df.columns = [c.strip() for c in edu_df.columns]
            
            # Map new columns
            # numero_uai -> id
            # appellation_officielle -> name
            # nature_uai_libe -> type
            # latitude -> lat
            # longitude -> lon
            # secteur_public_prive_libe -> metadata
            
            if 'latitude' in edu_df.columns and 'longitude' in edu_df.columns:
                 edu_df['lat'] = edu_df['latitude']
                 edu_df['lon'] = edu_df['longitude']
            
            # Calculate flags (logic from data_loader.py)
            edu_df['ecole_maternelle'] = edu_df['nature_uai_libe'].str.contains('maternelle', case=False, na=False) | \
                                          edu_df['nature_uai_libe'].str.contains('primaire', case=False, na=False)
            edu_df['ecole_elementaire'] = edu_df['nature_uai_libe'].str.contains('élémentaire', case=False, na=False) | \
                                           edu_df['nature_uai_libe'].str.contains('primaire', case=False, na=False)

            edu_pois = pd.DataFrame({
                'id': edu_df['numero_uai'],
                'name': edu_df['appellation_officielle'],
                'type': edu_df['nature_uai_libe'],
                'category': 'education',
                'lat': edu_df['lat'],
                'lon': edu_df['lon'],
                'codgeo': edu_df['code_commune'],
                'metadata': edu_df[['secteur_public_prive_libe', 'code_commune', 'adresse_uai', 'ecole_maternelle', 'ecole_elementaire']].rename(columns={
                    'secteur_public_prive_libe': 'statut',
                    'adresse_uai': 'adresse'
                }).to_dict(orient='records')
            })
            pois_list.append(edu_pois)
            
        # Health (FINESS)
        finess_cfg = config['sources']['finess_national']
        finess_path = CACHE_DIR / finess_cfg['local_name']
        if finess_path.exists():
            finess_df = load_dataset(finess_path, finess_cfg)
            finess_df = finess_df.dropna(subset=['coordxet', 'coordyet'])
            
            gdf_finess = gpd.GeoDataFrame(
                finess_df,
                geometry=gpd.points_from_xy(finess_df.coordxet, finess_df.coordyet),
                crs="EPSG:2154"
            ).to_crs("EPSG:4326")
            
            # Merge Maternites
            mat_cfg = config['sources']['maternites']
            mat_path = CACHE_DIR / mat_cfg['local_name']
            if mat_path.exists():
                 # JSON format
                 mat_df = pd.read_json(mat_path)
                 # Expecting 'FI_ET' column
                 if 'FI_ET' in mat_df.columns:
                     mat_ids = set(mat_df['FI_ET'].astype(str))
                     gdf_finess['is_maternite'] = gdf_finess['nofinesset'].astype(str).isin(mat_ids)
                 else:
                     gdf_finess['is_maternite'] = False
            else:
                 gdf_finess['is_maternite'] = False

            # Ensure we have codgeo. If Departement and Commune exist, construct it.
            if 'Departement' in gdf_finess.columns and 'Commune' in gdf_finess.columns:
                 gdf_finess['codgeo'] = gdf_finess['Departement'].astype(str) + gdf_finess['Commune'].astype(str).str.zfill(3)
            elif 'codgeo' not in gdf_finess.columns:
                 # Fallback: try to get it from nofinesset (first 2 digits = dept) + Commune? Unreliable.
                 # Or assume it's already there.
                 gdf_finess['codgeo'] = None

            finess_pois = pd.DataFrame({
                'id': gdf_finess['nofinesset'],
                'name': gdf_finess['RaisonSociale'],
                'type': gdf_finess['LibelleCategorieAgregat'],
                'category': 'sante',
                'lat': gdf_finess.geometry.y,
                'lon': gdf_finess.geometry.x,
                'codgeo': gdf_finess['codgeo'],
                'metadata': gdf_finess[['CategorieAgregat', 'Commune', 'LibelleVoie', 'is_maternite']].to_dict(orient='records')
            })
            pois_list.append(finess_pois)
            
        # Inclusion
        incl_cfg = config['sources']['services_inclusion']
        incl_path = CACHE_DIR / incl_cfg['local_name']
        if incl_path.exists():
            incl_df = load_dataset(incl_path, incl_cfg)
            incl_df = incl_df.dropna(subset=['latitude', 'longitude'])
            
            incl_pois = pd.DataFrame({
                'id': incl_df['id'],
                'name': incl_df['nom'],
                'type': incl_df['thematiques'].apply(lambda x: str(x) if x is not None else 'Autre'),
                'category': 'incl_services', # Renamed from 'inclusion'
                'lat': incl_df['latitude'],
                'lon': incl_df['longitude'],
                'codgeo': incl_df['code_insee'],
                'metadata': incl_df[['description', 'adresse', 'code_insee']].to_dict(orient='records')
            })
            pois_list.append(incl_pois)

        if pois_list:
            all_pois = pd.concat(pois_list, ignore_index=True)
            all_pois['metadata'] = all_pois['metadata'].apply(json.dumps)
            output_path = OUTPUT_DIR / "pois.parquet"
            all_pois.to_parquet(output_path)
            logger.log_step("generate_pois", "CREATED", {"path": str(output_path)})

    except Exception as e:
        logger.log_step("generate_pois", "ERROR", {"error": str(e)})

def generate_referentiels(config: Dict[str, Any], logger: PipelineLogger):
    """Generates referentiels."""
    logger.log_step("generate_referentiels", "STARTED")
    try:
        refs_list = []
        # FAP (Familles Professionnelles)
        fap_cfg = config['sources']['referentiel_fap']
        fap_path = CACHE_DIR / fap_cfg['local_name']
        if fap_path.exists():
            # Expected cols: 'Code FAP 228', 'Intitulé FAP 228'
            # Note: CSV might have BOM or encoding issues, so we use 'Code FAP 228' substring search
            fap_df = load_dataset(fap_path, fap_cfg)
            fap_df.columns = [c.strip().replace('\ufeff', '') for c in fap_df.columns] # Remove BOM if present
            
            code_col = next((c for c in fap_df.columns if 'Code FAP 228' in c), None)
            label_col = next((c for c in fap_df.columns if 'Intitulé FAP 228' in c), None)
            
            if code_col and label_col:
                fap_ref = pd.DataFrame({
                    'key': 'fap_codes',
                    'code': fap_df[code_col],
                    'label': fap_df[label_col],
                    'metadata': fap_df.drop(columns=[code_col, label_col]).to_json(orient='records')
                })
                refs_list.append(fap_ref)
            
        if refs_list:
            all_refs = pd.concat(refs_list, ignore_index=True)
            output_path = OUTPUT_DIR / "referentiels.parquet"
            all_refs.to_parquet(output_path)
            logger.log_step("generate_referentiels", "CREATED", {"path": str(output_path)})

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR", {"error": str(e)})

def main():
    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)
    
    communes_gdf = build_communes(config, logger)
    build_bassins_de_vie(communes_gdf, config, logger)
    build_vertical_tables(config, logger)
    generate_pois(config, logger)
    generate_referentiels(config, logger)
    
    logger.log_step("build_all", "COMPLETED")

if __name__ == "__main__":
    main()
