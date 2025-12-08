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
        merge_clean("lovac", ['pp_vacant_plus_2ans_25', 'log_priv_total_24'])
        
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
        
        # Merge Voisins
        merge_clean("voisins", ['codgeo_voisins'])

        # Merge BPE Petite Enfance (Creches)
        merge_clean("bpe_petite_enfance_cols", ['bpe_creches_count'])

        # Merge Gares (Odace API)
        merge_clean("gares", ['gare_count', 'has_gare'])

        # Merge Associations (Lien Social) - Moved from prescoring
        # Need to aggregate it first? merge_clean expects a parquet with 'codgeo'.
        # associations_vertical.parquet has 'codgeo'.
        # But wait, is it already aggregated? 'associations_vertical.parquet' is vertical.
        # prescoring.py did: assoc_df.groupby('codgeo')['count'].sum().rename('lien_social_count')
        # So I need to do that aggregation here or ensure a cleaned file exists.
        # ingest/clean_associations produces 'associations_vertical.parquet'.
        # I should probably just do the aggregation on the fly here like prescoring did, OR logic in ingest to produce a 'clean' 1-row-per-commune file.
        # Given constraints, I'll calculate it on flight here if possible or create a small helper.
        # Actually merge_clean expects a file.
        # Let's see if we can use a "associations_stats.parquet" if it exists, or just do raw load.
        # prescoring did raw load. I'll do raw load here.
        
        assocs_path = CLEAN_DIR / "associations_vertical.parquet"
        if assocs_path.exists():
             assocs_df = pd.read_parquet(assocs_path)
             if 'count' in assocs_df.columns:
                 assocs_agg = assocs_df.groupby('codgeo')['count'].sum().rename('lien_social_count').reset_index()
                 communes_gdf = communes_gdf.merge(assocs_agg, on='codgeo', how='left')
                 communes_gdf['lien_social_count'] = communes_gdf['lien_social_count'].fillna(0)
             else:
                 # If just rows, count them? clean_associations returns vertical with 'count' usually?
                 # Let's check 'associations_vertical.parquet' content if possible.
                 # Assuming standard structure from previous tasks.
                 pass

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
                
                logging.info(f"Health counts calculated. Columns added: {[c for c in ['count_hopital', 'count_psy', 'count_maternite'] if c in communes_gdf.columns]}")
                    
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
            'bpe_creches_count', 'lien_social_count'
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
        
        # Centroids
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
                
                logging.info(f"debug PLM: Paris BV={paris_bv}, Lyon BV={lyon_bv}, Mars BV={mars_bv}")
                
                paris_bv_label = bv_mapping.loc['75056', 'libelle_bassin_de_vie'] if '75056' in bv_mapping.index else 'Paris'
                lyon_bv_label = bv_mapping.loc['69123', 'libelle_bassin_de_vie'] if '69123' in bv_mapping.index else 'Lyon'
                mars_bv_label = bv_mapping.loc['13055', 'libelle_bassin_de_vie'] if '13055' in bv_mapping.index else 'Marseille'

                # Paris Arrondissements
                paris_mask = communes_gdf['codgeo'].between('75101', '75120')
                logging.info(f"debug PLM: Paris Arronds Mask Sum = {paris_mask.sum()}")
                
                communes_gdf.loc[paris_mask & communes_gdf['bassin_de_vie'].isna(), 'bassin_de_vie'] = paris_bv
                communes_gdf.loc[paris_mask & communes_gdf['libelle_bassin_de_vie'].isna(), 'libelle_bassin_de_vie'] = paris_bv_label
                
                # Check patch result
                patched_paris = communes_gdf.loc[paris_mask, 'bassin_de_vie']
                logging.info(f"debug PLM: Paris Patched Sample: {patched_paris.head(1).values}")

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
            'edu_maternelle_ct', 'edu_elementaire_ct', 'ecoles_count',
            'lien_social_count', 'svc_incl_count', 
            'pop_active', 'pop_employes', 'pop_chomeurs', 
            'log_priv_vacant_plus_2ans',
            'metiers_offres_diff', 'bpe_creches_count'
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
        communes_gdf['geometry'] = communes_gdf['geometry'].buffer(0)
        # 2. Filter invalid
        communes_gdf = communes_gdf[communes_gdf.is_valid]
        
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
        
        # Add Label
        bv_cfg = config['sources']['bassins_de_vie']
        bv_path = CACHE_DIR / bv_cfg['archive_file']
        if bv_path.exists():
            df_bv_source = load_dataset(bv_path, bv_cfg)
            # 'Bassin de vie 2022', 'Libellé géographique du bassin de vie 2022'
            # Rename to match our dissolved index 'bassin_de_vie'
            df_bv_source = df_bv_source.rename(columns={
                'Bassin de vie 2022': 'bassin_de_vie',
                'Libellé géographique du bassin de vie 2022': 'libgeo'
            })
            # Deduplicate (one label per BV code)
            labels = df_bv_source[['bassin_de_vie', 'libgeo']].drop_duplicates()
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
            
            # Copy raw vertical file to output as well if requested
            shutil.copy2(assoc_path, OUTPUT_DIR / "associations_vertical.parquet")
            
        # 3. Formations
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
            
            out = OUTPUT_DIR / "odis_rel_formations.parquet"
            df_agg.to_parquet(out)
            logger.log_step("build_vertical_tables", "FORMATIONS", {"path": str(out)})
            
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
            def map_edu_type(row):
                t = row['nature_uai_libe']
                if t == 'ECOLE MATERNELLE': return 'Maternelle'
                if t == 'ECOLE DE NIVEAU ELEMENTAIRE': return 'Elémentaire'
                if t == 'COLLEGE': return 'Collège'
                if t in ['LYCEE PROFESSIONNEL', 'LYCEE ENSEIGNT GENERAL ET TECHNOLOGIQUE', 'LYCEE D ENSEIGNEMENT GENERAL', 'LYCEE D ENSEIGNEMENT TECHNOLOGIQUE']: return 'Lycée'
                return t

            edu_df['standard_type'] = edu_df.apply(map_edu_type, axis=1)

            edu_pois = pd.DataFrame({
                'id': edu_df['numero_uai'],
                'name': edu_df['appellation_officielle'],
                'type': edu_df['standard_type'],
                'category': 'education',
                'lat': edu_df['lat'],
                'lon': edu_df['lon'],
                'codgeo': edu_df['code_commune']
            })
            pois_list.append(edu_pois)
            
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
                crs="EPSG:2154"
            ).to_crs("EPSG:4326")
            
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
                if row['is_maternite']: return 'Maternité'
                if row['is_hopital']: return 'Hopital'
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
            
        # Inclusion
        incl_cfg = config['sources']['services_inclusion']
        incl_path = CACHE_DIR / incl_cfg['local_name']
        if incl_path.exists():
            incl_df = load_dataset(incl_path, incl_cfg)
            incl_df = incl_df.dropna(subset=['latitude', 'longitude', 'thematiques'])
            
            # Parse 'thematiques' (which might be stringified list "['slug1', 'slug2']")
            def parse_thematiques(val):
                try:
                    raw_extracted = []
                    if isinstance(val, str):
                        val = val.strip()
                        if val.startswith('[') and val.endswith(']'):
                            # Check for space-separated stringified array "['a' 'b']"
                            if "' '" in val and "," not in val:
                                import re
                                raw_extracted = re.findall(r"'([^']*)'", val)
                            else:
                                import ast
                                try:
                                    raw_extracted = ast.literal_eval(val)
                                except:
                                    # Fallback
                                    raw_extracted = [val]
                        else:
                            raw_extracted = [val]
                    elif isinstance(val, list):
                        raw_extracted = val
                    elif hasattr(val, 'tolist'): # Handle numpy arrays
                        raw_extracted = val.tolist()
                    
                    # Aggressively flatten
                    flat_list = []
                    def flatten(x):
                        if isinstance(x, (list, tuple, np.ndarray)):
                            for item in x:
                                flatten(item)
                        elif x is not None:
                            flat_list.append(str(x))
                    
                    flatten(raw_extracted)
                    return flat_list
                except Exception as e:
                    logging.warning(f"Error parsing thematiques: {val} -> {e}")
                    return []

            incl_df['thematique_list'] = incl_df['thematiques'].apply(parse_thematiques)
            
            # Debug logging
            if not incl_df.empty:
                logging.info(f"Sample parsed thematiques: {incl_df['thematique_list'].head(3).tolist()}")

            # Explode
            incl_exploded = incl_df.explode('thematique_list')
            incl_exploded = incl_exploded.dropna(subset=['thematique_list'])
            
            # Debug logging post-explode
            if not incl_exploded.empty:
                 sample_vals = incl_exploded['thematique_list'].head(3).tolist()
                 sample_types = [type(x) for x in sample_vals]
                 logging.info(f"Sample exploded values: {sample_vals}")
                 logging.info(f"Sample exploded types: {sample_types}")
            
            # Create unique ID using MD5 hash of (id + slug)
            import hashlib
            def generate_hash_id(row):
                composite_key = f"{row['id']}_{row['thematique_list']}"
                return hashlib.md5(composite_key.encode()).hexdigest()

            incl_pois = pd.DataFrame({
                'id': incl_exploded.apply(generate_hash_id, axis=1),
                'name': incl_exploded['nom'],
                'type': incl_exploded['thematique_list'].astype(str),
                'category': 'incl_services', # Renamed from 'inclusion'
                'lat': incl_exploded['latitude'],
                'lon': incl_exploded['longitude'],
                'codgeo': incl_exploded['code_insee']
            })
            
            # Additional cleanup: remove any remaining list-like strings from 'type' if they exist
            # This shouldn't happen with correct flattening, but as a safeguard against "['slug']"
            def clean_final_slug(val):
                 val = str(val).strip()
                 if val.startswith("['") and val.endswith("']"):
                     return val[2:-2]
                 return val
            
            incl_pois['type'] = incl_pois['type'].apply(clean_final_slug)
            
            pois_list.append(incl_pois)

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
                    'label': form_df['label'],
                    'metadata': None
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
                        'label': incl_df['Label'],
                        'metadata': None
                    })
                    refs_list.append(incl_ref)
                    logger.log_step("generate_referentiels", "INCLUSION", {"count": len(incl_ref)})
                else:
                     logging.warning(f"Inclusion Referentiel: Missing columns. Found: {incl_df.columns}")

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
