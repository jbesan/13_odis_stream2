import os
import yaml
import requests
import pandas as pd
import geopandas as gpd
import logging
import json
import zipfile
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Union

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CONFIG_FILE = "pipeline/sources.yaml"
CACHE_DIR = Path("pipeline/cache")
OUTPUT_DIR = Path("pipeline/output")
STATUS_FILE = Path("pipeline/status.json")

class PipelineLogger:
    def __init__(self, status_file: Path):
        self.status_file = status_file
        self.status = self._load_status()

    def _load_status(self) -> Dict[str, Any]:
        if self.status_file.exists():
            try:
                with open(self.status_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_status(self):
        with open(self.status_file, 'w') as f:
            json.dump(self.status, f, indent=2, default=str)

    def log_step(self, step_name: str, status: str, details: Optional[Dict[str, Any]] = None):
        if "steps" not in self.status:
            self.status["steps"] = {}
        
        self.status["steps"][step_name] = {
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "details": details or {}
        }
        self._save_status()
        logging.info(f"Step '{step_name}': {status}")

    def log_source(self, source_name: str, status: str, file_path: Optional[str] = None):
        if "sources" not in self.status:
            self.status["sources"] = {}
        
        self.status["sources"][source_name] = {
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "file": str(file_path) if file_path else None
        }
        self._save_status()

def load_config(config_path: str) -> Dict[str, Any]:
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def extract_zip(zip_path: Path, target_file: str) -> Path:
    """Extracts a specific file from a zip archive."""
    with zipfile.ZipFile(zip_path, 'r') as z:
        # List files to find the match if exact name isn't known (optional improvement)
        # For now, assume target_file is the exact name inside the zip
        z.extract(target_file, path=zip_path.parent)
    return zip_path.parent / target_file

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

def load_dataset(path: Path, config: Dict[str, Any], **kwargs) -> pd.DataFrame:
    """Loads a dataset based on its extension or config."""
    fmt = config.get('format', '').lower()
    
    # Prioritize config format
    encoding = config.get('encoding', None)
    if fmt == 'parquet':
        return pd.read_parquet(path, **kwargs)
    elif fmt == 'csv':
        return pd.read_csv(path, sep=None, engine='python', encoding=encoding, **kwargs)
    elif fmt == 'json':
        return pd.read_json(path, encoding=encoding, **kwargs)
    elif fmt == 'geojson':
        return gpd.read_file(path, encoding=encoding, **kwargs)
    elif fmt == 'xlsx':
        sheet = kwargs.get('sheet_name', config.get('sheet_name', 0))
        header = kwargs.get('header', config.get('header', 0))
        skiprows = kwargs.get('skiprows', config.get('skiprows', None))
        # Remove kwargs that are already handled or specific to read_excel
        excel_kwargs = {k: v for k, v in kwargs.items() if k not in ['sheet_name', 'header', 'skiprows']}
        return pd.read_excel(path, sheet_name=sheet, engine='calamine', header=header, skiprows=skiprows, **excel_kwargs)
    
    # Fallback to extension
    if path.suffix == '.parquet':
        return pd.read_parquet(path, **kwargs)
    elif path.suffix == '.csv':
        return pd.read_csv(path, sep=None, engine='python', encoding=encoding, **kwargs)
    elif path.suffix == '.json':
        return pd.read_json(path, encoding=encoding, **kwargs)
    elif path.suffix == '.geojson':
        return gpd.read_file(path, encoding=encoding, **kwargs)
    elif path.suffix == '.xlsx':
        sheet = kwargs.get('sheet_name', config.get('sheet_name', 0))
        header = kwargs.get('header', config.get('header', 0))
        skiprows = kwargs.get('skiprows', config.get('skiprows', None))
        excel_kwargs = {k: v for k, v in kwargs.items() if k not in ['sheet_name', 'header', 'skiprows']}
        return pd.read_excel(path, sheet_name=sheet, engine='calamine', header=header, skiprows=skiprows, **excel_kwargs)
        
    raise ValueError(f"Unsupported format: {fmt} or extension {path.suffix} for {path}")

def process_bmo_rome(config: Dict[str, Any], logger: PipelineLogger) -> pd.DataFrame:
    """
    Processes BMO Top 5 jobs per commune and Total Job Offers (met).
    Input: Excel file with 'export_top5_diff' and 'export_diffusees' sheets.
    Output: DataFrame indexed by codgeo with 'top_metiers' (list) and 'met' (int).
    """
    logger.log_step("process_bmo_rome", "STARTED")
    try:
        source = config['sources']['bmo_rome']
        path = CACHE_DIR / source['local_name']
        
        if not path.exists():
             logging.warning("BMO file not found. Skipping BMO processing.")
             return pd.DataFrame()

        # 1. Top 5 Metiers (export_top5_diff)
        # No header, columns: Type, Code, Rank, ID, ROME, Count
        df_top5 = load_dataset(path, source, sheet_name="export_top5_diff", header=None)
        df_top5.columns = ['type', 'code', 'rank', 'id', 'rome', 'count']
        
        # Filter for Communes
        df_top5 = df_top5[df_top5['type'] == 'Commune']
        
        # Filter valid ROME codes (format X1234)
        df_top5 = df_top5[df_top5['rome'].astype(str).str.match(r'^[A-Z]\d{4}$', na=False)]
        
        # Sort by rank
        df_top5 = df_top5.sort_values(['code', 'rank'])
        
        # Group by Commune and collect ROME codes
        top_metiers = df_top5.groupby('code')['rome'].apply(lambda x: list(x.head(5))).reset_index()
        top_metiers.columns = ['codgeo', 'metiers_offres_top5']
        top_metiers['codgeo'] = top_metiers['codgeo'].astype(str).str.zfill(5)
        top_metiers.set_index('codgeo', inplace=True)
        
        # 2. Total Job Offers (export_diffusees)
        # Columns: type, code, libelle, offres_diff_com, offres_diff_dur_com
        df_diff = load_dataset(path, source, sheet_name="export_diffusees")
        
        # Filter for Communes
        if 'type' in df_diff.columns:
            df_diff = df_diff[df_diff['type'] == 'Commune']
            
        # Select relevant columns
        # We need 'code' (codgeo) and 'offres_diff_com' (met)
        if 'code' in df_diff.columns and 'offres_diff_com' in df_diff.columns:
            met_df = df_diff[['code', 'offres_diff_com']].rename(columns={'code': 'codgeo', 'offres_diff_com': 'metiers_offres_diff'})
            met_df['codgeo'] = met_df['codgeo'].astype(str).str.zfill(5)
            met_df.set_index('codgeo', inplace=True)
            
            # Join with top_metiers
            result = top_metiers.join(met_df, how='outer')
        else:
            logging.warning("Missing columns in export_diffusees. Returning only top_metiers.")
            result = top_metiers
            
        logger.log_step("process_bmo_rome", "COMPLETED", {"count": len(result)})
        return result

    except Exception as e:
        logger.log_step("process_bmo_rome", "ERROR", {"error": str(e)})
        logging.error(f"BMO Processing failed: {e}")
        return pd.DataFrame()

def process_population_active(config: Dict[str, Any], logger: PipelineLogger) -> pd.DataFrame:
    """
    Processes Population Active data to get 'pop_active' (active population).
    Input: CSV file (DS_RP_EMPLOI_LR_COMP_2022_data.csv).
    Output: DataFrame indexed by codgeo with 'pop_active' column.
    """
    logger.log_step("process_population_active", "started")
    try:
        source = config['sources']['population_active']
        path = CACHE_DIR / source['archive_file'] # It's inside the zip, extracted
        
        if not path.exists():
             logging.warning("Population Active file not found.")
             return pd.DataFrame()
             
        # Load data
        # User snippet:
        # actif = pd.read_csv(..., delimiter=';')
        # actif_2022 = actif[(actif.TIME_PERIOD == 2022) & (actif.GEO_OBJECT == "COM") & (actif.PCS == "_T") & (actif.EMPSTA_ENQ.isin(["1T2", "1"]))]
        # pivot...
        
        actif = load_dataset(path, source) # load_dataset handles csv/zip
        
        # Filter
        # Ensure columns exist
        required_cols = ['TIME_PERIOD', 'GEO_OBJECT', 'PCS', 'EMPSTA_ENQ', 'GEO', 'OBS_VALUE']
        if not all(col in actif.columns for col in required_cols):
             logging.warning(f"Population Active missing columns: {actif.columns}")
             return pd.DataFrame()
             
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
        
        # 1T2 = Total Actifs, 1 = Employés
        # User requested: total, chomeurs, employes
        # chomeurs = 1T2 - 1
        
        if "1T2" in actif_2022.columns and "1" in actif_2022.columns:
            actif_2022["pop_chomeurs"] = actif_2022["1T2"] - actif_2022["1"]
            actif_2022.rename(columns={"1T2": "pop_active", "1": "pop_employes"}, inplace=True)
            
            # Select columns
            actif_2022 = actif_2022[["pop_active", "pop_employes", "pop_chomeurs"]]
            
            actif_2022.index.name = 'codgeo'
            # Ensure codgeo format
            actif_2022.reset_index(inplace=True)
            actif_2022['codgeo'] = actif_2022['codgeo'].astype(str).str.zfill(5)
            actif_2022.set_index('codgeo', inplace=True)
            
            logger.log_step("process_population_active", "completed", {"count": len(actif_2022)})
            return actif_2022
        else:
             logging.warning(f"Population Active pivot failed. Columns found: {actif_2022.columns}")
             return pd.DataFrame()

    except Exception as e:
        logger.log_step("process_population_active", "failed", {"error": str(e)})
        logging.error(f"Population Active processing failed: {e}")
        return pd.DataFrame()

def process_lovac(df: pd.DataFrame, logger: PipelineLogger) -> pd.DataFrame:
    """
    Process LOVAC data (Vacant Housing).
    Expected columns: CODGEO_25, pp_vacant_plus_2ans_25
    """
    logger.log_step("process_lovac", "started")
    try:
        # Normalize columns
        df.columns = [c.strip() for c in df.columns]
        
        # Identify codgeo column
        codgeo_col = next((c for c in df.columns if 'CODGEO' in c), None)
        if not codgeo_col:
            raise ValueError(f"LOVAC: Could not find CODGEO column. Available: {df.columns.tolist()}")
            
        # Identify vacancy column
        vac_col = 'pp_vacant_plus_2ans_25'
        if vac_col not in df.columns:
             # Try to find it loosely
             vac_col = next((c for c in df.columns if 'vacant_plus_2ans' in c), None)
             if not vac_col:
                 raise ValueError(f"LOVAC: Could not find vacancy column. Available: {df.columns.tolist()}")

        # Clean data
        # Replace 's' (secret) with 0 or NaN. data_loader uses 0.
        df[vac_col] = pd.to_numeric(df[vac_col].replace('s', 0), errors='coerce').fillna(0)
        
        # Select and rename
        df_out = df[[codgeo_col, vac_col]].rename(columns={codgeo_col: 'codgeo', vac_col: 'pp_vacant_plus_2ans_25'})
        
        # Ensure codgeo is string and padded if necessary (though usually it is)
        df_out['codgeo'] = df_out['codgeo'].astype(str)
        
        logger.log_step("process_lovac", "completed")
        return df_out
    except Exception as e:
        logger.log_step("process_lovac", "failed", details={"error":str(e)})
        raise

def process_rpls(df: pd.DataFrame, logger: PipelineLogger) -> pd.DataFrame:
    """
    Process RPLS data (Social Housing).
    Expected columns: DEP, COM, LIBCOM, ... (Need to find Total and Vacant)
    """
    logger.log_step("process_rpls", "started")
    try:
        # Normalize columns
        df.columns = [str(c).strip() for c in df.columns]
        
        # Expected: DEP, DEPCOM_ARM or CODGEO
        # Found: DEP, DEPCOM_ARM
        
        if 'CODGEO' in df.columns:
            df['codgeo'] = df['CODGEO'].astype(str).str.zfill(5)
        elif 'DEPCOM_ARM' in df.columns:
             df['codgeo'] = df['DEPCOM_ARM'].astype(str).str.zfill(5)
        elif 'DEP' in df.columns and 'COM' in df.columns:
            df['codgeo'] = df['DEP'].astype(str).str.zfill(2) + df['COM'].astype(str).str.zfill(3)
        else:
            raise ValueError(f"RPLS: Could not find DEP/COM or CODGEO columns. Available: {df.columns.tolist()}")

        # Identify Metrics
        # We need: log_soc_total, log_soc_inoccupes
        # Look for columns
        cols = df.columns.tolist()
        
        # Total
        total_col = next((c for c in cols if 'total' in c.lower() and 'parc' in c.lower()), None)
        if not total_col:
             # Fallback: look for just 'total' or specific known names
             total_col = next((c for c in cols if c in ['PARC_SOCIAL_NB', 'NB_LOG_TOT', 'nb_lgt_tot']), None)
        
        # Vacant (Inoccupes)
        vac_col = next((c for c in cols if 'vacant' in c.lower() or 'inoccup' in c.lower()), None)
        
        if not total_col or not vac_col:
             # Log available columns for debugging
             logger.log_step("process_rpls", "warning", details={"error":f"Columns not found. Available: {cols}"})
             # Return empty df with codgeo to avoid crash, or raise?
             # Let's raise to force fix
             raise ValueError(f"RPLS: Missing columns. Found Total: {total_col}, Vacant: {vac_col}")

        # Clean and Select
        df['log_soc_total'] = pd.to_numeric(df[total_col], errors='coerce').fillna(0)
        df['log_soc_inoccupes'] = pd.to_numeric(df[vac_col], errors='coerce').fillna(0)
        
        df_out = df[['codgeo', 'log_soc_total', 'log_soc_inoccupes']]
        
        logger.log_step("process_rpls", "completed")
        return df_out
    except Exception as e:
        logger.log_step("process_rpls", "failed", details={"error":str(e)})
        raise

def process_maternites(df: pd.DataFrame, logger: PipelineLogger) -> pd.DataFrame:
    """
    Process DREES Maternités.
    """
    logger.log_step("process_maternites", "started")
    try:
        # DREES JSON usually has a list of dicts.
        # We need to extract relevant columns.
        # Expected: NumeroFiness, etc.
        # For now, just return it as is or minimal processing.
        # The real enrichment happens in process_health with FINESS.
        logger.log_step("process_maternites", "completed")
        return df
    except Exception as e:
        logger.log_step("process_maternites", "failed", details={"error":str(e)})
        raise

def process_caf(df: pd.DataFrame, logger: PipelineLogger) -> pd.DataFrame:
    """
    Process CAF data (Petite Enfance).
    Expected: codgeo, taux_couverture
    """
    logger.log_step("process_caf", "started")
    try:
        # Normalize
        df.columns = [c.strip() for c in df.columns]
        
        # Codgeo
        if 'codgeo' not in df.columns:
             # Try to find it
             codgeo_col = next((c for c in df.columns if 'codgeo' in c.lower() or 'insee' in c.lower() or c == 'numcom'), None)
             if codgeo_col:
                 df.rename(columns={codgeo_col: 'codgeo'}, inplace=True)
             else:
                 raise ValueError(f"CAF: Missing codgeo. Available: {df.columns}")
        
        df['codgeo'] = df['codgeo'].astype(str).str.zfill(5)

        # Filter for latest year
        if 'annee' in df.columns:
            max_year = df['annee'].max()
            df = df[df['annee'] == max_year]
            
        # Rename columns if needed
        # Expected: codgeo, taux_couverture
        if 'taux_accueil_total' in df.columns:
            df.rename(columns={'taux_accueil_total': 'taux_couverture'}, inplace=True)
        elif 'txcouv_com' in df.columns:
            df.rename(columns={'txcouv_com': 'taux_couverture'}, inplace=True)
            
        if 'taux_couverture' not in df.columns:
             # Look for it
             taux_col = next((c for c in df.columns if 'taux' in c.lower() and 'couverture' in c.lower()), None)
             if taux_col:
                 df.rename(columns={taux_col: 'taux_couverture'}, inplace=True)
             else:
                 # If missing, maybe it's not the right file.
                 # But we can return empty with codgeo to be safe.
                 logger.log_step("process_caf", "warning", details={"error": "taux_couverture not found"})
                 return df[['codgeo']]

        df['taux_couverture'] = pd.to_numeric(df['taux_couverture'], errors='coerce').fillna(0)
        
        df_out = df[['codgeo', 'taux_couverture']]
        logger.log_step("process_caf", "completed")
        return df_out
    except Exception as e:
        logger.log_step("process_caf", "failed", details={"error":str(e)})
        raise

def process_education(df_effectifs: pd.DataFrame, df_annuaire: pd.DataFrame, logger: PipelineLogger) -> pd.DataFrame:
    """
    Process Education Effectifs.
    Aggregates school counts by commune.
    """
    logger.log_step("process_education", "started")
    try:
        # Normalize columns
        df_effectifs.columns = [c.strip() for c in df_effectifs.columns]
        df_annuaire.columns = [c.strip() for c in df_annuaire.columns]
        
        # Join with Annuaire to get codgeo
        # Effectifs: numero_ecole
        # Annuaire: identifiant_de_l_etablissement, code_commune
        
        if 'numero_ecole' not in df_effectifs.columns:
             raise ValueError(f"Education Effectifs: Missing numero_ecole. Available: {df_effectifs.columns}")
             
        if 'identifiant_de_l_etablissement' not in df_annuaire.columns:
             # Maybe it's named differently in geojson?
             # Based on inspection: 'identifiant_de_l_etablissement'
             pass

        # Merge
        merged = df_effectifs.merge(
            df_annuaire[['identifiant_de_l_etablissement', 'code_commune']],
            left_on='numero_ecole',
            right_on='identifiant_de_l_etablissement',
            how='left'
        )
        
        # Rename code_commune to codgeo
        merged.rename(columns={'code_commune': 'codgeo'}, inplace=True)
        
        # Filter out missing codgeo
        merged = merged.dropna(subset=['codgeo'])
        merged['codgeo'] = merged['codgeo'].astype(str).str.zfill(5)

        # Calculate counts
        # Maternelle: mat_ct > 0
        # Elementaire: cp_ct + ... + cm2_ct > 0
        
        # Fill NaNs
        cols = ['mat_ct', 'cp_ct', 'ce1_ct', 'ce2_ct', 'cm1_ct', 'cm2_ct']
        for c in cols:
            if c in merged.columns:
                merged[c] = merged[c].fillna(0)
            else:
                merged[c] = 0
                
        merged['is_maternelle'] = (merged['mat_ct'] > 0).astype(int)
        merged['is_elementaire'] = (merged[['cp_ct', 'ce1_ct', 'ce2_ct', 'cm1_ct', 'cm2_ct']].sum(axis=1) > 0).astype(int)
        
        # Group by codgeo
        df_agg = merged.groupby('codgeo').agg({
            'is_maternelle': 'sum',
            'is_elementaire': 'sum',
            'numero_ecole': 'count' # Total schools
        }).rename(columns={
            'is_maternelle': 'count_maternelle',
            'is_elementaire': 'count_elementaire',
            'numero_ecole': 'ecoles_ct'
        }).reset_index()
        
        logger.log_step("process_education", "completed")
        return df_agg
    except Exception as e:
        logger.log_step("process_education", "failed", details={"error":str(e)})
        raise

def process_inclusion(df: pd.DataFrame, logger: PipelineLogger) -> pd.DataFrame:
    """
    Process Services Inclusion.
    Aggregates count by commune.
    """
    logger.log_step("process_inclusion", "started")
    try:
        # Expected: codgeo
        if 'codgeo' not in df.columns:
             # Try code_insee
             if 'code_insee' in df.columns:
                 df.rename(columns={'code_insee': 'codgeo'}, inplace=True)
             else:
                 # If geojson, maybe it's in properties
                 pass

        if 'codgeo' in df.columns:
            df_agg = df.groupby('codgeo').size().rename('svc_incl_count').reset_index()
            logger.log_step("process_inclusion", "completed")
            return df_agg
        else:
            logger.log_step("process_inclusion", "warning", details={"error": "codgeo not found"})
            return pd.DataFrame()
            
    except Exception as e:
        logger.log_step("process_inclusion", "failed", details={"error":str(e)})
        raise

def process_associations(df: pd.DataFrame, logger: PipelineLogger) -> pd.DataFrame:
    """
    Process Associations (RNA).
    Aggregates Lien Social and Affinite counts.
    """
    logger.log_step("process_associations", "started")
    try:
        # Expected: codgeo, id_waldec
        # Actual: adrs_codeinsee, objet_social1
        
        # Normalize columns
        df.columns = [c.strip() for c in df.columns]
        
        if 'adrs_codeinsee' in df.columns:
            df.rename(columns={'adrs_codeinsee': 'codgeo'}, inplace=True)
            
        if 'objet_social1' in df.columns:
            df.rename(columns={'objet_social1': 'id_waldec'}, inplace=True)
            
        if 'codgeo' not in df.columns:
             logger.log_step("process_associations", "warning", details={"error": "codgeo not found"})
             return pd.DataFrame()

        # Filter for Lien Social (CORE)
        # We need the config for WALDEC codes.
        try:
            from app import config as cfg
        except ImportError:
            # Fallback if running from pipeline dir
            import sys
            sys.path.append(str(Path(__file__).resolve().parent.parent))
            from app import config as cfg
        
        core_prefixes = tuple(cfg.WALDEC_CORE_INCLUSION)
        
        # Ensure strings
        df['id_waldec'] = df['id_waldec'].astype(str).str.zfill(6)
        df['codgeo'] = df['codgeo'].astype(str).str.zfill(5)
        
        # Lien Social
        core_mask = df['id_waldec'].str.startswith(core_prefixes, na=False)
        lien_social = df[core_mask].groupby('codgeo').size().rename('lien_social_count').reset_index()
        
        logger.log_step("process_associations", "completed")
        return lien_social
    except Exception as e:
        logger.log_step("process_associations", "failed", details={"error":str(e)})
        return pd.DataFrame() # Return empty on failure to not block


def generate_referentiels(config: Dict[str, Any], logger: PipelineLogger):
    """
    Generates referentiels (dropdown lists, labels).
    Output: referentiels.parquet
    Structure: key (str), code (str), label (str), metadata (JSON)
    """
    logger.log_step("generate_referentiels", "started")
    try:
        refs_list = []
        
        # --- ROME ---
        rome_cfg = config['sources']['rome']
        rome_path = CACHE_DIR / rome_cfg['archive_file']
        if rome_path.exists():
            # Load with latin-1 as discovered
            with open(rome_path, 'r', encoding='latin-1') as f:
                rome_data = json.load(f)
            
            rome_df = pd.DataFrame(rome_data)
            # Columns: code_rome, libelle
            rome_ref = pd.DataFrame({
                'key': 'rome_codes',
                'code': rome_df['code_rome'],
                'label': rome_df['libelle'],
                'metadata': rome_df.drop(columns=['code_rome', 'libelle']).to_json(orient='records')
            })
            refs_list.append(rome_ref)
            
        # --- Concatenate ---
        if refs_list:
            all_refs = pd.concat(refs_list, ignore_index=True)
            output_path = OUTPUT_DIR / "referentiels.parquet"
            all_refs.to_parquet(output_path)
            logger.log_step("generate_referentiels", "created", details={"count": len(all_refs), "path": str(output_path)})
        else:
            logger.log_step("generate_referentiels", "skipped", details={"reason": "No referentiels sources found"})

    except Exception as e:
        logger.log_step("generate_referentiels", "failed", details={"error": str(e)})
        raise

def generate_pois(config: Dict[str, Any], logger: PipelineLogger):
    """
    Generates a consolidated POI file from various sources.
    Output: pois.parquet
    Columns: id, name, type, category, lat, lon, metadata (JSON)
    """
    logger.log_step("generate_pois", "started")
    try:
        pois_list = []
        
        # --- Education ---
        edu_cfg = config['sources']['education_annuaire']
        edu_path = CACHE_DIR / edu_cfg['local_name']
        if edu_path.exists():
            edu_df = load_dataset(edu_path, edu_cfg)
            # GeoJSON already has geometry, but we want explicit lat/lon columns if possible
            # or just use geometry x/y
            if 'geometry' in edu_df.columns:
                edu_df['lon'] = edu_df.geometry.x
                edu_df['lat'] = edu_df.geometry.y
            
            edu_pois = pd.DataFrame({
                'id': edu_df['identifiant_de_l_etablissement'],
                'name': edu_df['nom_etablissement'],
                'type': edu_df['type_etablissement'],
                'category': 'education',
                'lat': edu_df['lat'],
                'lon': edu_df['lon'],
                'metadata': edu_df[['statut_public_prive', 'code_commune', 'adresse_1']].to_dict(orient='records')
            })
            pois_list.append(edu_pois)
            
        # --- Health (FINESS) ---
        finess_cfg = config['sources']['finess_national']
        finess_path = CACHE_DIR / finess_cfg['local_name']
        if finess_path.exists():
            finess_df = load_dataset(finess_path, finess_cfg)
            
            # Reproject L93 to WGS84
            # Filter valid coords
            finess_df = finess_df.dropna(subset=['coordxet', 'coordyet'])
            
            gdf_finess = gpd.GeoDataFrame(
                finess_df,
                geometry=gpd.points_from_xy(finess_df.coordxet, finess_df.coordyet),
                crs="EPSG:2154"
            )
            gdf_finess = gdf_finess.to_crs("EPSG:4326")
            
            finess_pois = pd.DataFrame({
                'id': gdf_finess['nofinesset'],
                'name': gdf_finess['RaisonSociale'],
                'type': gdf_finess['LibelleCategorieAgregat'], # Use label for better readability
                'category': 'sante',
                'lat': gdf_finess.geometry.y,
                'lon': gdf_finess.geometry.x,
                'metadata': gdf_finess[['CategorieAgregat', 'Commune', 'LibelleVoie']].to_dict(orient='records')
            })
            pois_list.append(finess_pois)
            
        # --- Inclusion ---
        incl_cfg = config['sources']['services_inclusion']
        incl_path = CACHE_DIR / incl_cfg['local_name']
        if incl_path.exists():
            incl_df = load_dataset(incl_path, incl_cfg)
            
            # Filter valid coords
            incl_df = incl_df.dropna(subset=['latitude', 'longitude'])
            
            incl_pois = pd.DataFrame({
                'id': incl_df['id'],
                'name': incl_df['nom'],
                'type': incl_df['thematiques'].apply(lambda x: str(x) if x is not None else 'Autre'), # thematiques is often a list or complex
                'category': 'inclusion',
                'lat': incl_df['latitude'],
                'lon': incl_df['longitude'],
                'metadata': incl_df[['description', 'adresse', 'code_insee']].to_dict(orient='records')
            })
            pois_list.append(incl_pois)

        # --- Concatenate ---
        if pois_list:
            all_pois = pd.concat(pois_list, ignore_index=True)
            # Ensure metadata is JSON string for parquet compatibility if needed, 
            # or keep as struct if using duckdb/parquet-tools that support it.
            # For simplicity in pandas/streamlit, stringify might be safer.
            all_pois['metadata'] = all_pois['metadata'].apply(json.dumps)
            
            output_path = OUTPUT_DIR / "pois.parquet"
            all_pois.to_parquet(output_path)
            logger.log_step("generate_pois", "created", details={"count": len(all_pois), "path": str(output_path)})
        else:
            logger.log_step("generate_pois", "skipped", details={"reason": "No POI sources found"})

    except Exception as e:
        logger.log_step("generate_pois", "failed", details={"error": str(e)})
        raise

def main():
    parser = argparse.ArgumentParser(description="ODIS Data Pipeline ETL")
    parser.add_argument("--step", choices=["fetch", "process", "all"], default="all", help="Step to run")
    args = parser.parse_args()

    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)
    
    # --- STEP 1: FETCH ---
    if args.step in ["fetch", "all"]:
        logger.log_step("fetch_all", "STARTED")
        for name, source_cfg in config['sources'].items():
            fetch_source(name, source_cfg, logger)
        logger.log_step("fetch_all", "COMPLETED")

    # --- STEP 2: PROCESS ---
    if args.step in ["process", "all"]:
        logger.log_step("process_all", "STARTED")
        
        # Load Local FINESS (Prerequisite for enrichment)
        finess_cfg = config['local_files']['finess']
        finess_path = Path(finess_cfg['path'])
        if finess_path.exists():
            finess_df = load_dataset(finess_path, finess_cfg)
        else:
            logging.warning(f"Local FINESS file not found at {finess_path}. Enrichment will be limited.")
            finess_df = pd.DataFrame()

        # --- Load & Clean Core Datasets ---
        
        # 1. Communes (GeoJSON)
        # Load Communes (Base Geometry)
        communes_cfg = config['sources']['communes']
        communes_path = CACHE_DIR / communes_cfg['local_name']
        
        if not communes_path.exists():
            logging.error("Communes file not found. Cannot proceed.")
            return

        communes_gdf = load_dataset(communes_path, communes_cfg)
        
        # Process BMO/ROME
        bmo_df = process_bmo_rome(config, logger)
        
        # Process Population Active
        pop_active_df = process_population_active(config, logger)
        
        # Process Maternites (DREES) - Placeholder
        # mat_df = process_maternites(..., logger)
        
        # Process LOVAC
        lovac_cfg = config['sources']['logement_vacant']
        lovac_path = CACHE_DIR / lovac_cfg['local_name']
        lovac_df = pd.DataFrame()
        if lovac_path.exists():
            lovac_df = load_dataset(lovac_path, lovac_cfg)
            lovac_df = process_lovac(lovac_df, logger)
            
        # Process RPLS
        rpls_cfg = config['sources']['logement_social']
        rpls_path = CACHE_DIR / rpls_cfg['local_name']
        rpls_df = pd.DataFrame()
        if rpls_path.exists():
            rpls_df = load_dataset(rpls_path, rpls_cfg)
            rpls_df = process_rpls(rpls_df, logger)

        # Merge Datasets into Communes
        logging.info("Merging datasets...")
        
        # Ensure codgeo is index for joining or consistent column
        # communes_gdf usually has 'codgeo' column from load_dataset if it's geojson/shp
        # If it's the specific communes file, it might need renaming.
        # Based on previous steps, we assume load_dataset handles it or we check here.
        if 'codgeo' not in communes_gdf.columns:
            if 'INSEE_COM' in communes_gdf.columns:
                 communes_gdf.rename(columns={'INSEE_COM': 'codgeo'}, inplace=True)
            elif 'code' in communes_gdf.columns:
                 communes_gdf.rename(columns={'code': 'codgeo'}, inplace=True)
        
        # Merge Population
        pop_cfg = config['sources']['population']
        pop_path = CACHE_DIR / pop_cfg['local_name']
        if pop_path.exists():
            pop_df = load_dataset(pop_path, pop_cfg)
            # Expected: codgeo, population
            # Check columns
            pop_col = next((c for c in pop_df.columns if 'pop' in c.lower()), None)
            geo_col = next((c for c in pop_df.columns if 'codgeo' in c.lower() or 'com' in c.lower()), None)
            
            if pop_col and geo_col:
                pop_df = pop_df[[geo_col, pop_col]].rename(columns={geo_col: 'codgeo', pop_col: 'population'})
                pop_df['codgeo'] = pop_df['codgeo'].astype(str).str.zfill(5)
                # pop_df.set_index('codgeo', inplace=True) # Don't set index, use merge
                communes_gdf = communes_gdf.merge(pop_df, on='codgeo', how='left')
                communes_gdf['population'] = communes_gdf['population'].fillna(0).astype(int)
            else:
                logging.warning("Could not find population column (ending in _pop)")
        
        # Merge BMO (Top Metiers + met)
        if not bmo_df.empty:
            # bmo_df has codgeo as index
            bmo_df = bmo_df.reset_index()
            # Merge top_metiers and met if available
            cols_to_merge = ['codgeo', 'metiers_offres_top5']
            if 'metiers_offres_diff' in bmo_df.columns:
                cols_to_merge.append('metiers_offres_diff')
            communes_gdf = communes_gdf.merge(bmo_df[cols_to_merge], on='codgeo', how='left')
            
        # Merge Population Active
        if not pop_active_df.empty:
            pop_active_df = pop_active_df.reset_index()
            communes_gdf = communes_gdf.merge(pop_active_df[['codgeo', 'pop_active', 'pop_employes', 'pop_chomeurs']], on='codgeo', how='left')
            
        # Merge LOVAC
        if not lovac_df.empty:
            communes_gdf = communes_gdf.merge(lovac_df, on='codgeo', how='left')
        
        # Merge RPLS
        if not rpls_df.empty:
            communes_gdf = communes_gdf.merge(rpls_df, on='codgeo', how='left')
            
        # Merge CAF
        caf_cfg = config['sources']['caf']
        caf_path = CACHE_DIR / caf_cfg['local_name']
        if caf_path.exists():
            caf_df = load_dataset(caf_path, caf_cfg)
            caf_df = process_caf(caf_df, logger)
            communes_gdf = communes_gdf.merge(caf_df, on='codgeo', how='left')
            
        # Merge Education
        edu_cfg = config['sources']['education_effectifs']
        edu_path = CACHE_DIR / edu_cfg['local_name']
        
        annuaire_cfg = config['sources']['education_annuaire']
        annuaire_path = CACHE_DIR / annuaire_cfg['local_name']
        
        if edu_path.exists() and annuaire_path.exists():
            edu_df = load_dataset(edu_path, edu_cfg)
            annuaire_df = load_dataset(annuaire_path, annuaire_cfg)
            edu_df = process_education(edu_df, annuaire_df, logger)
            communes_gdf = communes_gdf.merge(edu_df, on='codgeo', how='left')
        
        # Merge Inclusion
        incl_cfg = config['sources']['services_inclusion']
        incl_path = CACHE_DIR / incl_cfg['local_name']
        if incl_path.exists():
            incl_df = load_dataset(incl_path, incl_cfg)
            incl_df = process_inclusion(incl_df, logger)
            communes_gdf = communes_gdf.merge(incl_df, on='codgeo', how='left')
            
        # Merge Associations
        asso_cfg = config['sources']['associations']
        asso_path = CACHE_DIR / asso_cfg['local_name']
        if asso_path.exists():
            asso_df = load_dataset(asso_path, asso_cfg)
            asso_df = process_associations(asso_df, logger)
            communes_gdf = communes_gdf.merge(asso_df, on='codgeo', how='left')

        # --- Aggregation & Output ---
        
        # 1. ODIS Communes
        # Ensure geometry is valid
        communes_gdf = communes_gdf[communes_gdf.geometry.notnull()]
        
        # Simplify geometry for performance (optional but recommended for app)
        # communes_gdf.geometry = communes_gdf.geometry.simplify(0.001) 
        
        # Fill NaNs for numeric columns
        # Numeric columns to sum
        numeric_cols = ['population', 'log_soc_total', 'log_soc_inoccupes', 
                        'count_maternelle', 'count_elementaire', 'ecoles_ct',
                        'lien_social_count', 'svc_incl_count', 'pop_active', 'pop_employes', 'pop_chomeurs', 'metiers_offres_diff']
        # Add others as needed
        for col in numeric_cols:
            if col in communes_gdf.columns:
                communes_gdf[col] = communes_gdf[col].fillna(0)
                
        # --- Bassins de Vie (Join & Calculate pop_be) ---
        logging.info("Processing Bassins de Vie (Join & Metrics)...")
        bv_cfg = config['sources']['bassins_de_vie']
        bv_path = CACHE_DIR / bv_cfg['archive_file'] # Extracted file
        
        if bv_path.exists():
            bv_df = load_dataset(bv_path, bv_cfg)
            # Expected cols: CODGEO, BV2022
            bv_df = bv_df.rename(columns={
                'Code géographique': 'CODGEO',
                'Bassin de vie 2022': 'BV2022'
            })
            
            if 'CODGEO' in bv_df.columns and 'BV2022' in bv_df.columns:
                # Ensure CODGEO is string and zfilled
                bv_df['CODGEO'] = bv_df['CODGEO'].astype(str).str.zfill(5)
                
                bv_mapping = bv_df[['CODGEO', 'BV2022']].set_index('CODGEO')
                communes_gdf = communes_gdf.join(bv_mapping, on='codgeo', how='left')
                
                # Cleanup Communes
                if 'commune' in communes_gdf.columns:
                    communes_gdf.drop(columns=['commune'], inplace=True)
                    
                # Reorder columns to have BV2022 early
                cols = communes_gdf.columns.tolist()
                if 'BV2022' in cols:
                    cols.remove('BV2022')
                    # Insert after 'departement' if exists, else after 'codgeo'
                    insert_idx = cols.index('departement') + 1 if 'departement' in cols else 1
                    cols.insert(insert_idx, 'BV2022')
                    communes_gdf = communes_gdf[cols]
                    
                # Calculate pop_be (Population Bassin Emploi/Vie)
                # Sum population by BV2022
                pop_be_series = communes_gdf.groupby('BV2022')['population'].transform('sum')
                communes_gdf['pop_be'] = pop_be_series.fillna(0)
                
            else:
                logging.warning("Bassins de Vie file columns mismatch.")
        else:
            logging.warning("Bassins de Vie file not found.")

        # Derived Metrics
        if 'pop_be' not in communes_gdf.columns:
            logging.warning("pop_be could not be calculated (missing BV data). Using population as fallback.")
            communes_gdf['pop_be'] = communes_gdf['population']

            # If we have coords from FINESS join (not fully implemented in process_maternites yet), we use them
            # But process_maternites currently just returns drees_df. 
            # Let's assume for this step we rely on what we have. 
            # If process_maternites didn't fully join, we might skip this or use a placeholder.
            pass

        # --- Bassins de Vie (Geospatial Dissolve) ---
        logging.info("Processing Bassins de Vie (Dissolve)...")
        # We already joined BV2022 above
        
        if 'BV2022' in communes_gdf.columns:
             # Dissolve
            # Fix geometries before dissolve to avoid TopologyException
            communes_gdf['geometry'] = communes_gdf['geometry'].buffer(0)
            
            # We need to keep some aggregated metrics
            # For geometry: dissolve
            # For numeric: sum
            agg_dict = {col: 'sum' for col in numeric_cols if col in communes_gdf.columns}
            
            # Explicitly select columns to ensure they are present for dissolve
            # dissolve preserves columns in agg_dict if they are in the DF
            # But let's be sure
            
            # Filter out rows where BV2022 is null for dissolve
            bv_gdf = communes_gdf[communes_gdf['BV2022'].notnull()].dissolve(by='BV2022', aggfunc=agg_dict)
            bv_gdf.rename(columns={'population': 'population_bv'}, inplace=True)
        else:
             bv_gdf = gpd.GeoDataFrame()

        # --- Output Generation ---
        logging.info("Writing output files...")
        
        # 1. Communes
        communes_out = OUTPUT_DIR / "odis_communes.parquet"
        communes_gdf.to_parquet(communes_out)
        logger.log_step("output_communes", "CREATED", {"path": str(communes_out), "rows": len(communes_gdf)})
        
        # 2. Bassins de Vie
        if not bv_gdf.empty:
            bv_out = OUTPUT_DIR / "odis_bassins_de_vie.parquet"
            bv_gdf.to_parquet(bv_out)
            logger.log_step("output_bv", "CREATED", {"path": str(bv_out), "rows": len(bv_gdf)})
        
        # 3. Generate POIs
        generate_pois(config, logger)
        
        # 4. Generate Referentiels
        generate_referentiels(config, logger)
        
        logger.log_step("process_all", "COMPLETED")

    logging.info("Pipeline run finished.")

if __name__ == "__main__":
    main()
