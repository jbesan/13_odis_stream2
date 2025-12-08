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
                verify_ssl = source_cfg.get('verify_ssl', True)
                response = requests.get(url, stream=True, verify=verify_ssl)
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
        # Dynamic Sheet Detection
        import re
        bmo_xl = pd.ExcelFile(bmo_path, engine='calamine')
        sheet_pattern = re.compile(r'BMO_(\d+)_open_data', re.IGNORECASE)
        
        target_sheet = None
        for sheet in bmo_xl.sheet_names:
            if sheet_pattern.search(sheet) or sheet == "BMO_2025_open_data": # Fallback/Priority
                target_sheet = sheet
                break
        
        if not target_sheet:
             # Fallback to first sheet or specific default?
             logging.warning(f"BMO: No matching sheet found in {bmo_xl.sheet_names}. Using default 'BMO_2025_open_data'")
             target_sheet = "BMO_2025_open_data"

        logging.info(f"BMO: Using sheet {target_sheet}")
        
        df_bmo = pd.read_excel(bmo_path, sheet_name=target_sheet, engine='calamine')
        df_bmo.columns = [c.strip() for c in df_bmo.columns]
        
        # Identify columns
        # BE column usually "BE25", "BE24", etc.
        be_pattern = re.compile(r'^BE(\d+)$')
        bmo_be_col = next((c for c in df_bmo.columns if be_pattern.match(c)), None)
        
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
             
        max_year = actif['TIME_PERIOD'].max()
        logging.info(f"Population Active: Using max year {max_year}")
        
        actif_2022 = actif[
            (actif.TIME_PERIOD == max_year) & 
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
        if codgeo_col:
            # Dynamic Year Detection
            import re
            
            # Find all years for vacancy data
            years = []
            year_pattern = re.compile(r'pp_vacant_plus_2ans_(\d+)')
            
            for col in df.columns:
                match = year_pattern.search(col)
                if match:
                    years.append(int(match.group(1)))
            
            if years:
                max_year = max(years)
                # Ensure it's two digits if format assumes that, but usually int is fine for logic
                # The headers were like 'pp_vacant_plus_2ans_25'
                # so N is 25.
                
                # Vacancy Column (Year N)
                vac_col = f'pp_vacant_plus_2ans_{max_year}'
                
                # Total Column (Year N-1)
                target_total_year = max_year - 1
                total_col = f'pp_total_{target_total_year}'
                
                logging.info(f"LOVAC: Detected max year {max_year}. Using {vac_col} and {total_col}")
            else:
                # Fallback
                logging.warning("LOVAC: Could not detect years. Using default 25/24.")
                vac_col = 'pp_vacant_plus_2ans_25'
                total_col = 'pp_total_24'

            # Allow fallback if dynamic total dict doesn't exist but static might? 
            # Actually, let's just stick to the specific columns.
            
            if vac_col not in df.columns:
                 # Try finding any valid vac col
                 vac_col = next((c for c in df.columns if 'vacant_plus_2ans' in c), None)

            if vac_col and vac_col in df.columns:
                df[vac_col] = pd.to_numeric(df[vac_col].replace('s', 0), errors='coerce').fillna(0)
                
                # Extract Total Housing
                if total_col in df.columns:
                    df[total_col] = pd.to_numeric(df[total_col].replace('s', 0), errors='coerce').fillna(0)
                else:
                    logging.warning(f"LOVAC: {total_col} not found in {df.columns}. Setting to 0.")
                    df[total_col] = 0

                df_out = df[[codgeo_col, vac_col, total_col]].rename(columns={
                    codgeo_col: 'codgeo', 
                    vac_col: 'pp_vacant_plus_2ans_25', # Keep standardized internal name
                    total_col: 'log_priv_total_24'     # Keep standardized internal name
                })
                df_out['codgeo'] = df_out['codgeo'].astype(str)
                
                output_path = CLEAN_DIR / "lovac.parquet"
                df_out.to_parquet(output_path)
                logger.log_step("clean_lovac", "COMPLETED", {"path": str(output_path), "year_vac": vac_col, "year_total": total_col})
            else:
                 logging.warning(f"LOVAC: Vacancy column {vac_col} not found.")

        else:
            logging.warning("LOVAC: CODGEO not found.")
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
            
            # Save detailed associations for vertical table
            # We keep all associations to allow dynamic filtering in the app (Core vs Affinities)
            # We aggregate by codgeo and id_waldec to save space and provide a count
            df_out = df.groupby(['codgeo', 'id_waldec']).size().rename('count').reset_index()
            
            output_path = CLEAN_DIR / "associations_vertical.parquet"
            df_out.to_parquet(output_path)
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
        
        # Assuming format: insee, insee_voisins (string separated by | or ,)
        if 'insee' in df.columns and 'insee_voisins' in df.columns:
             df['codgeo'] = df['insee'].astype(str).str.zfill(5)
             # Voisins might be a string "12345|67890"
             # We want a list.
             df['codgeo_voisins'] = df['insee_voisins'].astype(str).apply(lambda x: x.split('|') if '|' in x else x.split(','))
             
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

def clean_political(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Political Nuance and saves to parquet."""
    logger.log_step("clean_political", "STARTED")
    try:
        source = config['sources']['political_nuance']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        df.columns = [c.strip() for c in df.columns]
        
        # Expected: 'Code Insee Commune', 'Nuance' OR 'cog_commune', 'nuance_politique'
        codgeo_col = next((c for c in df.columns if 'Code Insee' in c or 'cog_commune' in c), None)
        nuance_col = next((c for c in df.columns if ('Nuance' in c or 'nuance_politique' in c) and 'Libellé' not in c), None)
        
        if codgeo_col and nuance_col:
            # Mapping
            POL_MAPPING = {
                'UG': 1.0, 'COM': 1.0, 'FI': 1.0, 'SOC': 1.0, 'RDG': 1.0, 'ECO': 1.0, 'DVG': 1.0, 'VEC': 1.0,
                'REN': 0.5, 'MDM': 0.5, 'HOR': 0.5, 'DVC': 0.5,
                'LR': 0.2, 'DVD': 0.2, 'UDI': 0.2,
                'RN': 0.0, 'REC': 0.0, 'EXD': 0.0
            }
            
            df['pol_num'] = df[nuance_col].map(POL_MAPPING).fillna(0.5) # Default to neutral
            df['codgeo'] = df[codgeo_col].astype(str).str.zfill(5)
            
            df_out = df[['codgeo', 'pol_num']]
            output_path = CLEAN_DIR / "political.parquet"
            df_out.to_parquet(output_path)
            logger.log_step("clean_political", "COMPLETED", {"path": str(output_path)})
        else:
            logging.warning(f"Political: Columns not found. Found: {df.columns}")
            
    except Exception as e:
        logger.log_step("clean_political", "ERROR", {"error": str(e)})

def clean_housing_occupation(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Housing Occupation and saves to parquet."""
    logger.log_step("clean_housing_occupation", "STARTED")
    try:
        source = config['sources']['housing_occupation']
        path = CACHE_DIR / source['archive_file']
        if not path.exists(): return

        # Load with correct separator (likely ';')
        try:
            df = pd.read_csv(path, sep=';')
            if len(df.columns) < 2:
                 df = pd.read_csv(path, sep=',')
        except:
             df = pd.read_csv(path, sep=',')
             
        # Filter
        if 'TIME_PERIOD' in df.columns:
            max_year = df['TIME_PERIOD'].max()
            logging.info(f"Housing Occupation: Using max year {max_year}")
            df = df[df['TIME_PERIOD'] == max_year]
        if 'GEO_OBJECT' in df.columns:
            df = df[df['GEO_OBJECT'] == 'COM']
            
        # We need Taux d'occupation.
        # Assuming OCC_IND has 'STD_OCC' (Standard), 'OVER_OCC' (Suroccupation), 'UNDER_OCC' (Sous-occupation)
        # And OBS_VALUE is the count of dwellings.
        # We want the rate of "Good" occupation? Or rate of "Under" (room to spare)?
        # User said "build a scale based of OCC_IND".
        # Let's save the raw counts pivoted by OCC_IND and let build.py calculate the ratio.
        
        if 'GEO' in df.columns and 'OCC_IND' in df.columns and 'OBS_VALUE' in df.columns:
            df_pivot = df.pivot_table(index='GEO', columns='OCC_IND', values='OBS_VALUE', aggfunc='sum').reset_index()
            df_pivot.rename(columns={'GEO': 'codgeo'}, inplace=True)
            df_pivot['codgeo'] = df_pivot['codgeo'].astype(str).str.zfill(5)
            
            output_path = CLEAN_DIR / "housing_occupation.parquet"
            df_pivot.to_parquet(output_path)
            logger.log_step("clean_housing_occupation", "COMPLETED", {"path": str(output_path)})
            
    except Exception as e:
        logger.log_step("clean_housing_occupation", "ERROR", {"error": str(e)})

def clean_school_effectifs(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans School Effectifs and saves to parquet."""
    logger.log_step("clean_school_effectifs", "STARTED")
    try:
        source = config['sources']['education_effectifs']
        path = CACHE_DIR / source['local_name']
        
        # Load Codes Postaux for mapping
        cp_source = config['sources']['codes_postaux']
        cp_path = CACHE_DIR / cp_source['local_name']
        
        if not path.exists() or not cp_path.exists():
             logging.warning("Education Effectifs or Codes Postaux not found.")
             return

        df = load_dataset(path, source)
        df_cp = load_dataset(cp_path, cp_source)
        
        # Prepare CP data
        # Index(['#Code_commune_INSEE', 'Nom_de_la_commune', 'Code_postal', ...])
        df_cp = df_cp.rename(columns={
            '#Code_commune_INSEE': 'code_insee',
            'Code_postal': 'code_postal',
            'Nom_de_la_commune': 'nom_commune'
        })
        df_cp['code_postal'] = df_cp['code_postal'].astype(str).str.zfill(5)
        
        def normalize_city(s):
            if not isinstance(s, str): return ""
            # Replace hyphens with spaces
            s = s.upper().replace('-', ' ').replace("'", " ")
            # Standardize Saint/Sainte
            s = s.replace("SAINT ", "ST ").replace("SAINTE ", "STE ")
            # Strip extra spaces
            return " ".join(s.split())

        df_cp['nom_commune_norm'] = df_cp['nom_commune'].apply(normalize_city)
        
        # Prepare Effectifs data
        df['code_postal'] = df['code_postal'].astype(str).str.zfill(5)
        df['commune_norm'] = df['commune'].apply(normalize_city)
        
        # Merge on Code Postal and Normalized Name
        merged = df.merge(df_cp, left_on=['code_postal', 'commune_norm'], right_on=['code_postal', 'nom_commune_norm'], how='left')
        
        # Check match rate
        missing = merged[merged['code_insee'].isna()]
        if not missing.empty:
            logging.warning(f"Education Effectifs: {len(missing)} rows failed to map to INSEE code.")
            # Debug sample
            if len(missing) > 0:
                logging.warning(f"Sample missing: {missing[['code_postal', 'commune', 'commune_norm']].head(5).to_dict('records')}")
        
        # Filter valid
        valid = merged.dropna(subset=['code_insee'])
        
        effectif_col = 'nombre_total_eleves'
        if effectif_col not in valid.columns:
             logging.warning(f"Col {effectif_col} not found")
             return

        # Group by INSEE Code
        df_agg = valid.groupby('code_insee')[effectif_col].sum().reset_index()
        df_agg.rename(columns={'code_insee': 'codgeo', effectif_col: 'total_eleves'}, inplace=True)
        
        # Count Schools
        if 'numero_ecole' in valid.columns:
            df_count = valid.groupby('code_insee')['numero_ecole'].nunique().reset_index().rename(columns={'code_insee': 'codgeo', 'numero_ecole': 'ecoles_count'})
            df_agg = df_agg.merge(df_count, on='codgeo', how='left')
        
        output_path = CLEAN_DIR / "school_effectifs.parquet"
        df_agg.to_parquet(output_path)
        logger.log_step("clean_school_effectifs", "COMPLETED", {"path": str(output_path), "mapped": len(valid), "total": len(df)})
            
    except Exception as e:
        logger.log_step("clean_school_effectifs", "ERROR", {"error": str(e)})

def clean_bpe(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans BPE data for Creches and Petite Enfance."""
    logger.log_step("clean_bpe", "STARTED")
    try:
        source = config['sources']['bpe']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        
        # Filter for relevant types
        target_types = {
            'A401': 'Creche',
            'A402': 'Halte_Garderie',
            'A403': 'Micro_Creche',
            'A404': 'Creche_Familiale',
            'A405': 'Jardin_Enfants',
            'A406': 'MAM'
        }
        
        # Check columns: DEP, COM, TYPEQU, LAMBERT_X, LAMBERT_Y
        # Construct CODGEO
        if 'DEPCOM' in df.columns:
             df['codgeo'] = df['DEPCOM'].astype(str).str.zfill(5)
        elif 'DEP' in df.columns and 'COM' in df.columns:
            df['codgeo'] = df['DEP'].astype(str).str.zfill(2) + df['COM'].astype(str).str.zfill(3)
        elif 'CODGEO' in df.columns:
            df['codgeo'] = df['CODGEO'].astype(str).str.zfill(5)
            
        if 'TYPEQU' not in df.columns:
             logging.warning("BPE: TYPEQU column not found.")
             return

        df_filtered = df[df['TYPEQU'].isin(target_types.keys())].copy()
        df_filtered['type_libelle'] = df_filtered['TYPEQU'].map(target_types)
        
        # 1. Output Counts (aggregated by commune)
        counts = df_filtered.groupby('codgeo').size().rename('bpe_creches_count').reset_index()
        
        output_cols = CLEAN_DIR / "bpe_petite_enfance_cols.parquet"
        counts.to_parquet(output_cols)
        
        # 2. Output POIs Details
        # Rename columns to match POI schema
        # id (generated), name (default?), type, category, lat, lon
        # BPE parquet usually has LAMBERT_X, LAMBERT_Y in RGF93 (EPSG:2154)
        if 'LAMBERT_X' in df_filtered.columns and 'LAMBERT_Y' in df_filtered.columns:
             gdf = gpd.GeoDataFrame(
                 df_filtered, 
                 geometry=gpd.points_from_xy(df_filtered.LAMBERT_X, df_filtered.LAMBERT_Y),
                 crs="EPSG:2154"
             ).to_crs("EPSG:4326")
             
             pois = pd.DataFrame({
                 'code_equip': gdf.index.astype(str), # Use index as partial ID
                 'type': gdf['type_libelle'],
                 'category': 'education', # or 'petite_enfance'
                 'lat': gdf.geometry.y,
                 'lon': gdf.geometry.x,
                 'codgeo': gdf['codgeo'],
                 'metadata': gdf['TYPEQU']
             })
             
             output_pois = CLEAN_DIR / "bpe_petite_enfance_pois.parquet"
             pois.to_parquet(output_pois)
             
             logger.log_step("clean_bpe", "COMPLETED", {
                 "cols": str(output_cols),
                 "pois": str(output_pois),
                 "count": len(df_filtered)
             })
        else:
             logging.warning("BPE: Coordinates columns not found (LAMBERT_X, LAMBERT_Y). Skipping POIs.")
             # Still save counts
             logger.log_step("clean_bpe", "PARTIAL", {"cols": str(output_cols)})

    except Exception as e:
        logger.log_step("clean_bpe", "ERROR", {"error": str(e)})

def clean_formations(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Formations and saves to parquet."""
    logger.log_step("clean_formations", "STARTED")
    try:
        source = config['sources']['formations']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        # Load Excel, skip first row if empty (header=1 usually works if row 2 is header)
        # We saw row 1 was empty, row 2 had "Formations générales".
        # Let's try header=1 or 2.
        # Actually, let's load with header=None and find the row with "Code postal" or "Ville"
        df = pd.read_excel(path, header=None)
        
        # Find header row
        header_idx = None
        for i, row in df.iterrows():
            row_str = row.astype(str).str.lower().tolist()
            if any('code postal' in s for s in row_str) or any('ville' in s for s in row_str):
                header_idx = i
                break
        
        if header_idx is not None:
            df.columns = df.iloc[header_idx]
            df = df.iloc[header_idx+1:]
            
            # Identify columns
            # We need to map to commune code. We only have Code Postal and Ville.
            # We need a CP -> INSEE mapping.
            # We have 'codes_postaux' source in DATASOURCES.md but not in sources.yaml?
            # Wait, sources.yaml has 'communes' geojson.
            # We can use a CP mapping file if we have one.
            # Or we can just save the raw formations with CP and let build.py handle mapping (if we have a mapping table).
            # For now, let's save what we have.
            
            # Columns might be: 'Raison sociale', 'Code postal', 'Ville', 'Domaines de formation'
            df.columns = [str(c).strip() for c in df.columns]
            
            cp_col = next((c for c in df.columns if 'code postal' in c.lower()), None)
            ville_col = next((c for c in df.columns if 'ville' in c.lower()), None)
            domaine_col = next((c for c in df.columns if 'domaines' in c.lower()), None)
            
            if cp_col:
                df_out = df[[cp_col, ville_col, domaine_col]].copy() if ville_col and domaine_col else df[[cp_col]].copy()
                df_out.rename(columns={cp_col: 'code_postal'}, inplace=True)
                
                output_path = CLEAN_DIR / "formations.parquet"
                df_out.to_parquet(output_path)
                logger.log_step("clean_formations", "COMPLETED", {"path": str(output_path)})
            else:
                 logging.warning(f"Formations: Columns not found. Found: {df.columns}")
        else:
             logging.warning("Formations: Header not found")
                
    except Exception as e:
        logger.log_step("clean_formations", "ERROR", {"error": str(e)})

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
    clean_political(config, logger)
    clean_housing_occupation(config, logger)
    clean_school_effectifs(config, logger)
    clean_school_effectifs(config, logger)
    clean_bpe(config, logger)
    clean_codes_postaux(config, logger)
    clean_formations(config, logger)
    
    logger.log_step("ingest_all", "COMPLETED")

def clean_codes_postaux(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Codes Postaux and saves to parquet."""
    logger.log_step("clean_codes_postaux", "STARTED")
    try:
        source = config['sources']['codes_postaux']
        path = CACHE_DIR / source['local_name']
        if not path.exists(): return

        df = load_dataset(path, source)
        
        # Normalize columns
        df.columns = [c.strip() for c in df.columns]
        
        # Identify columns
        cp_col = next((c for c in df.columns if 'Code_postal' in c or 'code_postal' in c), None)
        insee_col = next((c for c in df.columns if 'Code_commune_INSEE' in c or 'code_commune_insee' in c), None)
        
        if cp_col and insee_col:
            df = df[[cp_col, insee_col]].copy()
            df['code_postal'] = df[cp_col].astype(str).str.zfill(5)
            df['codgeo'] = df[insee_col].astype(str).str.zfill(5)
            
            df_out = df[['code_postal', 'codgeo']].drop_duplicates()
            
            output_path = CLEAN_DIR / "codes_postaux.parquet"
            df_out.to_parquet(output_path)
            logger.log_step("clean_codes_postaux", "COMPLETED", {"path": str(output_path)})
        else:
             logging.warning(f"Codes Postaux: Columns not found. Found: {df.columns}")

    except Exception as e:
        logger.log_step("clean_codes_postaux", "ERROR", {"error": str(e)})

def clean_formations(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Formations and saves to parquet."""
    logger.log_step("clean_formations", "STARTED")
    try:
        # 1. Load Codes Postaux Mapping
        cp_path = CLEAN_DIR / "codes_postaux.parquet"
        if not cp_path.exists():
            logging.warning("Codes Postaux clean file not found. Skipping formations.")
            return
        
        cp_df = pd.read_parquet(cp_path)
        # cp_df has 'code_postal', 'codgeo'
        
        # 2. Formations Referentiel (XLSX)
        ref_cfg = config['sources']['formations_referentiel']
        ref_path = CACHE_DIR / ref_cfg['local_name']
        
        if ref_path.exists():
            # Read with header=None, skip first 2 rows (based on inspection)
            # Row 2 (index 2) has data "100.0 Formations générales"
            # So we can read from row 2 onwards.
            # Actually, read_excel with header=None gives index 0, 1...
            # We saw row 0, 1 are NaN. Row 2 has data.
            df_ref = pd.read_excel(ref_path, header=None, skiprows=2)
            # Columns 0: Code, 1: Label
            if len(df_ref.columns) >= 2:
                df_ref = df_ref.iloc[:, :2]
                df_ref.columns = ['code', 'label']
                df_ref['code'] = df_ref['code'].astype(str).str.replace('.0', '', regex=False)
                
                output_ref = CLEAN_DIR / "formations_referentiel.parquet"
                df_ref.to_parquet(output_ref)
                logger.log_step("clean_formations", "REFERENTIEL", {"path": str(output_ref)})
            else:
                 logging.warning("Formations Referentiel: Unexpected columns.")
        
        # 3. Formations Annuaire (CSV)
        annuaire_cfg = config['sources']['formations_annuaire']
        annuaire_path = CACHE_DIR / annuaire_cfg['local_name']
        
        if annuaire_path.exists():
            # Load CSV (semicolon likely)
            try:
                df_annuaire = pd.read_csv(annuaire_path, sep=';', on_bad_lines='skip')
            except:
                df_annuaire = pd.read_csv(annuaire_path, sep=',', on_bad_lines='skip')
            
            # Normalize columns
            df_annuaire.columns = [c.strip() for c in df_annuaire.columns]
            
            # Identify columns
            # We need 'code_postal' (to map to codgeo) and 'domaines_formation' (codes)
            # Let's look for them.
            cp_col = next((c for c in df_annuaire.columns if 'code_postal' in c.lower() or 'codepostal' in c.lower()), None)
            
            # For formation codes, we need to know the column name.
            # Based on typical data.gouv files, it might be 'domaines_formation' or 'code_domaine'.
            # If we don't know, we can't proceed.
            # But I'll assume 'domaines_formation' or similar based on user description "The formations annuaire which lists all the entities".
            # And "The formations referentiel which maps codes to labels".
            # So Annuaire must have the codes.
            # Let's try to find a column that looks like it contains codes (e.g. '100', '110').
            # Or 'liste_formations'.
            
            # I will log columns and try to guess.
            logging.info(f"Formations Annuaire Columns: {df_annuaire.columns.tolist()}")
            
            # Heuristic: find column with 'formation' or 'domaine'
            formation_col = next((c for c in df_annuaire.columns if 'domaine' in c.lower() or 'formation' in c.lower()), None)
            
            if cp_col and formation_col:
                df_annuaire['code_postal'] = df_annuaire[cp_col].astype(str).str.zfill(5)
            # Identify Postal Code Column
            # Raw: adressePhysiqueOrganismeFormation.codePostal
            cp_col = next((c for c in df_annuaire.columns if 'adressePhysiqueOrganismeFormation.codePostal' in c), None)
            if not cp_col:
                cp_col = next((c for c in df_annuaire.columns if 'code_postal' in c.lower() or 'codepostal' in c.lower()), None)
            
            # Identify Formation Code Columns
            # Raw: informationsDeclarees.specialitesDeFormation.codeSpecialite1, 2, 3
            formation_cols = [c for c in df_annuaire.columns if 'codeSpecialite' in c]
            
            if cp_col and formation_cols:
                # Melt to get one row per formation code
                df_melted = df_annuaire.melt(
                    id_vars=[cp_col], 
                    value_vars=formation_cols, 
                    value_name='formation_code'
                ).dropna(subset=['formation_code'])
                
                # Fix Postal Codes (handle float strings like "75011.0")
                df_melted['code_postal'] = pd.to_numeric(df_melted[cp_col], errors='coerce').fillna(0).astype(int).astype(str).str.zfill(5)
                
                # Merge with codes postaux to get codgeo
                merged = df_melted.merge(cp_df, on='code_postal', how='inner')
                
                merged['formation_code'] = merged['formation_code'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                
                # Filter out invalid codes (optional, maybe length check?)
                merged = merged[merged['formation_code'] != 'nan']
                
                df_out = merged[['codgeo', 'formation_code']].drop_duplicates()
                
                output_annuaire = CLEAN_DIR / "formations_annuaire.parquet"
                df_out.to_parquet(output_annuaire)
                logger.log_step("clean_formations", "ANNUAIRE", {"path": str(output_annuaire), "rows": len(df_out)})
            else:
                logging.warning(f"Formations Annuaire: Columns not found. CP: {cp_col}, Formations: {formation_cols}")

    except Exception as e:
        logger.log_step("clean_formations", "ERROR", {"error": str(e)})

if __name__ == "__main__":
    main()
