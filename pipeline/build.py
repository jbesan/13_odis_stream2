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
            # Sum the 'count' column (since we now have aggregated data)
            assoc_count = assoc_df.groupby('codgeo')['count'].sum().rename('lien_social_count').reset_index()
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
        # 4. Dissolve by Bassin de Vie
        # Fix invalid geometries before dissolve
        # 1. Try buffer(0)
        communes_gdf['geometry'] = communes_gdf['geometry'].buffer(0)
        # 2. Filter invalid
        communes_gdf = communes_gdf[communes_gdf.is_valid]
        
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
            
            # Copy raw vertical file to output as well if requested
            shutil.copy2(assoc_path, OUTPUT_DIR / "associations_vertical.parquet")
            
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

            edu_pois = pd.DataFrame({
                'id': edu_df['numero_uai'],
                'name': edu_df['appellation_officielle'],
                'type': edu_df['nature_uai_libe'],
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

            finess_pois = pd.DataFrame({
                'id': sante_df['nofinesset'],
                'name': sante_df['RaisonSociale'],
                'type': sante_df['LibelleCategorieAgregat'],
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
            
            incl_pois = pd.DataFrame({
                'id': incl_df['id'],
                'name': incl_df['nom'],
                'type': incl_df['thematiques'].astype(str),
                'category': 'incl_services', # Renamed from 'inclusion'
                'lat': incl_df['latitude'],
                'lon': incl_df['longitude'],
                'codgeo': incl_df['code_insee']
            })
            pois_list.append(incl_pois)

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
