import argparse
import logging
import requests
from datetime import datetime
import pandas as pd
import geopandas as gpd
import numpy as np
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional
from google import genai
from google.cloud import bigquery

from pipeline.common import (
    PipelineLogger, load_config, load_dataset, extract_zip,
    CONFIG_FILE, CACHE_DIR, CLEAN_DIR, OUTPUT_DIR, STATUS_FILE,
    is_cache_valid, fetch_remote_metadata_datagouv, validate_dataset_contract,
    atomic_swap, get_ingest_paths, finalize_ingest
)
from pipeline.odace_client import get_odace_client
from pipeline.ft_live_ingest import run_etl, get_token as get_ft_token
from pipeline.emplois_inclusion_ingest import run_ingestion as run_inclusion_ingest



def fetch_source(name: str, source_cfg: Dict[str, Any], logger: PipelineLogger) -> Optional[Path]:
    """Downloads and prepares a single source with caching, metadata checks, and staging."""
    import os
    resource_id = source_cfg.get('datagouv_resource_id')
    url = source_cfg.get('url')
    if not url and resource_id:
        url = f"https://www.data.gouv.fr/api/1/datasets/r/{resource_id}"

    if not url:
        logger.log_source(name, "SKIPPED", "No URL provided")
        return None

    local_name = source_cfg['local_name']
    local_path = CACHE_DIR / local_name
    
    # Create cache dir
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Check if cache is still valid
    ttl_days = source_cfg.get('ttl_days', 30)
    if is_cache_valid(name, source_cfg):
        logging.info(f"[Fetch] {name}: Local cache is valid (TTL={ttl_days} days). Skipping fetch.")
        logger.log_source(name, "CACHED", local_path)
        if source_cfg.get('format') == 'zip' and 'archive_file' in source_cfg:
            extracted_path = CACHE_DIR / source_cfg['archive_file']
            return extracted_path
        return local_path

    # Cache is expired or missing. Check if we can do data.gouv.fr remote metadata validation.
    staging_local_path = CACHE_DIR / f"staging_{local_name}"
    download_url = url

    if local_path.exists() and resource_id:
        # We can query remote metadata to check if the remote resource is newer than our local cache.
        meta = fetch_remote_metadata_datagouv(resource_id)
        if meta and 'last_modified' in meta:
            try:
                # Remove timezone offset or make naive to compare
                remote_mtime = datetime.fromisoformat(meta['last_modified'].replace('Z', '+00:00')).replace(tzinfo=None)
                local_mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
                if remote_mtime <= local_mtime:
                    logging.info(f"[Fetch] {name} is up-to-date on data.gouv.fr (remote: {remote_mtime}, local: {local_mtime}). Skipping download and resetting TTL.")
                    # Touch local file to refresh its modification time (reset TTL window)
                    os.utime(local_path, None)
                    logger.log_source(name, "CACHED", local_path)
                    if source_cfg.get('format') == 'zip' and 'archive_file' in source_cfg:
                        extracted_path = CACHE_DIR / source_cfg['archive_file']
                        return extracted_path
                    return local_path
                else:
                    logging.info(f"[Fetch] {name}: Remote version is newer (remote: {remote_mtime}, local: {local_mtime}). Downloading updated data...")
                    if meta.get('url'):
                        download_url = meta['url']
            except Exception as e:
                logging.warning(f"⚠️ Error parsing metadata for {name}: {e}")

    # Fallback to reminder alert for other expired non-datagouv sources
    if local_path.exists() and not resource_id:
        mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        logging.info(f"🔔 [REMINDER] Cache for dataset '{name}' is {age_days} days old (TTL={ttl_days}). Please check manually if a new version is available on the provider's site.")

    # Download to staging path
    logging.info(f"[Fetch] {name}: Downloading to staging file from {download_url}...")
    try:
        if download_url.startswith("file://"):
            import shutil
            src_path = Path(download_url.replace("file://", ""))
            if src_path.exists():
                shutil.copy(src_path, staging_local_path)
                logger.log_source(name, "STAGING_COPIED", staging_local_path)
            else:
                raise FileNotFoundError(f"Source file not found: {src_path}")
        else:
            verify_ssl = source_cfg.get('verify_ssl', True)
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            }
            response = requests.get(download_url, stream=True, verify=verify_ssl, headers=headers)
            response.raise_for_status()
            with open(staging_local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.log_source(name, "STAGING_DOWNLOADED", staging_local_path)

        # Handle Zip Extraction in staging mode
        if source_cfg.get('format') == 'zip' and 'archive_file' in source_cfg:
            import zipfile
            extracted_file = source_cfg['archive_file']
            staging_extracted_path = CACHE_DIR / f"staging_{extracted_file}"
            logging.info(f"[Fetch] {name}: Extracting zip member '{extracted_file}' to staging path...")
            with zipfile.ZipFile(staging_local_path, 'r') as z:
                with open(staging_extracted_path, 'wb') as f_out:
                    f_out.write(z.read(extracted_file))
            return staging_extracted_path

        return staging_local_path
    except Exception as e:
        logging.error(f"[Fetch] {name} Failed: {e}")
        logger.log_source(name, "FAILED", str(e))
        # If download failed, but we have an active cached file, return the active file so we can fall back to it
        if local_path.exists():
            logging.warning(f"⚠️ Failed to download updated version of {name}. Falling back to cached copy.")
            if source_cfg.get('format') == 'zip' and 'archive_file' in source_cfg:
                return CACHE_DIR / source_cfg['archive_file']
            return local_path
        return None

def fetch_rome_referential(logger: PipelineLogger) -> Optional[Path]:
    """Fetches ROME referential from France Travail API with 1-year TTL."""
    local_path = CACHE_DIR / "rome_referential_api.parquet"
    
    # 1. 1-Year TTL Check
    if local_path.exists():
        mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        if age_days < 365:
            logging.info(f"[ROME] Referential is {age_days} days old. Using cache (TTL=1 year).")
            return local_path
        logging.info(f"[ROME] Referential is {age_days} days old. Refreshing...")
    
    # 2. Fetch from API
    try:
        token = get_ft_token()
        url = "https://api.francetravail.io/partenaire/offresdemploi/v2/referentiel/metiers"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        logging.info(f"📡 [ROME] Fetching referential from France Travail API...")
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # 3. Process and Save
        # Expected list of {code, libelle}
        df = pd.DataFrame(data)
        if 'code' in df.columns and 'libelle' in df.columns:
            df = df[['code', 'libelle']].rename(columns={'libelle': 'label'})
            df.to_parquet(local_path, engine='fastparquet')
            logging.info(f"✅ [ROME] Saved {len(df)} métiers to {local_path}")
            logger.log_source("rome_referential", "FETCHED", str(local_path))
            return local_path
        else:
            logging.error(f"[ROME] Unexpected data format: {df.columns}")
            return None
            
    except Exception as e:
        logging.error(f"❌ [ROME] Failed to fetch referential: {e}")
        logger.log_source("rome_referential", "ERROR", str(e))
        return None


def clean_population_active(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Population Active and saves to parquet."""
    logger.log_step("clean_population_active", "STARTED")
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
        actif_2022.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_population_active", "COMPLETED", {"path": str(output_path)})
    else:
            logging.warning("Population Active pivot failed.")

def clean_lovac(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans LOVAC and saves to parquet."""
    logger.log_step("clean_lovac", "STARTED")
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
            df_out.to_parquet(output_path, engine='fastparquet')
            logger.log_step("clean_lovac", "COMPLETED", {"rows": len(df_out)})
        else:
                logging.warning(f"LOVAC: Vacancy column {vac_col} not found.")

    else:
        logging.warning("LOVAC: CODGEO not found.")

def clean_rpls(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans RPLS and saves to parquet."""
    logger.log_step("clean_rpls", "STARTED")

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
        df_out.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_rpls", "COMPLETED", {"path": str(output_path)})

def clean_caf(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans CAF and saves to parquet."""
    logger.log_step("clean_caf", "STARTED")
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
            df_out.to_parquet(output_path, engine='fastparquet')
            logger.log_step("clean_caf", "COMPLETED", {"path": str(output_path)})

def clean_education(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Education and saves to parquet."""
    logger.log_step("clean_education", "STARTED")
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
    df['is_maternelle'] = (df['nature_uai_libe'].str.contains('MATERNELLE', case=False, na=False) | \
                           df['nature_uai_libe'].str.contains('PRIMAIRE', case=False, na=False)).astype(int)
    df['is_elementaire'] = (df['nature_uai_libe'].str.contains('ELEMENTAIRE', case=False, na=False) | \
                            df['nature_uai_libe'].str.contains('PRIMAIRE', case=False, na=False)).astype(int)
    df['is_college'] = (df['nature_uai_libe'] == 'COLLEGE').astype(int)
    df['is_lycee'] = (df['nature_uai_libe'].str.contains('LYCEE', case=False, na=False) | \
                      df['nature_uai_libe'].str.contains('SECTION D ENSEIGNEMENT PROFESSIONNEL', case=False, na=False)).astype(int)
    
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
    df_agg.to_parquet(output_path, engine='fastparquet')
    logger.log_step("clean_education", "COMPLETED", {"path": str(output_path)})
        
def clean_services_inclusion(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Inclusion (Services) and saves to parquet (one row per service)."""
    logger.log_step("clean_services_inclusion", "STARTED")
    source = config['sources']['services_inclusion']
    path = CACHE_DIR / source['local_name']
    if not path.exists(): return

    df = load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    # Filter required columns
    required_cols = ['id', 'nom', 'thematiques', 'latitude', 'longitude', 'code_insee']
    if not all(col in df.columns for col in required_cols):
         logging.warning(f"Services Inclusion: Missing columns. Found: {df.columns}")
         return

    # Filter rows with coordinates and thematiques
    df = df.dropna(subset=['latitude', 'longitude', 'thematiques'])

    # Parse 'thematiques' (stringified list or list)
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
                            raw_extracted = [val]
                else:
                    raw_extracted = [val]
            elif isinstance(val, list):
                raw_extracted = val
            elif hasattr(val, 'tolist'): # Handle numpy arrays
                raw_extracted = val.tolist()
            
            # Flatten
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
            # logging.warning(f"Error parsing thematiques: {val} -> {e}")
            return []

    df['thematique_list'] = df['thematiques'].apply(parse_thematiques)
    
    # Explode
    df_exploded = df.explode('thematique_list')
    df_exploded = df_exploded.dropna(subset=['thematique_list'])
    
    # Clean slug
    def clean_slug(val):
         val = str(val).strip()
         if val.startswith("['") and val.endswith("']"):
             return val[2:-2]
         return val
    
    df_exploded['service_slug'] = df_exploded['thematique_list'].apply(clean_slug)

    # Select and Rename
    df_out = df_exploded[[
        'id', 'nom', 'service_slug', 'latitude', 'longitude', 'code_insee'
    ]].rename(columns={
        'id': 'id_structure',
        'code_insee': 'codgeo'
    })
    
    # Robust codgeo cleaning
    # 1. Coerce to numeric (handles 'None', '', 'nan' -> NaN)
    df_out['codgeo_numeric'] = pd.to_numeric(df_out['codgeo'], errors='coerce')
    # 2. Drop invalid
    df_out = df_out.dropna(subset=['codgeo_numeric'])
    # 3. Convert to int then str then zfill
    df_out['codgeo'] = df_out['codgeo_numeric'].astype(int).astype(str).str.zfill(5)
    
    df_out = df_out.drop(columns=['codgeo_numeric'])

    output_path = CLEAN_DIR / "services_inclusion.parquet"
    df_out.to_parquet(output_path, engine='fastparquet')
    logger.log_step("clean_services_inclusion", "COMPLETED", {"path": str(output_path), "rows": len(df_out)})

def clean_structures_inclusion(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Inclusion (Structures) and saves to parquet (one row per structure)."""
    logger.log_step("clean_structures_inclusion", "STARTED")
    source = config.get('local_files', {}).get('structures_inclusion') # Try local_files first if configured there for some reason
    if not source:
        source = config['sources']['structures_inclusion']
    
    path = CACHE_DIR / source['local_name']
    if not path.exists(): return

    df = load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    # Required columns
    # We need: reseaux_porteurs, code_insee, id, nom, courriel, telephone, site_web, adresse
    # Check if 'reseaux_porteurs' exists
    if 'reseaux_porteurs' not in df.columns:
        logging.warning("clean_structures_inclusion: 'reseaux_porteurs' column missing.")
        return

    # Parse reseaux_porteurs
    def parse_reseaux(val):
        try:
            # Handle numpy array directly
            if hasattr(val, 'tolist'):
                val = val.tolist()

            if pd.isna(val): return []
            if isinstance(val, list): return val
            if isinstance(val, str):
                import ast
                val = val.strip()
                if val.startswith('[') and val.endswith(']'):
                    try:
                         # Handle "['a', 'b']"
                         return ast.literal_eval(val)
                    except:
                         pass
                return [val] # Fallback for single string
            return []
        except:
             return []

    df['reseaux_parsed'] = df['reseaux_porteurs'].apply(parse_reseaux)

    # Filter: contains "ccas" or "cias" (case insensitive)
    def has_ccas(lst):
        if not lst: return False
        for item in lst:
            if isinstance(item, str):
                s = item.lower()
                if 'ccas' in s or 'cias' in s:
                    return True
        return False

    df_filtered = df[df['reseaux_parsed'].apply(has_ccas)].copy()
    
    # Filter: Telephone OR Courriel must exist
    # Normalize empty strings to NaN/None for easier check
    if 'telephone' in df_filtered.columns:
        df_filtered['telephone'] = df_filtered['telephone'].replace('', np.nan)
    else:
        df_filtered['telephone'] = np.nan
        
    if 'courriel' in df_filtered.columns:
        df_filtered['courriel'] = df_filtered['courriel'].replace('', np.nan)
    else:
        df_filtered['courriel'] = np.nan

    count_before_contact_filter = len(df_filtered)
    df_filtered = df_filtered.dropna(subset=['telephone', 'courriel'], how='all')
    logging.info(f"filtered structures: CCAS match={count_before_contact_filter}, +Contact match={len(df_filtered)}")

    if df_filtered.empty:
        logging.warning("clean_structures_inclusion: No structures found after filtering.")
        return

    # Normalize codgeo
    if 'code_insee' in df_filtered.columns:
         df_filtered['codgeo_numeric'] = pd.to_numeric(df_filtered['code_insee'], errors='coerce')
         df_filtered = df_filtered.dropna(subset=['codgeo_numeric'])
         df_filtered['codgeo'] = df_filtered['codgeo_numeric'].astype(int).astype(str).str.zfill(5)
    else:
         logging.warning("clean_structures_inclusion: 'code_insee' missing.")
         return

    # Select columns
    cols_to_keep = ['id', 'nom', 'codgeo', 'courriel', 'telephone', 'site_web', 'adresse', 'commune']
    # Ensure they exist
    existing_cols = [c for c in cols_to_keep if c in df_filtered.columns]
    
    df_out = df_filtered[existing_cols]

    output_path = CLEAN_DIR / "structures_inclusion.parquet"
    df_out.to_parquet(output_path, engine='fastparquet')
    logger.log_step("clean_structures_inclusion", "COMPLETED", {"path": str(output_path), "rows": len(df_out)})
        
def clean_associations(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Associations and saves to parquet."""
    logger.log_step("clean_associations", "STARTED")
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
        
        df['id_waldec'] = df['id_waldec'].astype(str).str.zfill(6)
        df['codgeo'] = df['codgeo'].astype(str).str.zfill(5)
        
        # Save detailed associations for vertical table
        # We keep all associations to allow dynamic filtering in the app (Core vs Affinities)
        # We aggregate by codgeo and id_waldec to save space and provide a count
        df_out = df.groupby(['codgeo', 'id_waldec']).size().rename('count').reset_index()
        
        output_path = CLEAN_DIR / "associations_vertical.parquet"
        df_out.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_associations", "COMPLETED", {"path": str(output_path)})

def clean_refugee_associations(config: Dict[str, Any], logger: PipelineLogger):
    """Filters RNA for refugee associations and augments data."""
    logger.log_step("clean_refugee_associations", "STARTED")
    source = config['sources']['associations']
    path = CACHE_DIR / source['local_name']
    if not path.exists():
        logging.warning("RNA file not found.")
        return

    # Updated to fetch from BigQuery using is_refugee_focused flag (Harmonization)
    try:
        client = bigquery.Client()
        query = """
        SELECT 
            id,
            codgeo,
            titre_court as name,
            description,
            primary_category as waldec_code -- Using primary_category as it contains more useful semantic grouping
        FROM `odis-stream2.rna_rag.rna_rag`
        WHERE is_refugee_focused = True
        """
        logging.info("📡 [RNA RAG] Fetching detailed refugee associations from BigQuery...")
        df_refug = client.query(query).to_dataframe()
    except Exception as e:
        logging.error(f"Failed to fetch refugee associations from BQ: {e}")
        return

    if df_refug.empty:
        logging.warning("No refugee associations found in BigQuery.")
        return

    # Normalize codgeo
    df_refug['codgeo'] = df_refug['codgeo'].astype(str).str.zfill(5)

    # Add Bassin de Vie mapping (INSEE Source)
    mapping_source = config['sources']['bassins_de_vie']
    mapping_path = CACHE_DIR / mapping_source['archive_file']
    if mapping_path.exists():
        df_mapping = load_dataset(mapping_path, mapping_source) # Already handles sheet_name/header from yaml
        # Rename columns to match odis_communes standard
        df_mapping = df_mapping.rename(columns={
            'Code géographique': 'codgeo', 
            'Bassin de vie 2022': 'bassin_de_vie'
        })
        if 'codgeo' in df_mapping.columns and 'bassin_de_vie' in df_mapping.columns:
            df_mapping['codgeo'] = df_mapping['codgeo'].astype(str).str.zfill(5)
            # Ensure bassin_de_vie is string and not float-string "12345.0"
            df_mapping['bassin_de_vie'] = df_mapping['bassin_de_vie'].astype(str).str.replace(r'\.0$', '', regex=True)
            df_refug = df_refug.merge(df_mapping[['codgeo', 'bassin_de_vie']], on='codgeo', how='left')

    # Save detailed associations for list display
    # Keep: id, codgeo, bassin_de_vie, name, description, waldec_code
    useful_cols = ['id', 'codgeo', 'bassin_de_vie', 'name', 'description', 'waldec_code']
    df_out = df_refug[[c for c in useful_cols if c in df_refug.columns]].copy()
    
    output_path = CLEAN_DIR / "refugee_associations.parquet"
    df_out.to_parquet(output_path, engine='fastparquet')
    logger.log_step("clean_refugee_associations", "COMPLETED", {"path": str(output_path), "rows": len(df_out)})

def clean_population(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Population and saves to parquet."""
    logger.log_step("clean_population", "STARTED")
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
        df.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_population", "COMPLETED", {"path": str(output_path)})

def clean_communes(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Communes and saves to parquet."""
    logger.log_step("clean_communes", "STARTED")
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
        if 'geometry' in gdf.columns:
            gdf['polygon'] = gdf.geometry.to_wkb()
            gdf.drop(columns=['geometry'], inplace=True)
        pd.DataFrame(gdf).to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_communes", "COMPLETED", {"path": str(output_path)})
    # except Exception as e:
    #     logger.log_step("clean_communes", "ERROR", {"error": str(e)})

def clean_political(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Political Nuance and saves to parquet."""
    logger.log_step("clean_political", "STARTED")
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
        df_out.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_political", "COMPLETED", {"path": str(output_path)})
    else:
        logging.warning(f"Political: Columns not found. Found: {df.columns}")

def clean_housing_occupation(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Housing Occupation and saves to parquet."""
    logger.log_step("clean_housing_occupation", "STARTED")
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
        df_pivot.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_housing_occupation", "COMPLETED", {"path": str(output_path)})

def clean_school_effectifs(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans School Effectifs and saves to parquet."""
    logger.log_step("clean_school_effectifs", "STARTED")
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
    
    
    # Filter for Latest Year
    if 'rentree_scolaire' in df.columns:
        # Normalize to datetime or string if needed, or just compare
        # Assuming format is comparable or datetime
        latest_year = df['rentree_scolaire'].max()
        logging.info(f"Education Effectifs: Using latest year {latest_year}")
        df = df[df['rentree_scolaire'] == latest_year].copy()
    else:
        logging.warning("Education Effectifs: 'rentree_scolaire' column missing. Using full dataset (risk of duplication).")
    
    # 2. Education Annuaire (Reference for UAI -> Commune)
    annuaire_cfg = config['sources']['education_annuaire']
    annuaire_path = CACHE_DIR / annuaire_cfg['local_name']
    
    if not annuaire_path.exists():
         logging.warning("Education Annuaire not found. Cannot map effectifs.")
         return

    df_annuaire = pd.read_parquet(annuaire_path, engine='fastparquet')
    
    # Check columns
    # We expect 'numero_uai' and 'code_commune' (or similar)
    uai_col = next((c for c in df_annuaire.columns if 'numero_uai' in c or 'identifiant_de_l_etablissement' in c), None)
    insee_col = next((c for c in df_annuaire.columns if 'code_commune' in c), None)

    if not uai_col or not insee_col:
        logging.warning(f"Education Annuaire: Missing UAI ({uai_col}) or INSEE ({insee_col}) columns.")
        return
        
    # Prepare Annuaire for link
    # Drop duplicates on UAI just in case
    df_ref = df_annuaire[[uai_col, insee_col]].drop_duplicates(subset=[uai_col]).rename(columns={uai_col: 'numero_ecole', insee_col: 'codgeo'})
    df_ref['codgeo'] = df_ref['codgeo'].astype(str).str.zfill(5)

    # 3. Merge Effectifs -> Annuaire (on UAI)
    merged = df.merge(df_ref, on='numero_ecole', how='left')
    
    mapped_count = merged['codgeo'].notna().sum()
    logging.info(f"Education Mapping (UAI): {mapped_count} / {len(merged)} ({mapped_count/len(merged):.1%}) mapped.")
    
    if len(merged) - mapped_count > 0:
         logging.warning(f"Education Effectifs: {len(merged) - mapped_count} rows failed to map via UAI.")
         # Optional: Fallback to old method? 
         # User said "Mapping on the address is ugly", so we stick to UAI or fail/warn.
    
    valid = merged.dropna(subset=['codgeo']).copy()
    
    # 4. Compute Metrics
    effectif_col = 'nombre_total_eleves'
    classes_col = 'nombre_total_classes'
    
    if effectif_col not in valid.columns or classes_col not in valid.columns:
        logging.warning("Missing effectifs/classes columns.")
        return

    # Calculate students per class
    # Avoid division by zero
    valid['students_per_class'] = np.where(valid[classes_col] > 0, valid[effectif_col] / valid[classes_col], 0.0)
    
    # Risk Metric: Threshold < 20 (User confirmed)
    # We want "Likely to close" -> Low number of students per class.
    # Score logic: Higher count of risky schools -> Worse score.
    THRESHOLD = 20
    valid['is_risky'] = (valid['students_per_class'] < THRESHOLD).astype(int)
    
    # Group by Commune
    df_agg = valid.groupby('codgeo').agg({
        effectif_col: 'sum',
        classes_col: 'sum',
        'is_risky': 'sum',
        'numero_ecole': 'nunique'
    }).reset_index()
    
    df_agg.rename(columns={
        effectif_col: 'total_eleves',
        classes_col: 'total_classes',
        'is_risky': 'risky_schools_count',
        'numero_ecole': 'ecoles_count'
    }, inplace=True)
    
    # Also calculate average students per class for the whole commune (optional context)
    df_agg['avg_students_per_class_commune'] = np.where(
        df_agg['total_classes'] > 0,
        df_agg['total_eleves'] / df_agg['total_classes'],
        0.0
    )
    
    output_path = CLEAN_DIR / "school_effectifs.parquet"
    df_agg.to_parquet(output_path, engine='fastparquet')
    logger.log_step("clean_school_effectifs", "COMPLETED", {"path": str(output_path), "rows": len(df_agg)})

def clean_bpe(config: Dict[str, Any], logger: PipelineLogger):
    """Extracts points of interest and aggregated counts from BPE."""
    output_heb_cols = CLEAN_DIR / "bpe_hebergement_cols.parquet"
    output_creches_cols = CLEAN_DIR / "bpe_petite_enfance_cols.parquet"
    output_pois = CLEAN_DIR / "bpe_pois.parquet"

    # 1-Year TTL Check (as requested by user)
    needs_refresh = True
    if output_heb_cols.exists() and output_creches_cols.exists() and output_pois.exists():
        mtime = datetime.fromtimestamp(output_heb_cols.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        if age_days < 365:
            logging.info(f"[BPE] BPE stats (hebergement) are {age_days} days old. Using cache (TTL=1 year).")
            needs_refresh = False

    if not needs_refresh:
        return

    logger.log_step("clean_bpe", "STARTED")
    source = config['sources']['bpe']
    path = CACHE_DIR / source['local_name']
    if not path.exists(): return

    df = load_dataset(path, source)
    
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

    # --- 1. Petite Enfance (Creches) ---
    df_creches = df[df['TYPEQU'] == 'D502'].copy()
    df_creches['type_libelle'] = 'Creche'
    creches_counts = df_creches.groupby('codgeo').size().rename('bpe_creches_count').reset_index()
    
    output_creches_cols = CLEAN_DIR / "bpe_petite_enfance_cols.parquet"
    creches_counts.to_parquet(output_creches_cols, engine='fastparquet')

    # --- 2. Hebergement (CHRS, CPH, FJT, Pensions) ---
    # CHRS (D703), CPH (D704) -> sum(CAPACITE)
    df_centres = df[df['TYPEQU'].isin(['D703', 'D704'])].copy()
    df_centres['type_libelle'] = df_centres['TYPEQU'].map({'D703': 'CHRS', 'D704': 'CPH'})
    df_centres['CAPACITE'] = pd.to_numeric(df_centres['CAPACITE'], errors='coerce').fillna(0)
    centres_agg = df_centres.groupby('codgeo')['CAPACITE'].sum().rename('heb_centres_heb_cap').reset_index()

    # FJT & Pensions & Migrants (D710 + name filter) -> Count
    # Keyword 'pension' for Pensions de famille as per user
    mask_fjt = (df['TYPEQU'] == 'D710') & (
        df['NOMRS'].str.contains('fjt|foyer jeunes travailleurs|pension|migrant', case=False, na=False, regex=True)
    )
    df_foyers = df[mask_fjt].copy()
    df_foyers['type_libelle'] = 'Foyer/Pension'
    foyers_agg = df_foyers.groupby('codgeo').size().rename('heb_foyers_count').reset_index()

    heb_metrics = centres_agg.merge(foyers_agg, on='codgeo', how='outer').fillna(0)
    output_heb_cols = CLEAN_DIR / "bpe_hebergement_cols.parquet"
    heb_metrics.to_parquet(output_heb_cols, engine='fastparquet')

    # --- 3. POIs Output ---
    # Combine all for POIs
    df_all_pois = pd.concat([df_creches, df_centres, df_foyers])
    
    if 'LAMBERT_X' in df_all_pois.columns and 'LAMBERT_Y' in df_all_pois.columns:
            gdf = gpd.GeoDataFrame(
                df_all_pois, 
                geometry=gpd.points_from_xy(df_all_pois.LAMBERT_X, df_all_pois.LAMBERT_Y),
                crs="EPSG:2154"
            ).to_crs("EPSG:4326")
            
            pois = pd.DataFrame({
                'id': gdf.index.astype(str),
                'name': gdf['NOMRS'].astype(str),
                'type': gdf['type_libelle'],
                'category': gdf['TYPEQU'].apply(lambda x: 'education' if x == 'D502' else 'hebergement'),
                'lat': gdf.geometry.y,
                'lon': gdf.geometry.x,
                'codgeo': gdf['codgeo']
            })
            
            output_pois = CLEAN_DIR / "bpe_pois.parquet"
            pois.to_parquet(output_pois, engine='fastparquet')
            logger.log_step("clean_bpe", "COMPLETED", {
                "creches_cols": str(output_creches_cols), 
                "heb_cols": str(output_heb_cols),
                "pois": str(output_pois)
            })
    else:
            logging.warning("BPE: LAMBERT coordinates not found.")
            logger.log_step("clean_bpe", "PARTIAL", {"counts": str(output_creches_cols)})

def compute_rna_rag_counts(query_text: str, threshold: float = 0.65) -> pd.DataFrame:
    """Computes semantic counts for a query using BigQuery Vector Search (ML.DISTANCE)."""
    client = bigquery.Client(project="odis-stream2")
    genai_client = genai.Client(vertexai=True, project="odis-stream2", location="europe-west1")
    
    # Generate Embedding
    response = genai_client.models.embed_content(
        model="text-multilingual-embedding-002",
        contents=[query_text],
        config={'output_dimensionality': 128}
    )
    query_vector = response.embeddings[0].values

    # Query using ML.DISTANCE (Cosine distance = 1 - cosine similarity)
    # Threshold 0.8 similarity => 0.2 distance
    distance_threshold = 1.0 - threshold
    
    sql = """
    SELECT 
        codgeo,
        COUNT(*) as count
    FROM `odis-stream2.rna_rag.rna_rag`
    WHERE is_inclusion_relevant = True
    AND ML.DISTANCE(ARRAY(SELECT element FROM UNNEST(embedding_128.list)), @query_vec, 'COSINE') < @dist_threshold
    GROUP BY 1
    """
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("query_vec", "FLOAT64", query_vector),
            bigquery.ScalarQueryParameter("dist_threshold", "FLOAT64", distance_threshold),
        ]
    )
    
    logging.info(f"📡 [RNA RAG] Computing semantic counts for: '{query_text}'")
    df = client.query(sql, job_config=job_config).to_dataframe()
    return df

def clean_hebergement_rna(config: Dict[str, Any], logger: PipelineLogger):
    """Extracts accommodation-related associations from RNA using RAG (IML & Citoyen)."""
    output_agg = CLEAN_DIR / "hebergement_rna_cols.parquet"
    
    # 1. 1-Year TTL Check (as requested by user)
    if output_agg.exists():
        mtime = datetime.fromtimestamp(output_agg.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        if age_days < 365:
            logging.info(f"[RNA RAG] Hebergement RNA stats are {age_days} days old. Using cache (TTL=1 year).")
            return
            
    logger.log_step("clean_hebergement_rna", "STARTED")

    try:
        # A. IML Counts
        df_iml = compute_rna_rag_counts("Bail solidaire et Intermediation Locative (IML)")
        df_iml = df_iml.rename(columns={'count': 'heb_loc_iml_count'})

        # B. Citoyen Counts
        df_cit = compute_rna_rag_counts("hébergement citoyen chez l'habitant")
        df_cit = df_cit.rename(columns={'count': 'heb_habitant_count'})

        # Merge and finalize
        agg = df_iml.merge(df_cit, on='codgeo', how='outer').fillna(0)
        agg['codgeo'] = agg['codgeo'].astype(str).str.zfill(5)
        
        agg.to_parquet(output_agg, engine='fastparquet')
        logger.log_step("clean_hebergement_rna", "COMPLETED", {"path": str(output_agg), "rows": len(agg)})
        
    except Exception as e:
        logging.error(f"❌ [RNA RAG] Pivot failed: {e}")
        logger.log_step("clean_hebergement_rna", "ERROR", {"error": str(e)})


def clean_loyers(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Loyers data (Appartements) and saves to parquet."""
    logger.log_step("clean_loyers", "STARTED")
    source = config['sources']['loyers_apparts']
    path = CACHE_DIR / source['local_name']
    
    if not path.exists():
        return

    # Load with correct options
    sep = source.get('sep', ';')
    encoding = source.get('encoding', 'utf-8')
    
    df = pd.read_csv(path, sep=sep, encoding=encoding, dtype={'INSEE_C': str})
    
    # Expected columns: INSEE_C (code commune), loypredm2 (loyer moyen m2)
    if 'INSEE_C' in df.columns:
        df.rename(columns={'INSEE_C': 'codgeo'}, inplace=True)
    
    if 'codgeo' not in df.columns:
            # Try to find a code column
            codgeo_col = next((c for c in df.columns if 'INSEE' in c or 'COD' in c), None)
            if codgeo_col:
                df.rename(columns={codgeo_col: 'codgeo'}, inplace=True)
                
    if 'codgeo' not in df.columns:
            logging.warning(f"Loyers: CODGEO not found. Found: {df.columns}")
            return

    df['codgeo'] = df['codgeo'].astype(str).str.zfill(5)
    
    # Loyer column
    val_col = 'loypredm2'
    if val_col not in df.columns:
            logging.warning(f"Loyers: {val_col} not found. Found: {df.columns}")
            return
            
    # Extract and clean
    df[val_col] = pd.to_numeric(df[val_col].astype(str).str.replace(',', '.'), errors='coerce')
    
    df_out = df[['codgeo', val_col]].rename(columns={val_col: 'loyer_app_m2'})
    df_out = df_out.groupby('codgeo')['loyer_app_m2'].mean().reset_index()
    
    output_path = CLEAN_DIR / "loyers.parquet"
    df_out.to_parquet(output_path, engine='fastparquet')
    logger.log_step("clean_loyers", "COMPLETED", {"path": str(output_path)})



def clean_population_details(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Population Details (Age Breakdown) and saves to parquet."""
    logger.log_step("clean_population_details", "STARTED")
    source = config['sources']['population_details']
    path = CACHE_DIR / source['archive_file']
    
    if not path.exists():
        logging.warning("Population Details file not found.")
        return

    # Load CSV (Long Format)
    # "AGE";"GEO";"GEO_OBJECT";"RP_MEASURE";"SEX";"TIME_PERIOD";"OBS_VALUE"
    df = pd.read_csv(path, sep=';', low_memory=False)
    
    # Filter Checks
    required_cols = ['AGE', 'GEO', 'GEO_OBJECT', 'SEX', 'TIME_PERIOD', 'OBS_VALUE']
    if not all(col in df.columns for col in required_cols):
        logging.warning(f"Population Details: Missing columns. Found: {df.columns}")
        return

    # Filter Rows
    # GEO_OBJECT == 'COM'
    # SEX == '_T' (Total)
    df = df[
        (df['GEO_OBJECT'] == 'COM') & 
        (df['SEX'] == '_T')
    ]
    
    # We need Age Groups:
    # Youth: < 15 -> 'Y_LT15'
    # Active: 25-54 -> 'Y25T39' + 'Y40T54'
    
    target_ages = ['Y_LT15', 'Y25T39', 'Y40T54']
    df = df[df['AGE'].isin(target_ages)]
    
    # Normalize GEO -> codgeo
    df.rename(columns={'GEO': 'codgeo'}, inplace=True)
    df['codgeo'] = df['codgeo'].astype(str).str.zfill(5)
    
    # Normalize Year
    df['year'] = df['TIME_PERIOD'].astype(str)
    
    # Value to numeric
    df['count'] = pd.to_numeric(df['OBS_VALUE'], errors='coerce').fillna(0)
    
    # Aggregate Active Group
    # Map ages to broad categories
    age_mapping = {
        'Y_LT15': 'jeune',
        'Y25T39': 'active',
        'Y40T54': 'active'
    }
    df['age_group'] = df['AGE'].map(age_mapping)
    
    # Pivot
    # Index: codgeo
    # Columns: {age_group}_{year}
    # Values: Sum of count
    
    df_pivot = df.pivot_table(
        index='codgeo',
        columns=['age_group', 'year'],
        values='count',
        aggfunc='sum'
    )
    
    # Flatten Columns
    # e.g. active_2016, active_2022
    df_pivot.columns = [f"pop_{c[0]}_{c[1]}" for c in df_pivot.columns]
    df_pivot.reset_index(inplace=True)
    
    # Ensure expected columns exist (fill 0 if checking years 2016/2022)
    expected_cols = ['pop_jeune_2016', 'pop_jeune_2022', 'pop_active_2016', 'pop_active_2022']
    for col in expected_cols:
        if col not in df_pivot.columns:
            logging.warning(f"Population Details: Missing expected column {col}. Setting to 0.")
            df_pivot[col] = 0.0

    output_path = CLEAN_DIR / "population_details.parquet"
    df_pivot.to_parquet(output_path, engine='fastparquet')
    logger.log_step("clean_population_details", "COMPLETED", {"path": str(output_path), "rows": len(df_pivot)})

def clean_nomenclature_waldec(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans WALDEC Nomenclature and saves to parquet."""
    logger.log_step("clean_nomenclature_waldec", "STARTED")
    source = config['sources']['nomenclature_waldec']
    path = CACHE_DIR / source['local_name']
    
    if not path.exists():
         # Ingest typically fetches, but if we run step manually loop might needed.
         # The 'Fetch' phase runs before.
         logging.warning("WALDEC Nomenclature file not found.")
         return

    # Load JSON
    # Structure based on URL: List of objects or Dict?
    # Usually: [{"code": "...", "label": "..."}] or similar.
    # Let's assume standard extraction or inspect first?
    # User said "Type de fichier: json".
    # I'll implement a robust loader.
    
    try:
        df = pd.read_json(path)
        # Expected columns? 'Code', 'Libellé'?
        # If deeply nested, might need normalization.
        # Let's assume flat or simple list given Data Gouv standard.
        
        # Standardize columns
        df.columns = [c.strip().lower() for c in df.columns]
        
        # Identify Code and Label
        # Found: objet_social_id, objet_social_lib
        code_col = next((c for c in df.columns if c in ['code', 'id', 'id_waldec', 'objet_social_id']), None)
        label_col = next((c for c in df.columns if c in ['libelle', 'label', 'titre', 'lib', 'objet_social_lib']), None)
        
        if code_col and label_col:
            df_out = df[[code_col, label_col]].rename(columns={code_col: 'code', label_col: 'label'})
            # Ensure strings and zero-padding (6 digits)
            df_out['code'] = df_out['code'].astype(str).str.zfill(6)
            df_out['label'] = df_out['label'].astype(str)
            
            output_path = CLEAN_DIR / "referentiel_waldec.parquet"
            df_out.to_parquet(output_path, engine='fastparquet')
            logger.log_step("clean_nomenclature_waldec", "COMPLETED", {"path": str(output_path), "rows": len(df_out)})
        else:
            logging.warning(f"WALDEC: Could not identify code/label columns. Found: {df.columns}")
            logger.log_step("clean_nomenclature_waldec", "FAILED", {"reason": "Columns not found"})

    except Exception as e:
        logger.log_step("clean_nomenclature_waldec", "ERROR", {"error": str(e)})
        logging.error(f"WALDEC clean failed: {e}")

def clean_inclusion_jobs(config: Dict[str, Any], logger: PipelineLogger, skip: bool = False):
    """Fetches job openings from Les emplois de l'inclusion."""
    logger.log_step("clean_inclusion_jobs", "STARTED")
    
    status = get_inclusion_jobs_status()
    should_run = not skip
    
    if should_run:
        if status["within_ttl"]:
            logging.info(f"Inclusion Jobs: Data is {status['age_days']:.1f} days old (TTL={status['ttl_days']}). Skipping fetch.")
            should_run = False
        elif not status["exists"]:
            logging.info("Inclusion Jobs: No existing data found.")
        else:
            logging.info(f"Inclusion Jobs: Data is {status['age_days']:.1f} days old (TTL expired).")

    if should_run:
        logging.info("Inclusion Jobs: Running Les emplois de l'inclusion ingest...")
        run_inclusion_ingest()
        logger.log_step("clean_inclusion_jobs", "COMPLETED")
    else:
        logger.log_step("clean_inclusion_jobs", "SKIPPED")

def get_live_jobs_status() -> Dict[str, Any]:
    """Checks the age of Live Jobs data in cache and deployed data."""
    cache_path = OUTPUT_DIR / "odis_ft_jobs_agg.parquet"
    data_path = Path("data/odis_ft_jobs_agg.parquet")
    
    # Dynamic TTL check
    try:
        config = load_config(CONFIG_FILE)
        ttl_days = config.get('local_files', {}).get('france_travail_live', {}).get('ttl_days', 7)
    except:
        ttl_days = 7
        
    files = [cache_path, data_path]
    mtimes = []
    for f in files:
        if f.exists():
            mtimes.append(f.stat().st_mtime)
    
    if not mtimes:
        return {"age_days": None, "within_ttl": False, "exists": False, "ttl_days": ttl_days}
    
    newest_mtime = max(mtimes)
    age_days = (time.time() - newest_mtime) / (24 * 3600)
    
    return {
        "age_days": age_days,
        "within_ttl": age_days < ttl_days,
        "exists": True,
        "ttl_days": ttl_days
    }

def get_inclusion_jobs_status() -> Dict[str, Any]:
    """Checks the age of Inclusion Jobs data in cache and deployed data."""
    cache_path = OUTPUT_DIR / "odis_inclusion_jobs.parquet"
    data_path = Path("data/odis_inclusion_jobs.parquet")
    
    # Dynamic TTL check
    try:
        config = load_config(CONFIG_FILE)
        ttl_days = config.get('local_files', {}).get('inclusion_jobs', {}).get('ttl_days', 7)
    except:
        ttl_days = 7
        
    files = [cache_path, data_path]
    mtimes = []
    for f in files:
        if f.exists():
            mtimes.append(f.stat().st_mtime)
    
    if not mtimes:
        return {"age_days": None, "within_ttl": False, "exists": False, "ttl_days": ttl_days}
    
    newest_mtime = max(mtimes)
    age_days = (time.time() - newest_mtime) / (24 * 3600)
    
    return {
        "age_days": age_days,
        "within_ttl": age_days < ttl_days,
        "exists": True,
        "ttl_days": ttl_days
    }

def clean_live_jobs(config: Dict[str, Any], logger: PipelineLogger, skip: bool = False):
    """Fetches and aggregates Live Job offers from France Travail."""
    logger.log_step("clean_live_jobs", "STARTED")
    
    status = get_live_jobs_status()
    should_run = not skip
    
    if should_run:
        if status["within_ttl"]:
            logging.info(f"Live Jobs: Data is {status['age_days']:.1f} days old (TTL={status['ttl_days']}). Skipping fetch.")
            should_run = False
        elif not status["exists"]:
            logging.info("Live Jobs: No existing data found.")
        else:
            logging.info(f"Live Jobs: Data is {status['age_days']:.1f} days old (TTL expired).")

    if should_run:
        logging.info("Live Jobs: Running France Travail ingest...")
        path = run_etl()
        if path:
            logger.log_step("clean_live_jobs", "COMPLETED", {"path": path})
        else:
            logger.log_step("clean_live_jobs", "FAILED")
    else:
        logger.log_step("clean_live_jobs", "SKIPPED")

class MutedPipelineLogger:
    """Wrapper to prevent duplicate log_step calls from inside individual clean_* cleaners."""
    def __init__(self, real_logger: PipelineLogger):
        self.real_logger = real_logger
        self.status = getattr(real_logger, 'status', None)

    def log_step(self, step_name: str, status: str, details: Optional[Dict[str, Any]] = None):
        pass

    def log_source(self, source_name: str, status: str, file_path: Optional[str] = None):
        self.real_logger.log_source(source_name, status, file_path)


def run_clean_step_safely(step_name: str, clean_func, config: Dict[str, Any], logger: PipelineLogger, *args, **kwargs):
    """
    Executes a step cleaning function under the Blue-Green staging-and-restore pattern.
    Checks if any staging file exists (e.g. staging_{local_name} or staging_{archive_file}).
    If they exist:
      1. Validates the RAW staging dataset against config-defined schema contracts.
      2. Backs up active raw, extracted, and clean files (renaming them to .active_bak).
      3. Renames staging files to their active paths.
      4. Executes the original clean function.
      5. Validates that the resulting clean parquet file is non-empty.
      6. Commits (deletes backups) on success, or rolls back (restores backups) on failure/exception.
    """
    import os
    from pathlib import Path
    
    source_cfg = config['sources'].get(step_name) or config.get('local_files', {}).get(step_name)
    if not source_cfg:
        # Fallback if no configuration is defined for this step name
        logger.log_step(f"clean_{step_name}", "STARTED")
        try:
            clean_func(config, logger, *args, **kwargs)
            logger.log_step(f"clean_{step_name}", "COMPLETED")
        except Exception as e:
            logger.log_step(f"clean_{step_name}", "ERROR", {"error": str(e)})
            logging.exception(f"Error running step clean_{step_name}")
            raise e
        return

    logger.log_step(f"clean_{step_name}", "STARTED")
    muted_logger = MutedPipelineLogger(logger)

    local_name = source_cfg.get('local_name')
    path_str = source_cfg.get('path')
    
    if path_str:
        active_raw = Path(path_str)
        staging_raw = active_raw.parent / f"staging_{active_raw.name}"
    elif local_name:
        active_raw = CACHE_DIR / local_name
        staging_raw = CACHE_DIR / f"staging_{local_name}"
    else:
        active_raw = None
        staging_raw = None

    # Check zip extracted path if archive_file exists
    archive_file = source_cfg.get('archive_file')
    if archive_file:
        active_ext = CACHE_DIR / archive_file
        staging_ext = CACHE_DIR / f"staging_{archive_file}"
    else:
        active_ext = None
        staging_ext = None

    # Dynamic Clean Filename Resolver
    clean_filenames = {
        'associations': 'associations_vertical.parquet',
        'nomenclature_waldec': 'referentiel_waldec.parquet',
        'hebergement_rna': 'hebergement_rna_cols.parquet',
        'jaccueille': 'jaccueille_bdv.parquet',
        'bpe': 'bpe_pois.parquet',
        'odace_rent': 'odace_loyer_annonce.parquet',
    }
    clean_filename = clean_filenames.get(step_name, f"{step_name}.parquet")
    active_clean = CLEAN_DIR / clean_filename

    # Determine if we are in staging mode
    is_staging = False
    if staging_raw and staging_raw.exists():
        is_staging = True
    if staging_ext and staging_ext.exists():
        is_staging = True

    if not is_staging:
        # No staging files exist, run the clean function directly
        try:
            clean_func(config, muted_logger, *args, **kwargs)
            
            # Validate that the active clean parquet is non-empty for basic sanity
            details = {}
            if active_clean.exists():
                try:
                    df_clean = pd.read_parquet(active_clean, engine='fastparquet')
                    details = {"rows": len(df_clean), "path": str(active_clean)}
                    if len(df_clean) == 0:
                        logging.warning(f"⚠️ [SANITY WARNING] Active clean file for '{step_name}' is empty.")
                except Exception as e:
                    logging.warning(f"⚠️ [SANITY WARNING] Failed to read active clean file for '{step_name}': {e}")
            
            logger.log_step(f"clean_{step_name}", "COMPLETED", details)
        except Exception as e:
            logger.log_step(f"clean_{step_name}", "ERROR", {"error": str(e)})
            logging.exception(f"Error running step clean_{step_name}")
        return

    # Blue-Green: validate RAW, back up, and swap staging files into place
    logging.info(f"🔄 [Staging Mode] Staging files detected for '{step_name}'. Performing safe dry-run.")
    
    # 1. Validate RAW schema contract
    raw_data_path = staging_ext if (archive_file and staging_ext and staging_ext.exists()) else staging_raw
    if raw_data_path and raw_data_path.exists():
        try:
            logging.info(f"📋 Validating raw schema contract for '{step_name}' using {raw_data_path.name}")
            df_raw = load_dataset(raw_data_path, source_cfg)
            if not validate_dataset_contract(df_raw, step_name, source_cfg):
                raise ValueError("Raw schema contract validation failed.")
        except Exception as e:
            logging.error(f"❌ [INGEST FAILURE] '{step_name}' raw schema validation failed: {e}")
            logger.log_step(f"clean_{step_name}", "ERROR", {"error": f"Raw validation failed: {str(e)}"})
            # Discard staging files and abort
            if staging_raw and staging_raw.exists():
                try: os.remove(staging_raw)
                except: pass
            if staging_ext and staging_ext.exists():
                try: os.remove(staging_ext)
                except: pass
            logging.warning(f"⚠️ [ABORTED] Retained existing cache for '{step_name}'.")
            return

    backups = {}  # Map of active_path -> backup_path
    moved_staging = []  # List of (active_path, staging_path)

    try:
        # 2. Back up active raw files
        if active_raw and active_raw.exists():
            bak_raw = active_raw.with_name(active_raw.name + ".active_bak")
            if bak_raw.exists(): os.remove(bak_raw)
            os.replace(active_raw, bak_raw)
            backups[active_raw] = bak_raw
            
        if active_ext and active_ext.exists():
            bak_ext = active_ext.with_name(active_ext.name + ".active_bak")
            if bak_ext.exists(): os.remove(bak_ext)
            os.replace(active_ext, bak_ext)
            backups[active_ext] = bak_ext

        # 3. Back up active clean parquet
        if active_clean.exists():
            bak_clean = active_clean.with_name(active_clean.name + ".active_bak")
            if bak_clean.exists(): os.remove(bak_clean)
            os.replace(active_clean, bak_clean)
            backups[active_clean] = bak_clean

        # 4. Swap staging raw files to active names
        if staging_raw and staging_raw.exists():
            os.replace(staging_raw, active_raw)
            moved_staging.append((active_raw, staging_raw))
            
        if staging_ext and staging_ext.exists():
            os.replace(staging_ext, active_ext)
            moved_staging.append((active_ext, staging_ext))

        # 5. Run the clean function (it will read the staging data and output to active_clean)
        clean_func(config, muted_logger, *args, **kwargs)

        # 6. Validate the resulting clean parquet file is non-empty
        if not active_clean.exists():
            raise FileNotFoundError(f"Clean step did not generate the clean parquet file: {active_clean}")

        df_clean = pd.read_parquet(active_clean, engine='fastparquet')
        if len(df_clean) == 0:
            raise ValueError("Cleaned output dataset is empty.")

        # Success! Commit changes (delete backups)
        for bak_path in backups.values():
            try: os.remove(bak_path)
            except: pass
        logging.info(f"✅ [SUCCESS] Ingested and verified '{step_name}' successfully. Staging committed.")
        logger.log_step(f"clean_{step_name}", "COMPLETED", {"rows": len(df_clean), "path": str(active_clean)})

    except Exception as e:
        logging.error(f"❌ [INGEST FAILURE] '{step_name}' failed validation/cleaning: {e}")
        logger.log_step(f"clean_{step_name}", "ERROR", {"error": str(e)})

        # Rollback!
        # Delete failed active clean parquet
        if active_clean.exists():
            try: os.remove(active_clean)
            except: pass

        # Move active files back to staging (re-create staging files if we want to preserve them, or delete them)
        # To match Option A "discards staging files", we can delete any active files that were staging
        for active_path, _ in moved_staging:
            if active_path.exists():
                try: os.remove(active_path)
                except: pass

        # Restore original active files from backups
        for active_path, bak_path in backups.items():
            if bak_path.exists():
                os.replace(bak_path, active_path)

        logging.warning(f"⚠️ [ROLLBACK COMPLETE] Reverted '{step_name}' to last known good cache.")

def main(argv=None):
    parser = argparse.ArgumentParser(description="ODIS Ingest Pipeline")
    parser.add_argument('--steps', type=str, help="Comma-separated list of steps to run (e.g. communes,inclusion)")
    parser.add_argument('--skip-live-jobs', action='store_true', help="Skip France Travail Live Jobs fetch")
    parser.add_argument('--skip-inclusion-jobs', action='store_true', help="Skip Inclusion Jobs fetch")
    args = parser.parse_args(argv)

    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)
    
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    
    logger.log_step("ingest_all", "STARTED")
    
    # --- 1. Fetch ROME Referential (New) ---
    fetch_rome_referential(logger)
    
    # --- 2. Fetch RNA RAG Stats from BigQuery (New) ---
    fetch_rna_rag_stats(logger)

    # 2. Fetch others
    for name, source_cfg in config['sources'].items():
        fetch_source(name, source_cfg, logger)
        
    # 2. Clean
    steps_map = {
        'communes': clean_communes,
        'services_inclusion': clean_services_inclusion,
        'structures_inclusion': clean_structures_inclusion,
        'population': clean_population,
        'population_active': clean_population_active,
        'lovac': clean_lovac,
        'rpls': clean_rpls,
        'caf': clean_caf,
        'education': clean_education,
        'associations': clean_associations,
        'refugee_associations': clean_refugee_associations,
        'political': clean_political,
        'housing_occupation': clean_housing_occupation,
        'school_effectifs': clean_school_effectifs,
        'bpe': clean_bpe,
        'codes_postaux': clean_codes_postaux,
        'formations': clean_formations,
        'gares': clean_odace_gares,
        'odace_rent': clean_odace_rent,
        'loyers': clean_loyers,
        'population_details': clean_population_details,
        'nomenclature_waldec': clean_nomenclature_waldec,
        'departements': clean_departements,
        'live_jobs': clean_live_jobs,
        'inclusion_jobs': clean_inclusion_jobs,
        'mob_transports_pub': clean_mob_transports_pub,
        'hebergement_rna': clean_hebergement_rna,
        'jaccueille': clean_jaccueille,
        'log_soc_delay': clean_log_soc_delay,
        'sante_apl': clean_sante_apl,
        'mob_durable': clean_mob_durable,
        'ter_insecurite': clean_ter_insecurite
    }

    selected_steps = args.steps.split(',') if args.steps else steps_map.keys()
    
    for step_name in selected_steps:
        if step_name in steps_map:
            try:
                if step_name == 'live_jobs':
                    skip_live = getattr(args, 'skip_live_jobs', False)
                    run_clean_step_safely(step_name, steps_map[step_name], config, logger, skip=skip_live)
                elif step_name == 'inclusion_jobs':
                    skip_inc = getattr(args, 'skip_inclusion_jobs', False)
                    run_clean_step_safely(step_name, steps_map[step_name], config, logger, skip=skip_inc)
                else:
                    run_clean_step_safely(step_name, steps_map[step_name], config, logger)
            except Exception as e:
                logging.exception(f"❌ [INGEST FAILURE] Error running step '{step_name}'")
        else:
            logging.warning(f"Unknown step: {step_name}")

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
            df_out.to_parquet(output_path, engine='fastparquet')
            logger.log_step("clean_codes_postaux", "COMPLETED", {"path": str(output_path)})
        else:
             logging.warning(f"Codes Postaux: Columns not found. Found: {df.columns}")

    except Exception as e:
        logger.log_step("clean_codes_postaux", "ERROR", {"error": str(e)})

def clean_formations(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Formations and saves to parquet."""
    logger.log_step("clean_formations", "STARTED")

    # 1. Load Codes Postaux Mapping
    cp_path = CLEAN_DIR / "codes_postaux.parquet"
    if not cp_path.exists():
        logging.warning("Codes Postaux clean file not found. Skipping formations.")
        return
    
    cp_df = pd.read_parquet(cp_path, engine='fastparquet')
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
            df_ref.to_parquet(output_ref, engine='fastparquet')
            logger.log_step("clean_formations", "REFERENTIEL", {"path": str(output_ref)})
        else:
                logging.warning("Formations Referentiel: Unexpected columns.")
    
    # 3. Formations Annuaire (CSV)
    annuaire_cfg = config['sources']['formations_annuaire']
    annuaire_path = CACHE_DIR / annuaire_cfg['local_name']
    
    if annuaire_path.exists():
        # Load CSV (semicolon likely)
        try:
            df_annuaire = pd.read_csv(annuaire_path, sep=';', on_bad_lines='skip', low_memory=False)
        except:
            df_annuaire = pd.read_csv(annuaire_path, sep=',', on_bad_lines='skip', low_memory=False)
        
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
        # We need 'Code UAI' and 'Patronyme uai' (Name)th 'formation' or 'domaine'
        formation_col = next((c for c in df_annuaire.columns if 'domaine' in c.lower() or 'formation' in c.lower()), None)
        
        if cp_col and formation_col:
            df_annuaire['code_postal'] = df_annuaire[cp_col].astype(str).str.zfill(5)
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
            df_out.to_parquet(output_annuaire, engine='fastparquet')
            logger.log_step("clean_formations", "ANNUAIRE", {"path": str(output_annuaire), "rows": len(df_out)})
        else:
            logging.warning(f"Formations Annuaire: Columns not found. CP: {cp_col}, Formations: {formation_cols}")


def clean_odace_gares(config: Dict[str, Any], logger: PipelineLogger):
    """Fetches and cleans Gare data from Odace API."""
    logger.log_step("clean_odace_gares", "STARTED")

    client = get_odace_client(logger)
    
    # Fetch Data
    df_commune = client.fetch_dim_commune()
    df_gare = client.fetch_dim_gare()
    
    if df_commune.empty or df_gare.empty:
        logging.warning("Odace API returned empty data for communes or gares.")
        return

    # Initialize output
    # Join Strategy:
    # 1. Primary: commune_sk
    # 2. Fallback: gare_label == commune_label (for rows with empty/null commune_sk)
    
    # Prepare Fallback
    # Ensure common columns, drop duplicates in commune for name match
    # commune_label might not be unique? Assume it is or take first.
    # Actually commune_insee_code is unique. Labels might repeat (e.g. Saint-Sauveur).
    # We should be careful. 
    # But for 'Ambérieu-en-Bugey', it's likely unique.
    
    # Split gares into linked and unlinked
    df_gare['commune_sk'] = df_gare['commune_sk'].replace('', pd.NA)
    
    linked_gares = df_gare.dropna(subset=['commune_sk'])
    unlinked_gares = df_gare[df_gare['commune_sk'].isna()]
    
    logging.info(f"Gares: Total={len(df_gare)}, Linked={len(linked_gares)}, Unlinked={len(unlinked_gares)}")
    
    # 1. Merge Linked
    merged_linked = linked_gares.merge(df_commune, on='commune_sk', how='inner')
    
    # 2. Merge Unlinked (Fallback on Name)
    if not unlinked_gares.empty:
        # Prepare commune name lookup
        # We want to match unlinked 'gare_label' to 'commune_label'
        # To avoid bad matches on duplicate names, we could filter for unique names only?
        # Or just proceed.
        unlinked_gares = unlinked_gares.copy()
        # Normalize for matching
        unlinked_gares['match_name'] = unlinked_gares['gare_label'].astype(str).str.lower().str.strip()
        
        df_commune_lookup = df_commune.copy()
        df_commune_lookup['match_name'] = df_commune_lookup['commune_label'].astype(str).str.lower().str.strip()
        
        # Drop duplicates in lookup (if multiple communes have same name, we can't safely match)
        # Actually, let's keep duplicate names but log.
        # Convert to dict?
        
        merged_fallback = unlinked_gares.merge(df_commune_lookup, on='match_name', how='inner', suffixes=('', '_commune'))
        
        logging.info(f"Fallback Name Match: Recovered {len(merged_fallback)} gares.")
        
        # Align columns
        # merged_linked has cols from dim_commune
        # merged_fallback has cols from dim_commune with possible suffixes if collision (but we joined on match_name)
        # We want 'commune_insee_code'
        
        cols_needed = ['gare_sk', 'commune_insee_code']
        
        combined = pd.concat([
            merged_linked[cols_needed], 
            merged_fallback[cols_needed]
        ], ignore_index=True)
        
    else:
        combined = merged_linked[['gare_sk', 'commune_insee_code']]

    # 2. Group by INSEE Code
    if 'commune_insee_code' not in combined.columns:
            logging.warning("Missing commune_insee_code in Odace data.")
            return

    # Count unique gares
    stats = combined.groupby('commune_insee_code')['gare_sk'].nunique().rename('gare_count').reset_index()
    stats.rename(columns={'commune_insee_code': 'codgeo'}, inplace=True)
    stats['codgeo'] = stats['codgeo'].astype(str).str.zfill(5)
    stats['has_gare'] = (stats['gare_count'] > 0).astype(int)
    
    # Save Gares Stats
    clean_dir = CLEAN_DIR
    clean_dir.mkdir(parents=True, exist_ok=True)
    output_path = clean_dir / "gares.parquet"
    stats.to_parquet(output_path, engine='fastparquet')
    
    # Save Commune SK Mapping
    # We want to keep the link between codgeo and commune_sk
    # df_commune columns: commune_sk, commune_insee_code, commune_label, departement_code, region_code
    sk_mapping = df_commune[['commune_insee_code', 'commune_sk']].drop_duplicates()
    sk_mapping.rename(columns={'commune_insee_code': 'codgeo'}, inplace=True)
    sk_mapping['codgeo'] = sk_mapping['codgeo'].astype(str).str.zfill(5)
    
    sk_output_path = clean_dir / "odace_communes_sk.parquet"
    sk_mapping.to_parquet(sk_output_path, engine='fastparquet')
    logging.info(f"Odace: Saved {len(sk_mapping)} SK mappings to {sk_output_path}")

    logger.log_step("clean_odace_gares", "COMPLETED", {"path": str(output_path), "rows": len(stats)})

def clean_odace_rent(config: Dict[str, Any], logger: PipelineLogger):
    """Fetches and cleans Rent data from Odace API."""
    logger.log_step("clean_odace_rent", "STARTED")

    client = get_odace_client(logger)
    
    # Fetch Data
    df_rent = client.fetch_fact_loyer_annonce()
    df_profil = client.fetch_ref_logement_profil()
    
    if df_rent.empty or df_profil.empty:
        logging.warning("Odace API returned empty data for rent facts or housing profiles.")
        return

    # Filter for relevant data (Prioritize 'commune', fallback to 'maille' if commune not available)
    if 'maille_observation' in df_rent.columns:
        # Define priority (smaller number = higher priority)
        priority_map = {'commune': 1, 'maille': 2, 'EPCI': 3}
        df_rent['maille_priority'] = df_rent['maille_observation'].map(priority_map).fillna(99)
        
        # Sort and drop duplicates for each (commune_sk, logement_profil_sk)
        df_rent = df_rent.sort_values(['commune_sk', 'logement_profil_sk', 'maille_priority'])
        df_rent = df_rent.drop_duplicates(subset=['commune_sk', 'logement_profil_sk'])
        
        logging.info(f"Odace Rent: Filtered and deduplicated. {len(df_rent)} rows remaining.")
        df_rent = df_rent.drop(columns=['maille_priority'])

    # Save to Clean Dir
    df_rent.to_parquet(CLEAN_DIR / "odace_loyer_annonce.parquet", index=False, engine='fastparquet')
    df_profil.to_parquet(CLEAN_DIR / "odace_logement_profil.parquet", index=False, engine='fastparquet')

    logger.log_step("clean_odace_rent", "COMPLETED", {"rent_rows": len(df_rent), "profil_rows": len(df_profil)})


def clean_departements(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Departements referential and saves to parquet."""
    logger.log_step("clean_departements", "STARTED")
    source = config['sources']['departements_ref']
    path = CACHE_DIR / source['local_name']
    if not path.exists(): return

    df = load_dataset(path, source)
    
    # Expected columns: DEP, REG, LIBELLE
    if 'DEP' in df.columns and 'LIBELLE' in df.columns:
        cols_to_keep = ['DEP', 'LIBELLE']
        if 'REG' in df.columns:
            cols_to_keep.append('REG')
            
        df_out = df[cols_to_keep].rename(columns={'DEP': 'code', 'LIBELLE': 'label', 'REG': 'reg_code'})
        df_out['code'] = df_out['code'].astype(str).str.zfill(2)
        if 'reg_code' in df_out.columns:
            df_out['reg_code'] = df_out['reg_code'].astype(str).str.zfill(2)
        
        output_path = CLEAN_DIR / "departements.parquet"
        df_out.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_departements", "COMPLETED", {"path": str(output_path), "rows": len(df_out)})
    else:
        logging.warning(f"Departements: Columns not found. Found: {df.columns}")


def fetch_rna_rag_stats(logger: PipelineLogger) -> Optional[Path]:
    """Fetches RNA category counts from BigQuery with 30-day TTL."""
    local_path = CLEAN_DIR / "rna_inclusion_agg.parquet"
    
    # 1. 30-Day TTL Check
    if local_path.exists():
        mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        if age_days < 30:
            logging.info(f"[RNA RAG] Stats are {age_days} days old. Using cache (TTL=30 days).")
            return local_path
        logging.info(f"[RNA RAG] Stats are {age_days} days old. Refreshing...")

    try:
        client = bigquery.Client()
        
        # 1. Fetch Category Counts
        query_cats = """
        SELECT 
            codgeo,
            primary_category,
            COUNT(*) as count
        FROM `odis-stream2.rna_rag.rna_rag`
        WHERE is_inclusion_relevant = True
        GROUP BY 1, 2
        """
        logging.info("📡 [RNA RAG] Fetching category counts from BigQuery...")
        df_cats = client.query(query_cats).to_dataframe()
        
        # 2. Fetch Refugee-specific Counts
        query_refug = """
        SELECT 
            codgeo,
            COUNT(*) as inc_asso_refug_count
        FROM `odis-stream2.rna_rag.rna_rag`
        WHERE is_refugee_focused = True
        GROUP BY 1
        """
        logging.info("📡 [RNA RAG] Fetching refugee counts from BigQuery...")
        df_refug = client.query(query_refug).to_dataframe()

        if df_cats.empty:
            logging.warning("[RNA RAG] No category data returned from BigQuery.")
            return None

        # Pivot category counts
        df_pivot = df_cats.pivot(index='codgeo', columns='primary_category', values='count').fillna(0).reset_index()
        df_pivot.columns = [f"inc_rna_{col}_count" if col != 'codgeo' else col for col in df_pivot.columns]
        df_pivot['codgeo'] = df_pivot['codgeo'].astype(str).str.zfill(5)

        # Merge with refugee counts
        if not df_refug.empty:
            df_refug['codgeo'] = df_refug['codgeo'].astype(str).str.zfill(5)
            df_pivot = df_pivot.merge(df_refug, on='codgeo', how='left')
            df_pivot['inc_asso_refug_count'] = df_pivot['inc_asso_refug_count'].fillna(0)
        else:
            df_pivot['inc_asso_refug_count'] = 0

        df_pivot.to_parquet(local_path, engine='fastparquet')
        logging.info(f"✅ [RNA RAG] Saved {len(df_pivot)} commune stats to {local_path}")
        logger.log_step("fetch_rna_rag_stats", "COMPLETED", {"path": str(local_path), "rows": len(df_pivot)})
        return local_path

    except Exception as e:
        logging.error(f"❌ [RNA RAG] BigQuery fetch failed: {e}")
        logger.log_step("fetch_rna_rag_stats", "ERROR", {"error": str(e)})
        return None

def clean_mob_transports_pub(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Public Transport Stations data and saves to parquet."""
    logger.log_step("clean_mob_transports_pub", "STARTED")
    source = config['sources']['mob_transports_pub']
    path = CACHE_DIR / source['local_name']
    if not path.exists():
        logging.warning("Public Transport Stations file not found.")
        return

    df = load_dataset(path, source)
    
    # Required columns: geocode_commune, type_transport_en_commun, valeur
    required_cols = ['geocode_commune', 'type_transport_en_commun', 'valeur']
    if not all(col in df.columns for col in required_cols):
        logging.warning(f"Public Transport Stations: Missing columns. Found: {df.columns}")
        return

    # Pivot the data: 1 row per commune
    # type_transport_en_commun values: 'Bus', 'Tramway', 'Métropolitain', 'Train' (assuming)
    df_pivot = df.pivot_table(
        index='geocode_commune',
        columns='type_transport_en_commun',
        values='valeur',
        aggfunc='sum'
    ).reset_index().fillna(0)

    # Rename columns to standardized names
    # The raw data has lowercase keys according to my earlier print: 'bus', 'tramway', 'métro', 'train'
    col_mapping = {
        'geocode_commune': 'codgeo',
        'bus': 'nb_stops_bus',
        'tramway': 'nb_stops_tram',
        'métro': 'nb_stops_metro',
        'train': 'nb_stops_train'
    }
    
    # Apply mapping
    df_pivot.rename(columns=col_mapping, inplace=True)
    
    # Ensure all columns exist (in case some types are missing in the data)
    for col in ['nb_stops_bus', 'nb_stops_tram', 'nb_stops_metro', 'nb_stops_train']:
        if col not in df_pivot.columns:
            df_pivot[col] = 0.0

    df_pivot['codgeo'] = df_pivot['codgeo'].astype(str).str.zfill(5)
    df_pivot['nb_stops_total'] = df_pivot['nb_stops_bus'] + df_pivot['nb_stops_tram'] + df_pivot['nb_stops_metro'] + df_pivot['nb_stops_train']

    output_path = CLEAN_DIR / "mob_transports_pub.parquet"
    df_pivot.to_parquet(output_path, engine='fastparquet')
    logger.log_step("clean_mob_transports_pub", "COMPLETED", {"path": str(output_path), "rows": len(df_pivot)})

def clean_log_soc_delay(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans USH Housing Delay and saves to parquet."""
    logger.log_step("clean_log_soc_delay", "STARTED")
    source = config['sources']['logement_social_delay']
    path = CACHE_DIR / source['local_name']
    if not path.exists(): return

    # USH range A3:B1263. load_dataset uses header=2 (row 3)
    df = load_dataset(path, source)
    
    if len(df) > 1260:
        df = df.iloc[:1260] 
        
    if "SIRET" in df.columns and "Délai d'attribution moyen" in df.columns:
        df.rename(columns={
            "SIRET": "epci_code",
            "Délai d'attribution moyen": "log_soc_delay"
        }, inplace=True)
        df["epci_code"] = df["epci_code"].astype(str).str.strip()
        df["log_soc_delay"] = pd.to_numeric(df["log_soc_delay"], errors='coerce').fillna(0)
        
        output_path = CLEAN_DIR / "log_soc_delay.parquet"
        df.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_log_soc_delay", "COMPLETED", {"rows": len(df)})

def clean_sante_apl(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans DREES APL and saves to parquet."""
    logger.log_step("clean_sante_apl", "STARTED")
    source = config['sources']['sante_apl']
    path = CACHE_DIR / source['local_name']
    if not path.exists(): return

    df = load_dataset(path, source)
    
    codgeo_col = "Code commune INSEE"
    val_col = "APL aux médecins généralistes"
    
    if codgeo_col in df.columns and val_col in df.columns:
        df = df[[codgeo_col, val_col]].rename(columns={
            codgeo_col: "codgeo",
            val_col: "sante_apl"
        })
        df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)
        df["sante_apl"] = pd.to_numeric(df["sante_apl"], errors='coerce').fillna(0)
        
        output_path = CLEAN_DIR / "sante_apl.parquet"
        df.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_sante_apl", "COMPLETED", {"rows": len(df)})

def clean_mob_durable(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Ecolab Mobility and saves to parquet."""
    logger.log_step("clean_mob_durable", "STARTED")
    source = config['sources']['mob_durable_share']
    path = CACHE_DIR / source['local_name']
    if not path.exists(): return

    df = load_dataset(path, source)
    
    if all(c in df.columns for c in ["geocode_commune", "mode_transport", "valeur"]):
        if "date_mesure" in df.columns:
            latest = df["date_mesure"].max()
            df = df[df["date_mesure"] == latest]
            
        df_pivot = df.pivot_table(index='geocode_commune', columns='mode_transport', values='valeur', aggfunc='sum').reset_index()
        df_pivot.rename(columns={'geocode_commune': 'codgeo'}, inplace=True)
        df_pivot['codgeo'] = df_pivot['codgeo'].astype(str).str.zfill(5)
        
        durable_modes = ["Transports en commun", "Marche", "Vélo", "V\u00e9lo"]
        present_durable = [m for m in durable_modes if m in df_pivot.columns]
        
        mode_cols = [c for c in df_pivot.columns if c != 'codgeo']
        df_pivot['total_valeur'] = df_pivot[mode_cols].sum(axis=1)
        
        df_pivot['mob_dur_share'] = np.where(
            df_pivot['total_valeur'] > 0,
            df_pivot[present_durable].sum(axis=1) / df_pivot['total_valeur'],
            0.0
        )
        
        df_out = df_pivot[['codgeo', 'mob_dur_share']]
        output_path = CLEAN_DIR / "mob_durable.parquet"
        df_out.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_mob_durable", "COMPLETED", {"rows": len(df_out)})

def clean_ter_insecurite(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans SSMSI Insecurity and saves to parquet."""
    logger.log_step("clean_ter_insecurite", "STARTED")
    source = config['sources']['ter_insecurite']
    path = CACHE_DIR / source['local_name']
    if not path.exists(): return

    df = load_dataset(path, source)
    
    if all(c in df.columns for c in ["CODGEO_2025", "annee", "indicateur", "taux_pour_mille"]):
        latest = df["annee"].max()
        df = df[df["annee"] == latest]
        
        df_agg = df.groupby('CODGEO_2025')['taux_pour_mille'].sum().reset_index()
        df_agg.rename(columns={'CODGEO_2025': 'codgeo', 'taux_pour_mille': 'ter_insecurite'}, inplace=True)
        df_agg['codgeo'] = df_agg['codgeo'].astype(str).str.zfill(5)
        
        output_path = CLEAN_DIR / "ter_insecurite.parquet"
        df_agg.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_ter_insecurite", "COMPLETED", {"rows": len(df_agg)})

def clean_jaccueille(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans J'Accueille data and aggregates by Bassin de Vie."""
    logger.log_step("clean_jaccueille", "STARTED")
    source = config.get('local_files', {}).get('jaccueille')
    if not source:
        source = config['sources'].get('jaccueille')
        
    if not source:
        logging.warning("J'Accueille source config not found.")
        return

    path = CACHE_DIR / source['local_name']
    if not path.exists():
        # Maybe it's directly in local? The fetch_source should have copied it.
        logging.warning(f"J'Accueille file not found at {path}.")
        return

    # 1. Load J'Accueille CSV
    try:
        df = pd.read_csv(path)
    except Exception as e:
        df = pd.read_csv(path, sep=';') # fallback

    # Expected columns: 'Code postal', 'Nombre d'enregistrements'
    cp_col = next((c for c in df.columns if 'Code postal' in c or 'code_postal' in c), None)
    val_col = next((c for c in df.columns if 'Nombre d\'enregistrements' in c or 'accueillants' in c.lower() or 'count' in c.lower()), None)

    if not cp_col or not val_col:
        logging.warning(f"J'Accueille: Could not identify columns. Found: {df.columns}")
        return

    df = df.rename(columns={cp_col: 'code_postal', val_col: 'heb_jaccueille_count'})
    df['code_postal'] = df['code_postal'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(5)
    df['heb_jaccueille_count'] = pd.to_numeric(df['heb_jaccueille_count'], errors='coerce').fillna(0)

    # 2. Map Code Postal -> Code Commune -> Bassin de Vie
    # A. Code Postal -> Commune (using official mapping)
    cp_mapping_path = CLEAN_DIR / "codes_postaux.parquet"
    if not cp_mapping_path.exists():
        logging.warning("Codes Postaux mapping not found, cannot map J'Accueille data.")
        return
        
    df_cp = pd.read_parquet(cp_mapping_path, engine='fastparquet') # 'code_postal', 'codgeo'
    
    # Take the first commune for a given postal code (since 1 CP maps to 1 BDV eventually)
    df_cp_unique = df_cp.drop_duplicates(subset=['code_postal'], keep='first')
    
    merged = df.merge(df_cp_unique, on='code_postal', how='inner')
    
    # B. Commune -> Bassin de Vie (using our pre-processed mapping or from BDV dataset)
    # The BDV mapping is usually applied in `build.py`, but we can extract it from the raw BDV file or communes_pre if it exists.
    # Let's read it from the raw BDV file loaded earlier (bassins_de_vie)
    bdv_cfg = config['sources']['bassins_de_vie']
    bdv_path = CACHE_DIR / bdv_cfg['archive_file']
    
    if not bdv_path.exists():
        logging.warning("Bassin de vie raw file not found.")
        return

    df_bdv = load_dataset(bdv_path, bdv_cfg)
    
    codgeo_col = next((c for c in df_bdv.columns if 'Code géographique' in c or 'CODGEO' in c), None)
    bdv_col = next((c for c in df_bdv.columns if 'Bassin de vie' in c), None)
    
    if codgeo_col and bdv_col:
        df_bdv = df_bdv[[codgeo_col, bdv_col]].rename(columns={codgeo_col: 'codgeo', bdv_col: 'bassin_de_vie'})
        df_bdv['codgeo'] = df_bdv['codgeo'].astype(str).str.zfill(5)
        df_bdv['bassin_de_vie'] = df_bdv['bassin_de_vie'].astype(str).str.replace(r'\.0$', '', regex=True)
        
        merged_bdv = merged.merge(df_bdv, on='codgeo', how='inner')
        
        # Aggregate by BDV
        df_agg = merged_bdv.groupby('bassin_de_vie')['heb_jaccueille_count'].sum().reset_index()
        
        output_path = CLEAN_DIR / "jaccueille_bdv.parquet"
        df_agg.to_parquet(output_path, engine='fastparquet')
        logger.log_step("clean_jaccueille", "COMPLETED", {"path": str(output_path), "rows": len(df_agg)})
    else:
        logging.warning("Bassin de vie columns not identified for mapping.")

if __name__ == "__main__":
    main()
