import argparse
import logging
import requests
import pandas as pd
import geopandas as gpd
import json
from pathlib import Path
from typing import Dict, Any, Optional

from pipeline.common import (
    PipelineLogger, load_config, load_dataset, extract_zip,
    CONFIG_FILE, CACHE_DIR, CLEAN_DIR, STATUS_FILE
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def fetch_source(name: str, source_cfg: Dict[str, Any], logger: PipelineLogger) -> Optional[Path]:
    """Downloads and prepares a single source."""
    url = source_cfg.get('url')
    if not url:
        logger.log_source(name, "SKIPPED", "No URL provided")
        return None

    local_name = source_cfg['local_name']
    local_path = CACHE_DIR / local_name
    
    # Create cache dir
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if local_path.exists():
            logging.info(f"[{name}] File exists. Skipping download.")
            logger.log_source(name, "CACHED", local_path)
        else:
            logging.info(f"[{name}] Downloading from {url}...")
            
            if url.startswith("file://"):
                import shutil
                src_path = Path(url.replace("file://", ""))
                if src_path.exists():
                    shutil.copy(src_path, local_path)
                    logger.log_source(name, "COPIED", local_path)
                else:
                     raise FileNotFoundError(f"Source file not found: {src_path}")
            else:
                response = requests.get(url, stream=True)
                response.raise_for_status()
                with open(local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.log_source(name, "DOWNLOADED", local_path)

        # Handle Zip Extraction
        if source_cfg.get('format') == 'zip' and 'archive_file' in source_cfg:
            extracted_file = source_cfg['archive_file']
            extracted_path = CACHE_DIR / extracted_file
            if not extracted_path.exists():
                logging.info(f"[{name}] Extracting {extracted_file}...")
                extract_zip(local_path, extracted_file)
            return extracted_path
            
        return local_path

    except Exception as e:
        logging.error(f"[{name}] Failed: {e}")
        logger.log_source(name, "ERROR", str(e))
        return None

def clean_bmo_fap(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans BMO data (Bassins d'Emploi + FAP) and saves to parquet."""
    logger.log_step("clean_bmo_fap", "STARTED")
    try:
        bmo_source = config['sources']['bmo']
        bmo_path = CACHE_DIR / bmo_source['local_name']
        
        mapping_source = config['sources']['communes_bassins_emploi']
        mapping_path = CACHE_DIR / mapping_source['local_name']
        
        if not bmo_path.exists() or not mapping_path.exists():
             logging.warning("BMO or Mapping file not found.")
             return

        # 1. Load Mapping (Commune -> Bassin d'Emploi)
        # Expected cols: 'Code commune', 'Code Bassin d'emploi 2021'
        df_mapping = load_dataset(mapping_path, mapping_source, sheet_name=0)
        df_mapping.columns = [c.strip() for c in df_mapping.columns]
        
        # Identify columns
        com_col = next((c for c in df_mapping.columns if 'code_commune' in c), None)
        be_col = next((c for c in df_mapping.columns if 'code_bassin' in c), None)
        
        if not com_col or not be_col:
            logging.warning(f"BMO Mapping: Missing columns. Found: {df_mapping.columns}")
            return
            
        df_mapping = df_mapping[[com_col, be_col]].rename(columns={com_col: 'codgeo', be_col: 'code_be'})
        df_mapping['codgeo'] = df_mapping['codgeo'].astype(str).str.zfill(5)
        df_mapping['code_be'] = df_mapping['code_be'].astype(str)
        
        # 2. Load BMO Data (Bassins d'Emploi)
        # Sheet: BMO_2025_open_data
        # Cols: BE25, Code métier BMO, met
        df_bmo = load_dataset(bmo_path, bmo_source, sheet_name="BMO_2025_open_data")
        df_bmo.columns = [c.strip() for c in df_bmo.columns]
        
        # Identify columns
        bmo_be_col = next((c for c in df_bmo.columns if 'BE25' in c), None)
        fap_col = next((c for c in df_bmo.columns if 'Code métier BMO' in c), None)
        count_col = next((c for c in df_bmo.columns if 'met' == c or 'met ' in c), None) # 'met' is exact match usually
        
        if not count_col and 'met' in df_bmo.columns: count_col = 'met'
        
        if not bmo_be_col or not fap_col or not count_col:
             logging.warning(f"BMO Data: Missing columns. Found: {df_bmo.columns}")
             return
             
        df_bmo = df_bmo[[bmo_be_col, fap_col, count_col]].rename(columns={
            bmo_be_col: 'code_be', 
            fap_col: 'fap_code', 
            count_col: 'count'
        })
        df_bmo['code_be'] = df_bmo['code_be'].astype(str)
        df_bmo['count'] = pd.to_numeric(df_bmo['count'], errors='coerce').fillna(0).astype(int)
        
        # 3. Join Mapping + BMO
        # We want to attribute the BMO data of the Bassin to EACH commune in that Bassin.
        # This is what the user requested: "count will be based on the Bassin d'Emploi of the commune"
        merged = df_mapping.merge(df_bmo, on='code_be', how='inner')
        
        # 4. Extract Top 5 FAP per Commune
        # Sort by count desc
        merged = merged.sort_values(['codgeo', 'count'], ascending=[True, False])
        
        # Take top 10
        top_5 = merged.groupby('codgeo').head(10)
        
        # Save Vertical Table
        bmo_vertical = top_5[['codgeo', 'fap_code', 'count']]
        output_vertical = CLEAN_DIR / "bmo_vertical.parquet"
        bmo_vertical.to_parquet(output_vertical)
        
        # 5. Stats (Total offers per commune = Total offers in its Bassin)
        # Sum of all offers in the Bassin
        bmo_total = df_bmo.groupby('code_be')['count'].sum().reset_index().rename(columns={'count': 'metiers_offres_diff'})
        stats = df_mapping.merge(bmo_total, on='code_be', how='left')
        stats = stats[['codgeo', 'metiers_offres_diff', 'code_be']]
        stats['metiers_offres_diff'] = stats['metiers_offres_diff'].fillna(0).astype(int)
        
        output_stats = CLEAN_DIR / "bmo_stats.parquet"
        stats.to_parquet(output_stats)
        
        logger.log_step("clean_bmo_fap", "COMPLETED", {"vertical": str(output_vertical), "stats": str(output_stats)})

    except Exception as e:
        logger.log_step("clean_bmo_fap", "ERROR", {"error": str(e)})
        logging.error(f"BMO Cleaning failed: {e}")

def clean_population_active(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Population Active and saves to parquet."""
    logger.log_step("clean_population_active", "STARTED")
    try:
        source = config['sources']['population_active']
        path = CACHE_DIR / source['archive_file']
        
        if not path.exists():
             logging.warning("Population Active file not found.")
             return
             
        actif = load_dataset(path, source)
        
        required_cols = ['TIME_PERIOD', 'GEO_OBJECT', 'PCS', 'EMPSTA_ENQ', 'GEO', 'OBS_VALUE']
        if not all(col in actif.columns for col in required_cols):
             logging.warning("Population Active missing columns")
             return
             
        actif_2022 = actif[
            (actif.TIME_PERIOD == 2022) & 
            (actif.GEO_OBJECT == "COM") & 
            (actif.PCS == "_T") & 
            (actif.EMPSTA_ENQ.isin(["1T2", "1"]))
        ].pivot_table(
            index="GEO", 
            columns="EMPSTA_ENQ", 
            values="OBS_VALUE", 
            aggfunc="sum"
        )
        
        if "1T2" in actif_2022.columns and "1" in actif_2022.columns:
            actif_2022["pop_chomeurs"] = actif_2022["1T2"] - actif_2022["1"]
            actif_2022.rename(columns={"1T2": "pop_active", "1": "pop_employes"}, inplace=True)
            actif_2022 = actif_2022[["pop_active", "pop_employes", "pop_chomeurs"]]
            actif_2022.index.name = 'codgeo'
            actif_2022.reset_index(inplace=True)
            actif_2022['codgeo'] = actif_2022['codgeo'].astype(str).str.zfill(5)
            
            output_path = CLEAN_DIR / "population_active.parquet"
            actif_2022.to_parquet(output_path)
            logger.log_step("clean_population_active", "COMPLETED", {"path": str(output_path)})
        else:
             logging.warning("Population Active pivot failed.")

    except Exception as e:
        logger.log_step("clean_population_active", "ERROR", {"error": str(e)})
        logging.error(f"Population Active Cleaning failed: {e}")

def clean_lovac(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans LOVAC and saves to parquet."""
    logger.log_step("clean_lovac", "STARTED")
    try:
        source = config['sources']['logement_vacant']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        df.columns = [c.strip() for c in df.columns]
        
        codgeo_col = next((c for c in df.columns if 'CODGEO' in c), None)
        vac_col = 'pp_vacant_plus_2ans_25'
        if vac_col not in df.columns:
             vac_col = next((c for c in df.columns if 'vacant_plus_2ans' in c), None)

        if codgeo_col and vac_col:
            df[vac_col] = pd.to_numeric(df[vac_col].replace('s', 0), errors='coerce').fillna(0)
            df_out = df[[codgeo_col, vac_col]].rename(columns={codgeo_col: 'codgeo', vac_col: 'pp_vacant_plus_2ans_25'})
            df_out['codgeo'] = df_out['codgeo'].astype(str)
            
            output_path = CLEAN_DIR / "lovac.parquet"
            df_out.to_parquet(output_path)
            logger.log_step("clean_lovac", "COMPLETED", {"path": str(output_path)})
    except Exception as e:
        logger.log_step("clean_lovac", "ERROR", {"error": str(e)})

def clean_rpls(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans RPLS and saves to parquet."""
    logger.log_step("clean_rpls", "STARTED")
    try:
        source = config['sources']['logement_social']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        df.columns = [str(c).strip() for c in df.columns]
        
        if 'CODGEO' in df.columns:
            df['codgeo'] = df['CODGEO'].astype(str).str.zfill(5)
        elif 'DEPCOM_ARM' in df.columns:
             df['codgeo'] = df['DEPCOM_ARM'].astype(str).str.zfill(5)
        elif 'DEP' in df.columns and 'COM' in df.columns:
            df['codgeo'] = df['DEP'].astype(str).str.zfill(2) + df['COM'].astype(str).str.zfill(3)
        else:
            logging.warning("RPLS: No codgeo found")
            return

        cols = df.columns.tolist()
        total_col = next((c for c in cols if 'total' in c.lower() and 'parc' in c.lower()), None)
        if not total_col:
             total_col = next((c for c in cols if c in ['PARC_SOCIAL_NB', 'NB_LOG_TOT', 'nb_lgt_tot']), None)
        
        vac_col = next((c for c in cols if 'vacant' in c.lower() or 'inoccup' in c.lower()), None)
        
        if total_col and vac_col:
            df['log_soc_total'] = pd.to_numeric(df[total_col], errors='coerce').fillna(0)
            df['log_soc_inoccupes'] = pd.to_numeric(df[vac_col], errors='coerce').fillna(0)
            df_out = df[['codgeo', 'log_soc_total', 'log_soc_inoccupes']]
            
            output_path = CLEAN_DIR / "rpls.parquet"
            df_out.to_parquet(output_path)
            logger.log_step("clean_rpls", "COMPLETED", {"path": str(output_path)})
    except Exception as e:
        logger.log_step("clean_rpls", "ERROR", {"error": str(e)})

def clean_caf(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans CAF and saves to parquet."""
    logger.log_step("clean_caf", "STARTED")
    try:
        source = config['sources']['caf']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        df.columns = [c.strip() for c in df.columns]
        
        codgeo_col = next((c for c in df.columns if 'codgeo' in c.lower() or 'insee' in c.lower() or c == 'numcom'), None)
        if codgeo_col:
            df.rename(columns={codgeo_col: 'codgeo'}, inplace=True)
            df['codgeo'] = df['codgeo'].astype(str).str.zfill(5)
            
            if 'annee' in df.columns:
                max_year = df['annee'].max()
                df = df[df['annee'] == max_year]
                
            if 'taux_accueil_total' in df.columns:
                df.rename(columns={'taux_accueil_total': 'taux_couverture'}, inplace=True)
            elif 'txcouv_com' in df.columns:
                df.rename(columns={'txcouv_com': 'taux_couverture'}, inplace=True)
                
            if 'taux_couverture' in df.columns:
                df['taux_couverture'] = pd.to_numeric(df['taux_couverture'], errors='coerce').fillna(0)
                df_out = df[['codgeo', 'taux_couverture']]
                
                output_path = CLEAN_DIR / "caf.parquet"
                df_out.to_parquet(output_path)
                logger.log_step("clean_caf", "COMPLETED", {"path": str(output_path)})
    except Exception as e:
        logger.log_step("clean_caf", "ERROR", {"error": str(e)})

def clean_education(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Education and saves to parquet."""
    logger.log_step("clean_education", "STARTED")
    try:
        source = config['sources']['education_annuaire']
        path = CACHE_DIR / source['local_name']
        
        if not path.exists(): return

        df = load_dataset(path, source)
        
        # Columns: 'Code INSEE de la commune', 'Code nature', 'Nature'
        # Normalize columns
        df.columns = [c.strip() for c in df.columns]
        
        # Identify columns
        codgeo_col = next((c for c in df.columns if 'code_commune' in c), None) # Changed from 'Code INSEE'
        nature_col = 'nature_uai' # Changed from 'Code nature'
        
        if not codgeo_col or 'nature_uai_libe' not in df.columns:
             logging.warning(f"Education: Missing columns. Found: {df.columns}")
             return

        df['codgeo'] = df[codgeo_col].astype(str).str.zfill(5)
        
        # Aggregation logic based on 'nature_uai_libe'
        # Maternelles = ['ECOLE MATERNELLE']
        # Elementaires = ['ECOLE DE NIVEAU ELEMENTAIRE']
        # Collèges = ['COLLEGE']
        # Lycées = ['LYCEE PROFESSIONNEL', 'LYCEE ENSEIGNT GENERAL ET TECHNOLOGIQUE', 'LYCEE D ENSEIGNEMENT GENERAL', 'LYCEE D ENSEIGNEMENT TECHNOLOGIQUE']
        
        if 'nature_uai_libe' not in df.columns:
             logging.warning(f"Education: Missing 'nature_uai_libe'. Found: {df.columns}")
             return

        # Create flags
        df['is_maternelle'] = df['nature_uai_libe'].isin(['ECOLE MATERNELLE']).astype(int)
        df['is_elementaire'] = df['nature_uai_libe'].isin(['ECOLE DE NIVEAU ELEMENTAIRE']).astype(int)
        df['is_college'] = df['nature_uai_libe'].isin(['COLLEGE']).astype(int)
        df['is_lycee'] = df['nature_uai_libe'].isin([
            'LYCEE PROFESSIONNEL', 
            'LYCEE ENSEIGNT GENERAL ET TECHNOLOGIQUE', 
            'LYCEE D ENSEIGNEMENT GENERAL', 
            'LYCEE D ENSEIGNEMENT TECHNOLOGIQUE'
        ]).astype(int)
        
        df_agg = df.groupby('codgeo').agg({
            'is_maternelle': 'sum',
            'is_elementaire': 'sum',
            'is_college': 'sum',
            'is_lycee': 'sum'
        }).rename(columns={
            'is_maternelle': 'edu_maternelle_ct',
            'is_elementaire': 'edu_elementaire_ct',
            'is_college': 'edu_college_ct',
            'is_lycee': 'edu_lycee_ct'
        }).reset_index()
        
        output_path = CLEAN_DIR / "education.parquet"
        df_agg.to_parquet(output_path)
        logger.log_step("clean_education", "COMPLETED", {"path": str(output_path)})
            
    except Exception as e:
        logger.log_step("clean_education", "ERROR", {"error": str(e)})

def clean_inclusion(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Inclusion and saves to parquet."""
    logger.log_step("clean_inclusion", "STARTED")
    try:
        source = config['sources']['services_inclusion']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        if 'code_insee' in df.columns:
             df.rename(columns={'code_insee': 'codgeo'}, inplace=True)
        
        if 'codgeo' in df.columns:
            df_agg = df.groupby('codgeo').size().rename('svc_incl_count').reset_index()
            output_path = CLEAN_DIR / "inclusion.parquet"
            df_agg.to_parquet(output_path)
            logger.log_step("clean_inclusion", "COMPLETED", {"path": str(output_path)})
    except Exception as e:
        logger.log_step("clean_inclusion", "ERROR", {"error": str(e)})

def clean_associations(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Associations and saves to parquet."""
    logger.log_step("clean_associations", "STARTED")
    try:
        source = config['sources']['associations']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        df.columns = [c.strip() for c in df.columns]
        
        if 'adrs_codeinsee' in df.columns:
            df.rename(columns={'adrs_codeinsee': 'codgeo'}, inplace=True)
        if 'objet_social1' in df.columns:
            df.rename(columns={'objet_social1': 'id_waldec'}, inplace=True)
            
        if 'codgeo' in df.columns and 'id_waldec' in df.columns:
            # Need config for WALDEC codes. 
            # We can load them from app config or hardcode/duplicate for pipeline isolation.
            # For now, let's try to load from app.config if possible, or just use a known list.
            # To avoid dependency issues, I will read them from config.py if I can, or just skip filtering here?
            # No, I need to filter to get 'lien_social'.
            # Let's import from app.config carefully.
            import sys
            sys.path.append(str(Path(__file__).resolve().parent.parent))
            from app import config as cfg
            core_prefixes = tuple(cfg.WALDEC_CORE_INCLUSION)
            
            df['id_waldec'] = df['id_waldec'].astype(str).str.zfill(6)
            df['codgeo'] = df['codgeo'].astype(str).str.zfill(5)
            
            core_mask = df['id_waldec'].str.startswith(core_prefixes, na=False)
            lien_social = df[core_mask].groupby('codgeo').size().rename('lien_social_count').reset_index()
            
            output_path = CLEAN_DIR / "associations_vertical.parquet"
            lien_social.to_parquet(output_path)
            logger.log_step("clean_associations", "COMPLETED", {"path": str(output_path)})
    except Exception as e:
        logger.log_step("clean_associations", "ERROR", {"error": str(e)})

def clean_voisins(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Voisins and saves to parquet."""
    logger.log_step("clean_voisins", "STARTED")
    try:
        source = config['sources']['voisins']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        # Expected: insee_com, insee_voisins (list or string?)
        # Actually the file usually has pairs or list.
        # Let's assume standard format: 'insee_com', 'insee_voisins' (list of codes)
        # If it's the adjacency file from data.gouv, it might be an adjacency list.
        
        # Assuming format: insee, voisins (string separated by | or ,)
        if 'insee' in df.columns and 'voisins' in df.columns:
             df['codgeo'] = df['insee'].astype(str).str.zfill(5)
             # Voisins might be a string "12345|67890"
             # We want a list.
             df['codgeo_voisins'] = df['voisins'].astype(str).apply(lambda x: x.split('|') if '|' in x else x.split(','))
             
             df_out = df[['codgeo', 'codgeo_voisins']]
             output_path = CLEAN_DIR / "voisins.parquet"
             df_out.to_parquet(output_path)
             logger.log_step("clean_voisins", "COMPLETED", {"path": str(output_path)})
        else:
             logging.warning(f"Voisins: Columns not found. Found: {df.columns}")

    except Exception as e:
        logger.log_step("clean_voisins", "ERROR", {"error": str(e)})

def clean_population(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Population and saves to parquet."""
    logger.log_step("clean_population", "STARTED")
    try:
        source = config['sources']['population']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        
        pop_col = next((c for c in df.columns if 'pop' in c.lower()), None)
        geo_col = next((c for c in df.columns if 'codgeo' in c.lower() or 'com' in c.lower()), None)
        
        if pop_col and geo_col:
            df = df[[geo_col, pop_col]].rename(columns={geo_col: 'codgeo', pop_col: 'population'})
            df['codgeo'] = df['codgeo'].astype(str).str.zfill(5)
            
            output_path = CLEAN_DIR / "population.parquet"
            df.to_parquet(output_path)
            logger.log_step("clean_population", "COMPLETED", {"path": str(output_path)})
    except Exception as e:
        logger.log_step("clean_population", "ERROR", {"error": str(e)})

def clean_communes(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Communes and saves to parquet."""
    logger.log_step("clean_communes", "STARTED")
    try:
        source = config['sources']['communes']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        gdf = load_dataset(path, source)
        
        if 'codgeo' not in gdf.columns:
            if 'INSEE_COM' in gdf.columns:
                 gdf.rename(columns={'INSEE_COM': 'codgeo'}, inplace=True)
            elif 'code' in gdf.columns:
                 gdf.rename(columns={'code': 'codgeo'}, inplace=True)
        
        if 'codgeo' in gdf.columns:
            output_path = CLEAN_DIR / "communes.parquet"
            gdf.to_parquet(output_path)
            logger.log_step("clean_communes", "COMPLETED", {"path": str(output_path)})
    except Exception as e:
        logger.log_step("clean_communes", "ERROR", {"error": str(e)})

def main():
    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)
    
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    
    logger.log_step("ingest_all", "STARTED")
    
    # 1. Fetch
    for name, source_cfg in config['sources'].items():
        fetch_source(name, source_cfg, logger)
        
    # 2. Clean
    clean_communes(config, logger)
    clean_bmo_fap(config, logger)
    clean_population(config, logger)
    clean_population_active(config, logger)
    clean_lovac(config, logger)
    clean_rpls(config, logger)
    clean_caf(config, logger)
    clean_education(config, logger)
    clean_inclusion(config, logger)
    clean_inclusion(config, logger)
    clean_associations(config, logger)
    clean_voisins(config, logger)
    
    logger.log_step("ingest_all", "COMPLETED")

if __name__ == "__main__":
    main()
