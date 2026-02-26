import argparse
import logging
import pandas as pd
import geopandas as gpd
import json
import logging
import shutil
import numpy as np
from pathlib import Path
import warnings
from shapely.geometry import Polygon, MultiPolygon
from typing import Dict, Any, List

def extract_polygonal(geom):
    """Keep only Polygon/MultiPolygon parts of a geometry."""
    if geom is None:
        return None
    if geom.geom_type in ["Polygon", "MultiPolygon"]:
        return geom
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ["Polygon", "MultiPolygon"]]
        if not polys:
            return None
        if len(polys) == 1:
            return polys[0]
        return MultiPolygon(polys)
    return None

from pipeline.common import (
    PipelineLogger, load_config, load_dataset,
    PipelineLogger, load_config, load_dataset,
    CONFIG_FILE, CACHE_DIR, CLEAN_DIR, OUTPUT_DIR, STATUS_FILE
)
import app.config as cfg

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
            logger.log_step("build_communes", "FAILED", {"reason": "Clean Communes file not found"})
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
                    
                if name == "population_details":
                     pass
                     
                communes_gdf = communes_gdf.merge(df, on='codgeo', how='left')
            else:
                logging.warning(f"Clean {name} file not found.")
                # Optional: Detailed log if impactful?
                # logger.log_step("build_communes_merge", "WARNING", {"missing": name})

        # Merge BMO (Stats only + code_be) - DEPRECATED
        # merge_clean("bmo_stats", ['metiers_offres_diff', 'metiers_tension_diff', 'code_be'])

        
        # Merge Population
        merge_clean("population", ['population'])
        
        # Merge Population Active
        merge_clean("population_active", ['pop_active', 'pop_employes', 'pop_chomeurs'])

        # Merge Population Details (Age Breakdown)
        merge_clean("population_details", ['pop_jeune_2016', 'pop_jeune_2022', 'pop_active_2016', 'pop_active_2022'])
        
        # Merge LOVAC
        merge_clean("lovac", ['pp_vacant_plus_2ans_25', 'log_priv_total_24'])
        
        # Merge RPLS
        merge_clean("rpls", ['log_soc_total', 'log_soc_inoccupes'])
        
        # Merge CAF
        merge_clean("caf", ['taux_couverture'])
        
        # Merge Education
        merge_clean("education", ['edu_maternelle_ct', 'edu_elementaire_ct', 'edu_college_ct', 'edu_lycee_ct'])
        
        # Merge Political
        merge_clean("political", ['pol_num'])
        
        # Merge Housing Occupation
        merge_clean("housing_occupation", ['MOD_OVER_OCC', 'MOD_UNDER_OCC', 'SEV_OVER_OCC', 'SEV_UNDER_OCC', 'STD_OCC', 'VSEV_UNDER_OCC'])
        
        # Merge School Effectifs
        merge_clean("school_effectifs", ['total_eleves', 'ecoles_count', 'risky_schools_count'])
        
        # Merge BPE Petite Enfance (Creches)
        merge_clean("bpe_petite_enfance_cols", ['bpe_creches_count'])

        # Merge Gares (Odace API)
        merge_clean("gares", ['gare_count', 'has_gare'])

        # Merge mobility metrics
        merge_clean("mob_transports_pub", ['nb_stops_bus', 'nb_stops_tram', 'nb_stops_metro', 'nb_stops_train', 'nb_stops_total'])

        # Merge RNA RAG Inclusion Stats (New)
        # This brings in inc_rna_{category}_count columns
        merge_clean("rna_inclusion_agg")

        # Merge SIAE Structures Count (New F-39)
        siae_path = CLEAN_DIR.parent / "output" / "odis_inclusion_structures.parquet"
        if siae_path.exists():
            siae_df = pd.read_parquet(siae_path)
            siae_agg = siae_df.groupby('codgeo').size().rename('inc_siae_count').reset_index()
            communes_gdf = communes_gdf.merge(siae_agg, on='codgeo', how='left')
            communes_gdf['inc_siae_count'] = communes_gdf['inc_siae_count'].fillna(0)
            logging.info(f"SIAE structures counts merged from {siae_path}.")
        else:
            logging.warning(f"SIAE structures file not found at {siae_path}.")
            communes_gdf['inc_siae_count'] = 0
        
        # Calculate lien_social_count from RAG categories
        # 'lien_social_count' is used for inc_asso_core_scaled (Lien Social Density)
        # Any association with is_inclusion_relevant=True in BQ contributes here.
        rna_cols = [c for c in communes_gdf.columns if c.startswith("inc_rna_") and c.endswith("_count")]
        if rna_cols:
            communes_gdf['lien_social_count'] = communes_gdf[rna_cols].sum(axis=1)
            logging.info(f"RNA RAG: Calculated lien_social_count from {len(rna_cols)} categories.")
        else:
            communes_gdf['lien_social_count'] = 0

        # Merge Odace Commune SK
        merge_clean("odace_communes_sk", ['commune_sk'])

        # Merge Odace Rent Data
        # We pivot the ODACE rent data by housing type and join it using commune_sk
        try:
            rent_path = CLEAN_DIR / "odace_loyer_annonce.parquet"
            profil_path = CLEAN_DIR / "odace_logement_profil.parquet"
            if rent_path.exists() and profil_path.exists():
                df_rent = pd.read_parquet(rent_path)
                df_profil = pd.read_parquet(profil_path)
                
                # Merge profile info to get human labels
                df_merged = df_rent.merge(df_profil, on='logement_profil_sk', how='inner')
                
                # Create a standardized column name for each profile
                def get_col_name(row):
                    type_bien = str(row['logement_type']).lower()
                    typologie = str(row['typologie']).lower()
                    
                    # Target: appt_all, appt_t1_t2, appt_t3_p, house_all
                    tb = 'appt' if 'appartement' in type_bien else 'house'
                    
                    if 'toutes' in typologie:
                        suffix = 'all'
                    elif 't1' in typologie:
                        suffix = 't1_t2'
                    elif 't3' in typologie:
                        suffix = 't3_p'
                    else:
                        suffix = 'unknown'
                    
                    return f"loyer_m2_moy_{tb}_{suffix}"

                df_merged['odace_col'] = df_merged.apply(get_col_name, axis=1)
                
                # Pivot: 1 row per commune_sk, columns are the 4 housing types
                df_pivot = df_merged.pivot_table(
                    index='commune_sk', 
                    columns='odace_col', 
                    values='loyer_m2_moy',
                    aggfunc='mean' # Should be unique per sk/col anyway
                ).reset_index()
                
                # Merge into main GDF on commune_sk
                if 'commune_sk' in communes_gdf.columns:
                    communes_gdf = communes_gdf.merge(df_pivot, on='commune_sk', how='left')
                    logging.info(f"Odace Rent: Merged pivoted data. Columns added: {list(df_pivot.columns[1:])}")
                    logging.info(f"DEBUG: communes_gdf cols after merge: {[c for c in communes_gdf.columns if 'loyer' in c]}")
            else:
                logging.warning("Odace Rent clean files missing.")
        except Exception as e:
            logging.error(f"Failed to merge Odace Rent: {e}")

        # Merge Loyers (Appartements - Legacy source)
        merge_clean("loyers", ['loyer_app_m2'])

        # Associations merge (Deprecated - Now handled via RNA RAG above)

        # Merge Refugee Associations Count
        refug_path = CLEAN_DIR / "refugee_associations.parquet"
        if refug_path.exists():
            refug_df = pd.read_parquet(refug_path)
            refug_agg = refug_df.groupby('codgeo').size().rename('inc_asso_refug_count').reset_index()
            communes_gdf = communes_gdf.merge(refug_agg, on='codgeo', how='left')
            communes_gdf['inc_asso_refug_count'] = communes_gdf['inc_asso_refug_count'].fillna(0)
            logging.info(f"Refugee associations counts merged.")

        # --- Calculate Health Counts (On-the-fly) ---
        # Since we don't have a clean health file with counts, we calculate them here from raw/cache.
        try:
            finess_cfg = config['sources']['finess_national']
            finess_path = CACHE_DIR / finess_cfg['local_name']
            if finess_path.exists():
                finess_df = load_dataset(finess_path, finess_cfg)
                
                # Construct codgeo
                if 'Departement' in finess_df.columns and 'Commune' in finess_df.columns:
                     finess_df['codgeo'] = finess_df['Departement'].astype(str) + finess_df['Commune'].astype(str).str.zfill(3)
                
                # Filter Categories
                # Hospitals
                hopital_mask = finess_df['LibelleCategorieAgregat'].isin([
                    'Centres Hospitaliers', 
                    'Centres Hospitaliers Régionaux', 
                    'Hôpitaux Locaux'
                ])
                # Psy
                psy_mask = finess_df['LibelleCategorieAgregat'].isin([
                    'Centres Hospitaliers Spécialisés Lutte Maladies Mentales', 
                    'Autres Etablissements de Lutte contre les Maladies Mentales'
                ])
                
                # Maternites (Merge with DREES)
                mat_cfg = config['sources']['maternites']
                mat_path = CACHE_DIR / mat_cfg['local_name']
                is_maternite_mask = pd.Series(False, index=finess_df.index)
                
                if mat_path.exists():
                     mat_df = pd.read_json(mat_path)
                     mat_col = 'FI_ET' if 'FI_ET' in mat_df.columns else 'fi_et'
                     if mat_col in mat_df.columns:
                         mat_ids = set(mat_df[mat_col].astype(str))
                         is_maternite_mask = finess_df['nofinesset'].astype(str).isin(mat_ids)

                # Aggregate
                health_counts = finess_df.groupby('codgeo').agg(
                    count_hopital=('nofinesset', lambda x: x[hopital_mask.loc[x.index]].count()),
                    count_psy=('nofinesset', lambda x: x[psy_mask.loc[x.index]].count()),
                    count_maternite=('nofinesset', lambda x: x[is_maternite_mask.loc[x.index]].count())
                ).reset_index()
                
                communes_gdf = communes_gdf.merge(health_counts, on='codgeo', how='left')
                
                # Fill NaNs for health counts
                for col in ['count_hopital', 'count_psy', 'count_maternite']:
                    communes_gdf[col] = communes_gdf[col].fillna(0)
                
                logging.info(f"Health counts calculated.")
                    
        except Exception as e:
            logging.error(f"Failed to calculate health counts: {e}")
            import traceback
            traceback.print_exc()

        # 3. Renames and Calculations
        
        # Renames
        rename_map = {
            'taux_couverture': 'edu_pe_tx_couverture',
            'pp_vacant_plus_2ans_25': 'log_priv_vacant_plus_2ans',
            'log_priv_total_24': 'log_priv_total',
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
            'metiers_offres_diff', 'log_priv_vacant_plus_2ans', 'log_priv_total', 'edu_pe_tx_couverture',
            'bpe_creches_count', 'lien_social_count',
            'pop_jeune_2016', 'pop_jeune_2022', 'pop_active_2016', 'pop_active_2022',
            'nb_stops_bus', 'nb_stops_tram', 'nb_stops_metro', 'nb_stops_train', 'nb_stops_total',
            'inc_siae_count'
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
        # Moved to prescoring.py

        
        # Rounding
        for col in ['pop_active', 'pop_employes', 'pop_chomeurs']:
            if col in communes_gdf.columns:
                communes_gdf[col] = communes_gdf[col].round(0).astype(int)
        
        # Centroids & Geometry
        # CRITICAL: We project the STORAGE to EPSG:2154 (Lambert-93) for performance and consistency.
        # This allows scoring.py to run without constantly re-projecting.
        
        if communes_gdf.crs != cfg.PROJECTED_CRS:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*array with ndim > 0 to a scalar is deprecated.*")
                communes_gdf = communes_gdf.to_crs(cfg.PROJECTED_CRS)
            
        # Store centroids in projected CRS (for fast distance calc)
        communes_gdf['geometry'] = communes_gdf.geometry.make_valid()
        communes_gdf['geometry'] = communes_gdf.geometry.apply(extract_polygonal)
        communes_gdf = communes_gdf[communes_gdf.geometry.notnull()].copy()
        communes_gdf['centroid'] = communes_gdf.geometry.centroid
                      
        # 4. Bassins de Vie Mapping (for pop_be)
        # We need to load BV mapping. It's in the raw zip usually, but we can extract it or maybe we should have cleaned it?
        # Let's load it from cache as in etl.py
        bv_cfg = config['sources']['bassins_de_vie']
        bv_path = CACHE_DIR / bv_cfg['archive_file']
        
        if bv_path.exists():
            bv_df = load_dataset(bv_path, bv_cfg)
            bv_df = bv_df.rename(columns={
                'Code géographique': 'CODGEO', 
                'Bassin de vie 2022': 'bassin_de_vie',
                'Libellé géographique du bassin de vie 2022': 'libelle_bassin_de_vie'
            })
            if 'CODGEO' in bv_df.columns and 'bassin_de_vie' in bv_df.columns:
                bv_df['CODGEO'] = bv_df['CODGEO'].astype(str).str.zfill(5)
                bv_mapping = bv_df[['CODGEO', 'bassin_de_vie', 'libelle_bassin_de_vie']].set_index('CODGEO')
                communes_gdf = communes_gdf.join(bv_mapping, on='codgeo', how='left')

                # FIX: Handle PLM Arrondissements (Paris, Lyon, Marseille)
                # Arrondissements often don't have a BV code in the official file, but belong to the city BV.
                # Paris: 75101-75120 -> 75056
                # Lyon: 69381-69389 -> 69123
                # Marseille: 13201-13216 -> 13055
                
                # We can use the global dictionary to lookup the BV for the main city code
                paris_bv = bv_mapping.loc['75056', 'bassin_de_vie'] if '75056' in bv_mapping.index else '75056'
                lyon_bv = bv_mapping.loc['69123', 'bassin_de_vie'] if '69123' in bv_mapping.index else '69123'
                mars_bv = bv_mapping.loc['13055', 'bassin_de_vie'] if '13055' in bv_mapping.index else '13055'
                                
                paris_bv_label = bv_mapping.loc['75056', 'libelle_bassin_de_vie'] if '75056' in bv_mapping.index else 'Paris'
                lyon_bv_label = bv_mapping.loc['69123', 'libelle_bassin_de_vie'] if '69123' in bv_mapping.index else 'Lyon'
                mars_bv_label = bv_mapping.loc['13055', 'libelle_bassin_de_vie'] if '13055' in bv_mapping.index else 'Marseille'

                # Paris Arrondissements
                paris_mask = communes_gdf['codgeo'].between('75101', '75120')
                
                communes_gdf.loc[paris_mask & communes_gdf['bassin_de_vie'].isna(), 'bassin_de_vie'] = paris_bv
                communes_gdf.loc[paris_mask & communes_gdf['libelle_bassin_de_vie'].isna(), 'libelle_bassin_de_vie'] = paris_bv_label
                
                # Check patch result
                patched_paris = communes_gdf.loc[paris_mask, 'bassin_de_vie']
                

                # Lyon Arrondissements
                lyon_mask = communes_gdf['codgeo'].between('69381', '69389')
                communes_gdf.loc[lyon_mask & communes_gdf['bassin_de_vie'].isna(), 'bassin_de_vie'] = lyon_bv
                communes_gdf.loc[lyon_mask & communes_gdf['libelle_bassin_de_vie'].isna(), 'libelle_bassin_de_vie'] = lyon_bv_label

                # Marseille Arrondissements
                mars_mask = communes_gdf['codgeo'].between('13201', '13216')
                communes_gdf.loc[mars_mask & communes_gdf['bassin_de_vie'].isna(), 'bassin_de_vie'] = mars_bv
                communes_gdf.loc[mars_mask & communes_gdf['libelle_bassin_de_vie'].isna(), 'libelle_bassin_de_vie'] = mars_bv_label
                
                # Calculate pop_active_be for Ratio
                # We need pop_active per commune first. It is already in communes_gdf.
                # Group by bassin_emploi (which is code_be from BMO, but we just joined BV mapping which is bassin_de_vie)
                # Wait, the user asked for "population active du bassin d'emploi".
                # 'bassin_emploi' comes from BMO stats merge.
                
                # Ratios moved to prescoring.py

        # --- Pre-calculate Ratios and Scaled Scores (Optimization) ---
        # Moved to prescoring.py

        # --- Drop Unused Columns ---
        # --- Drop Unused Columns ---
        # Moved to prescoring.py

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
        # LOGGING CRS STATE
        # logger.log_step("build_communes", "DEBUG", {"crs": str(communes_gdf.crs)})
        
        # Explicitly convert to WKB to ensure we save the PROJECTED geometry (EPSG:2154)
        # and avoid any implicit conversion to EPSG:4326 by GeoParquet logic
        if communes_gdf.crs != cfg.PROJECTED_CRS:
             logger.log_step("build_communes", "WARNING", {"msg": "CRS mismatch before save, re-projecting"})
             with warnings.catch_warnings():
                 warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*array with ndim > 0 to a scalar is deprecated.*")
                 communes_gdf = communes_gdf.to_crs(cfg.PROJECTED_CRS)
             
        communes_gdf['polygon'] = communes_gdf.geometry.to_wkb()
        
        # Drop the geometry column and conversion artifacts to avoid GeoParquet metadata overriding
        # Also drop 'centroid' (shapely objects) which fails to serialize. app/data_loader.py will re-calc it.
        # FIX: Drop names (libgeo, libelle_bassin_de_vie) as they are now in referentiels
        cols_to_drop = ['geometry', 'centroid', 'libgeo', 'libelle_bassin_de_vie']
        # Handle case where columns might not exist (e.g. if already dropped or renamed)
        cols_to_drop = [c for c in cols_to_drop if c in communes_gdf.columns]
        df_to_save = communes_gdf.drop(columns=cols_to_drop).copy()
        
        output_path = OUTPUT_DIR / "odis_communes_pre.parquet"
        logging.info(f"DEBUG: Saving to {output_path}. Columns: {[c for c in df_to_save.columns if 'loyer' in c]}")
        df_to_save.to_parquet(output_path, compression='brotli', index=False)
        logger.log_step("build_communes", "CREATED", {"path": str(output_path), "rows": len(df_to_save)})
        
        # Copy bmo_vertical to output -> Handled in build_vertical_tables as odis_metiers_agg.parquet
        # bmo_vertical_path = CLEAN_DIR / "bmo_vertical.parquet"
        # if bmo_vertical_path.exists():
        #    shutil.copy2(bmo_vertical_path, OUTPUT_DIR / "bmo_vertical.parquet")
        #    logger.log_step("build_communes", "COPIED", {"file": "bmo_vertical.parquet"})
        
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
            # Only set geometry to 'polygon' if it's not already the active geometry
            # AND if it seems to contain geometry objects (not bytes)
            if communes_gdf.geometry.name != 'polygon':
                if not isinstance(communes_gdf['polygon'].iloc[0], bytes):
                     communes_gdf['geometry'] = communes_gdf['polygon'].apply(lambda x: make_valid(wkb.loads(x)))
        communes_gdf = communes_gdf.set_geometry('geometry')
                # If bytes, we assume active geometry is already correct (from build_communes)
                # or we would need to load it. Since build_communes returns valid GDF, we do nothing.
        # communes_gdf['geometry'] = communes_gdf.geometry.buffer(0)
        # communes_gdf['geometry'] = communes_gdf.geometry.make_valid()
        
        from shapely.validation import make_valid
        communes_gdf['geometry'] = communes_gdf.geometry.apply(make_valid)
        
        numeric_cols = [
            'population', 'log_soc_total', 'log_soc_inoccupes', 
            'edu_maternelle_ct', 'edu_elementaire_ct', 'ecoles_count',
            'lien_social_count', 'svc_incl_count', 
            'pop_active', 'pop_employes', 'pop_chomeurs', 
            'log_priv_vacant_plus_2ans',
            'metiers_offres_diff', 'bpe_creches_count',
            'inc_siae_count'
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
        # 4. Dissolve by Bassin de Vie
        # Fix invalid geometries before dissolve
        # 1. Try buffer(0)
        # 1. Clean geometries for dissolve
        communes_gdf['geometry'] = communes_gdf.geometry.make_valid()
        communes_gdf['geometry'] = communes_gdf.geometry.apply(extract_polygonal)
        communes_gdf = communes_gdf[communes_gdf.geometry.notnull()].copy()
        
        bv_gdf = communes_gdf[communes_gdf['bassin_de_vie'].notnull()].dissolve(by='bassin_de_vie', aggfunc=agg_dict)
        
        # FIX: Remove holes from the dissolved polygons
        # Some communes might be "enclaves" or topological errors might create holes.
        # We want the BV to be a solid shape covering everything.
        from shapely.geometry import Polygon, MultiPolygon
        
        def remove_holes(geom):
            if isinstance(geom, Polygon):
                return Polygon(geom.exterior)
            elif isinstance(geom, MultiPolygon):
                parts = [Polygon(p.exterior) for p in geom.geoms]
                return MultiPolygon(parts)
            return geom
            
        # Use the active geometry column
        bv_gdf[bv_gdf.geometry.name] = bv_gdf.geometry.apply(remove_holes)
        
        bv_gdf.rename(columns={'population': 'population_bv'}, inplace=True)
        
        # Calculate pop_chomage_ratio for BV
        if 'pop_active' in bv_gdf.columns and 'pop_chomeurs' in bv_gdf.columns:
             bv_gdf['pop_chomage_ratio'] = np.where(
                 bv_gdf['pop_active'] > 0,
                 bv_gdf['pop_chomeurs'] / bv_gdf['pop_active'],
                 0.0
             )
        
        # Add Label - REMOVED (Now in Referentiels)
        # bv_cfg = config['sources']['bassins_de_vie']
        # bv_path = CACHE_DIR / bv_cfg['archive_file']
        # if bv_path.exists():
             # Logic removed to avoid adding 'libgeo' back
        #    pass
        
        # Explicitly convert to WKB to ensure we save the PROJECTED geometry (EPSG:2154)
        if bv_gdf.crs != cfg.PROJECTED_CRS:
             with warnings.catch_warnings():
                 warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*array with ndim > 0 to a scalar is deprecated.*")
                 bv_gdf = bv_gdf.to_crs(cfg.PROJECTED_CRS)
             
        bv_gdf['polygon'] = bv_gdf.geometry.to_wkb()
        
        # Drop geometry to avoid GeoParquet 4326 default
        # Also drop name columns if present (libgeo)
        cols_to_drop = ['geometry', 'libgeo', 'libelle_bassin_de_vie']
        cols_to_drop = [c for c in cols_to_drop if c in bv_gdf.columns]
        
        df_to_save = pd.DataFrame(bv_gdf.drop(columns=cols_to_drop))
        
        output_path = OUTPUT_DIR / "odis_bassins_de_vie.parquet"
        df_to_save.reset_index().to_parquet(output_path, compression='brotli', index=False)
        logger.log_step("build_bassins_de_vie", "CREATED", {"path": str(output_path), "rows": len(df_to_save)})

    except Exception as e:
        logger.log_step("build_bassins_de_vie", "ERROR", {"error": str(e)})
        logging.error(f"Build BV failed: {e}")

def build_vertical_tables(config: Dict[str, Any], logger: PipelineLogger):
    """Generates vertical lookup tables."""
    logger.log_step("build_vertical_tables", "STARTED")
    try:
        # 1. Metiers - DEPRECATED (Moved to Live Jobs)
        # bmo_path = CLEAN_DIR / "bmo_vertical.parquet"
        # if bmo_path.exists():
        #     df = pd.read_parquet(bmo_path)
        #     out = OUTPUT_DIR / "odis_metiers_agg.parquet"
        #     df.to_parquet(out)
        #     logger.log_step("build_vertical_tables", "METIERS", {"path": str(out)})

            
        # 2. Associations
        assoc_path = CLEAN_DIR / "associations_vertical.parquet"
        if assoc_path.exists():
            df = pd.read_parquet(assoc_path)
            out = OUTPUT_DIR / "odis_associations_agg.parquet"
            df.to_parquet(out, compression='brotli', index=False)
            logger.log_step("build_vertical_tables", "ASSOCIATIONS", {"path": str(out)})
            
            # Copy raw vertical file to output as well if requested -> Replaced by odis_associations_agg
            # shutil.copy2(assoc_path, OUTPUT_DIR / "associations_vertical.parquet")
            
        # 3. Structures Inclusion (CCAS/CIAS)
        struct_path = CLEAN_DIR / "structures_inclusion.parquet"
        if struct_path.exists():
             out = OUTPUT_DIR / "odis_ccas.parquet"
             shutil.copy2(struct_path, out)
             logger.log_step("build_vertical_tables", "STRUCTURES", {"path": str(out)})
            
        # 4. Formations
        form_path = CLEAN_DIR / "formations_annuaire.parquet"
        if form_path.exists():
            df = pd.read_parquet(form_path)
            # Aggregate count by codgeo and formation_code
            # The file has 'codgeo', 'formation_code' (one row per entity)
            # We want count of entities per formation type per commune?
            # Or just list of available formations?
            # User said: "aggregations count of avaliable formation codes by codgeo"
            # So we group by codgeo, formation_code and count.
            
            df_agg = df.groupby(['codgeo', 'formation_code']).size().rename('count').reset_index()
            
            out = OUTPUT_DIR / "odis_formations_agg.parquet"
            df_agg.to_parquet(out, compression='brotli', index=False)
            logger.log_step("build_vertical_tables", "FORMATIONS", {"path": str(out)})

        # 5. Refugee Associations (Detailed List)
        refug_path = CLEAN_DIR / "refugee_associations.parquet"
        if refug_path.exists():
             out = OUTPUT_DIR / "odis_refugee_associations.parquet"
             shutil.copy2(refug_path, out)
             logger.log_step("build_vertical_tables", "REFUGEE_ASSOCIATIONS", {"path": str(out)})
            
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
            # secteur_public_prive_libe -> metadata (unused)
            
            if 'latitude' in edu_df.columns and 'longitude' in edu_df.columns:
                 edu_df['lat'] = edu_df['latitude']
                 edu_df['lon'] = edu_df['longitude']
            
            # Filter allowed types
            allowed_types = [
                'ECOLE MATERNELLE',
                'ECOLE DE NIVEAU ELEMENTAIRE',
                'COLLEGE',
                'LYCEE PROFESSIONNEL', 
                'LYCEE ENSEIGNT GENERAL ET TECHNOLOGIQUE', 
                'LYCEE D ENSEIGNEMENT GENERAL', 
                'LYCEE D ENSEIGNEMENT TECHNOLOGIQUE'
            ]
            
            if 'nature_uai_libe' in edu_df.columns:
                edu_df = edu_df[edu_df['nature_uai_libe'].isin(allowed_types)]

            # Calculate flags (logic from data_loader.py)
            edu_df['ecole_maternelle'] = edu_df['nature_uai_libe'].str.contains('maternelle', case=False, na=False) | \
                                          edu_df['nature_uai_libe'].str.contains('primaire', case=False, na=False)
            edu_df['ecole_elementaire'] = edu_df['nature_uai_libe'].str.contains('élémentaire', case=False, na=False) | \
                                           edu_df['nature_uai_libe'].str.contains('primaire', case=False, na=False)

            # Standardize Types
            edu_pois_list = []
            
            # 1. Maternelles
            mat_mask = edu_df['ecole_maternelle']
            if mat_mask.any():
                mat_df_pois = edu_df[mat_mask].copy()
                edu_pois_list.append(pd.DataFrame({
                    'id': mat_df_pois['numero_uai'].astype(str) + "_mat",
                    'name': mat_df_pois['appellation_officielle'],
                    'type': 'Maternelle',
                    'category': 'education',
                    'lat': mat_df_pois['lat'],
                    'lon': mat_df_pois['lon'],
                    'codgeo': mat_df_pois['code_commune']
                }))
            
            # 2. Elementaires
            elem_mask = edu_df['ecole_elementaire']
            if elem_mask.any():
                elem_df_pois = edu_df[elem_mask].copy()
                edu_pois_list.append(pd.DataFrame({
                    'id': elem_df_pois['numero_uai'].astype(str) + "_elem",
                    'name': elem_df_pois['appellation_officielle'],
                    'type': 'Elémentaire',
                    'category': 'education',
                    'lat': elem_df_pois['lat'],
                    'lon': elem_df_pois['lon'],
                    'codgeo': elem_df_pois['code_commune']
                }))
            
            # 3. Colleges & Lycees (remaining types)
            other_types = ['COLLEGE', 'LYCEE PROFESSIONNEL', 'LYCEE ENSEIGNT GENERAL ET TECHNOLOGIQUE', 'LYCEE D ENSEIGNEMENT GENERAL', 'LYCEE D ENSEIGNEMENT TECHNOLOGIQUE']
            other_mask = edu_df['nature_uai_libe'].isin(other_types)
            if other_mask.any():
                other_df = edu_df[other_mask].copy()
                def map_other_type(t):
                    if t == 'COLLEGE': return 'Collège'
                    return 'Lycée'
                
                edu_pois_list.append(pd.DataFrame({
                    'id': other_df['numero_uai'],
                    'name': other_df['appellation_officielle'],
                    'type': other_df['nature_uai_libe'].apply(map_other_type),
                    'category': 'education',
                    'lat': other_df['lat'],
                    'lon': other_df['lon'],
                    'codgeo': other_df['code_commune']
                }))

            if edu_pois_list:
                pois_list.append(pd.concat(edu_pois_list, ignore_index=True))
            
        # Health (FINESS)
        finess_cfg = config['sources']['finess_national']
        finess_path = CACHE_DIR / finess_cfg['local_name']
        if finess_path.exists():
            finess_df = load_dataset(finess_path, finess_cfg)
            finess_df = finess_df.dropna(subset=['coordxet', 'coordyet'])
            
            # Filter Public only
            if 'LibelleSph' in finess_df.columns:
                 finess_df = finess_df[finess_df['LibelleSph'] == 'Etablissement public de santé']
            
            gdf_finess = gpd.GeoDataFrame(
                finess_df,
                geometry=gpd.points_from_xy(finess_df.coordxet, finess_df.coordyet),
                crs=cfg.PROJECTED_CRS
            )
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*array with ndim > 0 to a scalar is deprecated.*")
                gdf_finess = gdf_finess.to_crs("EPSG:4326")
            
            # Merge Maternites
            mat_cfg = config['sources']['maternites']
            mat_path = CACHE_DIR / mat_cfg['local_name']
            if mat_path.exists():
                 # JSON format
                 mat_df = pd.read_json(mat_path)
                 # Expecting 'FI_ET' or 'fi_et' column
                 mat_col = 'FI_ET' if 'FI_ET' in mat_df.columns else 'fi_et'
                 
                 if mat_col in mat_df.columns:
                     mat_ids = set(mat_df[mat_col].astype(str))
                     gdf_finess['is_maternite'] = gdf_finess['nofinesset'].astype(str).isin(mat_ids)
                 else:
                     gdf_finess['is_maternite'] = False
            else:
                 gdf_finess['is_maternite'] = False

            # Define categories
            # Clean strings first
            if 'LibelleCategorieAgregat' in gdf_finess.columns:
                gdf_finess['LibelleCategorieAgregat'] = gdf_finess['LibelleCategorieAgregat'].astype(str).str.strip()
            
            gdf_finess['is_hopital'] = gdf_finess['LibelleCategorieAgregat'].isin([
                'Centres Hospitaliers', 
                'Centres Hospitaliers Régionaux', 
                'Hôpitaux Locaux'
            ])
            
            gdf_finess['is_psy'] = gdf_finess['LibelleCategorieAgregat'].isin([
                'Centres Hospitaliers Spécialisés Lutte Maladies Mentales', 
                'Autres Etablissements de Lutte contre les Maladies Mentales'
            ])
            
            # Ensure we have codgeo
            if 'Departement' in gdf_finess.columns and 'Commune' in gdf_finess.columns:
                 gdf_finess['codgeo'] = gdf_finess['Departement'].astype(str) + gdf_finess['Commune'].astype(str).str.zfill(3)
            elif 'codgeo' not in gdf_finess.columns:
                  gdf_finess['codgeo'] = None

            # 1. Sante POIs (Hopital OR Psy)
            sante_mask = gdf_finess['is_hopital'] | gdf_finess['is_psy']
            sante_df = gdf_finess[sante_mask].copy()

            # Standardize Types
            def map_sante_type(row):
                if row['is_hopital']: return 'Hopital'
                if row['is_maternite']: return 'Maternité'
                if row['is_psy']: return 'Soutien Psychologique & Addictologie'
                return row['LibelleCategorieAgregat']

            sante_df['standard_type'] = sante_df.apply(map_sante_type, axis=1)

            finess_pois = pd.DataFrame({
                'id': sante_df['nofinesset'],
                'name': sante_df['RaisonSociale'],
                'type': sante_df['standard_type'],
                'category': 'sante',
                'lat': sante_df.geometry.y,
                'lon': sante_df.geometry.x,
                'codgeo': sante_df['codgeo']
            })
            pois_list.append(finess_pois)
            
            # 2. Maternites POIs
            mat_mask = gdf_finess['is_maternite']
            mat_df_pois = gdf_finess[mat_mask].copy()
            
            maternite_pois = pd.DataFrame({
                'id': mat_df_pois['nofinesset'].astype(str) + "_mat",
                'name': mat_df_pois['RaisonSociale'],
                'type': 'Maternité',
                'category': 'sante',
                'lat': mat_df_pois.geometry.y,
                'lon': mat_df_pois.geometry.x,
                'codgeo': mat_df_pois['codgeo']
            })
            pois_list.append(maternite_pois)
            
        # Inclusion Services (Cleaned in Ingest)
        incl_clean_path = CLEAN_DIR / "services_inclusion.parquet"
        if incl_clean_path.exists():
            incl_df = pd.read_parquet(incl_clean_path)
            logging.info(f"Inclusion Clean File Found: {len(incl_df)} rows")
            
            # Create unique ID from id_structure and service_slug
            import hashlib
            def generate_hash_id(row):
                composite_key = f"{row['id_structure']}_{row['service_slug']}"
                return hashlib.md5(composite_key.encode()).hexdigest()

            incl_pois = pd.DataFrame({
                'id': incl_df.apply(generate_hash_id, axis=1),
                'name': incl_df['nom'],
                'type': incl_df['service_slug'].astype(str),
                'category': 'incl_services',
                'lat': incl_df['latitude'],
                'lon': incl_df['longitude'],
                'codgeo': incl_df['codgeo']
            })
            
            pois_list.append(incl_pois)
        else:
             logging.warning("Clean services_inclusion.parquet not found. Run ingest.")

        # BPE - Petite Enfance POIs
        bpe_pois_path = CLEAN_DIR / "bpe_petite_enfance_pois.parquet"
        if bpe_pois_path.exists():
            bpe_pois_df = pd.read_parquet(bpe_pois_path)
            # Schema should already match from ingest step
            pois_list.append(bpe_pois_df)

        if pois_list:
            all_pois = pd.concat(pois_list, ignore_index=True)
            
            # Optimize types
            all_pois['category'] = all_pois['category'].astype('category')
            all_pois['type'] = all_pois['type'].astype('category')
            all_pois['lat'] = all_pois['lat'].astype('float32')
            all_pois['lon'] = all_pois['lon'].astype('float32')
            if 'codgeo' in all_pois.columns:
                 all_pois['codgeo'] = all_pois['codgeo'].astype('category')
            
            output_path = OUTPUT_DIR / "odis_pois.parquet"
            all_pois.to_parquet(output_path, compression='brotli', index=False)
            logger.log_step("generate_pois", "CREATED", {"path": str(output_path)})

    except Exception as e:
        logger.log_step("generate_pois", "ERROR", {"error": str(e)})

def generate_referentiels(config: Dict[str, Any], logger: PipelineLogger):
    """Generates referentiels."""
    logger.log_step("generate_referentiels", "STARTED")
    try:
        refs_list = []

            
        if refs_list:
            all_refs = pd.concat(refs_list, ignore_index=True)
            output_path = OUTPUT_DIR / "odis_referentiels.parquet"
            all_refs.to_parquet(output_path)
            logger.log_step("generate_referentiels", "CREATED", {"path": str(output_path)})

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR", {"error": str(e)})

    try:
        # Formations
        form_ref_path = CLEAN_DIR / "formations_referentiel.parquet"
        if form_ref_path.exists():
            form_df = pd.read_parquet(form_ref_path)
            # Expected: code, label
            if 'code' in form_df.columns and 'label' in form_df.columns:
                form_ref = pd.DataFrame({
                    'key': 'formation_codes',
                    'code': form_df['code'],
                    'label': form_df['label']
                    #'metadata': None # Removed
                })
                refs_list.append(form_ref)
                
        if refs_list:
            all_refs = pd.concat(refs_list, ignore_index=True)
            output_path = OUTPUT_DIR / "referentiels.parquet"
            all_refs.to_parquet(output_path)
            logger.log_step("generate_referentiels", "CREATED", {"path": str(output_path)})

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR", {"error": str(e)})

    try:
        # Inclusion Services Referentiel (Local CSV)
        incl_cfg = config['sources'].get('referentiel_services_inclusion')
        if incl_cfg:
            incl_path = CACHE_DIR / incl_cfg['local_name']
            if incl_path.exists():
                # Expected cols: Nom, Label
                incl_df = load_dataset(incl_path, incl_cfg)
                incl_df.columns = [c.strip() for c in incl_df.columns]
                
                if 'Nom' in incl_df.columns and 'Label' in incl_df.columns:
                    incl_ref = pd.DataFrame({
                        'key': 'inclusion_services',
                        'code': incl_df['Nom'],
                        'label': incl_df['Label']
                        #'metadata': None # Removed
                    })
                    refs_list.append(incl_ref)
                    logger.log_step("generate_referentiels", "INCLUSION", {"count": len(incl_ref)})
                else:
                     logging.warning(f"Inclusion Referentiel: Missing columns. Found: {incl_df.columns}")

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR", {"error": str(e)})

    try:
        # WALDEC
        waldec_path = CLEAN_DIR / "referentiel_waldec.parquet"
        if waldec_path.exists():
            waldec_df = pd.read_parquet(waldec_path)
            if 'code' in waldec_df.columns and 'label' in waldec_df.columns:
                 waldec_ref = pd.DataFrame({
                    'key': 'waldec_codes',
                    'code': waldec_df['code'],
                    'label': waldec_df['label']
                })
                 refs_list.append(waldec_ref)
                 logger.log_step("generate_referentiels", "WALDEC", {"count": len(waldec_ref)})

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR", {"error": str(e)})

    try:
        # Communes (from Clean or Raw)
        # We need code (codgeo) and label (libgeo or nom)
        # We can load the clean communes file
        communes_path = CLEAN_DIR / "communes.parquet"
        if communes_path.exists():
            # Clean file uses 'nom' instead of 'libgeo'
            communes_df = pd.read_parquet(communes_path, columns=['codgeo', 'nom'])
            if 'codgeo' in communes_df.columns and 'nom' in communes_df.columns:
                 communes_ref = pd.DataFrame({
                    'key': 'communes',
                    'code': communes_df['codgeo'],
                    'label': communes_df['nom']
                })
                 refs_list.append(communes_ref)
                 logger.log_step("generate_referentiels", "COMMUNES", {"count": len(communes_ref)})

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR_COMMUNES", {"error": str(e)})

    try:
        # Bassins de Vie
        bv_cfg = config['sources']['bassins_de_vie']
        bv_path = CACHE_DIR / bv_cfg['archive_file']
        if bv_path.exists():
            # Load raw to get names
            df_bv_source = load_dataset(bv_path, bv_cfg)
             # 'Bassin de vie 2022', 'Libellé géographique du bassin de vie 2022'
            if 'Bassin de vie 2022' in df_bv_source.columns and 'Libellé géographique du bassin de vie 2022' in df_bv_source.columns:
                 bv_ref = df_bv_source[['Bassin de vie 2022', 'Libellé géographique du bassin de vie 2022']].drop_duplicates()
                 bv_ref.columns = ['code', 'label']
                 
                 bv_ref = pd.DataFrame({
                    'key': 'bassins_de_vie',
                    'code': bv_ref['code'].astype(str),
                    'label': bv_ref['label']
                })
                 refs_list.append(bv_ref)
                 logger.log_step("generate_referentiels", "BASSINS_VIE", {"count": len(bv_ref)})

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR_BASSINS_VIE", {"error": str(e)})

    try:
        # Regions
        regions_path = CLEAN_DIR / "regions.parquet"
        if regions_path.exists():
            regions_df = pd.read_parquet(regions_path)
            regions_ref = pd.DataFrame({
                'key': 'regions',
                'code': regions_df['code'],
                'label': regions_df['label']
            })
            refs_list.append(regions_ref)
            logger.log_step("generate_referentiels", "REGIONS", {"count": len(regions_ref)})

        # Departements
        deps_path = CLEAN_DIR / "departements.parquet"
        if deps_path.exists():
            deps_df = pd.read_parquet(deps_path)
            deps_ref = pd.DataFrame({
                'key': 'departements',
                'code': deps_df['code'],
                'label': deps_df['label'],
                'reg_code': deps_df.get('reg_code', None)
            })
            refs_list.append(deps_ref)
            logger.log_step("generate_referentiels", "DEPARTEMENTS", {"count": len(deps_ref)})



        # ROME Codes (Referential from API)
        rome_path = CACHE_DIR / "rome_referential_api.parquet"
        if rome_path.exists():
            rome_df = pd.read_parquet(rome_path)
            # Expected: code, label
            if 'code' in rome_df.columns and 'label' in rome_df.columns:
                rome_ref = pd.DataFrame({
                    'key': 'rome_codes',
                    'code': rome_df['code'].astype(str),
                    'label': rome_df['label']
                })
                refs_list.append(rome_ref)
                logger.log_step("generate_referentiels", "ROME_CODES", {"count": len(rome_ref)})

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR_REG_DEP_MAPPING", {"error": str(e)})

    # Final concatenation and save for all referentiels
    if refs_list:
        all_refs = pd.concat(refs_list, ignore_index=True)
        output_path = OUTPUT_DIR / "odis_referentiels.parquet"
        all_refs.to_parquet(output_path)
        logger.log_step("generate_referentiels", "CREATED", {"path": str(output_path)})

def main(argv=None):
    parser = argparse.ArgumentParser(description="ODIS Build Pipeline")
    parser.add_argument('--steps', type=str, help="Comma-separated list of steps to run (e.g. communes,pois)")
    args = parser.parse_args(argv)

    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)
    
    steps_map = {
        'communes': build_communes,
        'bassins_de_vie': lambda cfg, log: build_bassins_de_vie(communes_gdf, cfg, log),
        'vertical_tables': build_vertical_tables,
        'pois': generate_pois,
        'referentiels': generate_referentiels
    }

    selected_steps = args.steps.split(',') if args.steps else ['communes', 'bassins_de_vie', 'vertical_tables', 'pois', 'referentiels']
    
    communes_gdf = None
    if 'communes' in selected_steps or 'bassins_de_vie' in selected_steps:
        # We need communes for BV
        communes_gdf = build_communes(config, logger)
    
    for step_name in selected_steps:
        if step_name == 'communes': continue # Already run
        if step_name in steps_map:
            try:
                if step_name == 'bassins_de_vie':
                    build_bassins_de_vie(communes_gdf, config, logger)
                else:
                    steps_map[step_name](config, logger)
            except Exception as e:
                print(f"ERROR running build step {step_name}: {e}")
                import traceback
                traceback.print_exc()
        else:
            logging.warning(f"Unknown build step: {step_name}")

    logger.log_step("build_all", "COMPLETED")

if __name__ == "__main__":
    main()
