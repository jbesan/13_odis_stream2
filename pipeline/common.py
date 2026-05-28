import os
import yaml
import pandas as pd
import geopandas as gpd
import logging
import json
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from dotenv import load_dotenv

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

# Constants
CONFIG_FILE = "pipeline/sources.yaml"
CACHE_DIR = Path("pipeline/cache/raw")
CLEAN_DIR = Path("pipeline/cache/clean")
OUTPUT_DIR = Path("pipeline/cache/output")
STATUS_FILE = Path("pipeline/status.json")

# Configure logging centrally
def configure_logging(level=logging.INFO):
    logging.basicConfig(level=level, format='%(asctime)s - %(levelname)s - %(message)s')
    # Silence extremely verbose third-party loggers
    for logger_name in ["fastparquet", "requests", "urllib3", "pyarrow", "geopandas", "fiona", "shapely"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

configure_logging()

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
        z.extract(target_file, path=zip_path.parent)
    return zip_path.parent / target_file

def load_dataset(path: Path, config: Dict[str, Any], **kwargs) -> pd.DataFrame:
    """Loads a dataset based on its extension or config."""
    fmt = config.get('format', '').lower()
    
    # Prioritize config format
    encoding = config.get('encoding', None)
    if fmt == 'parquet':
        return pd.read_parquet(path, engine='fastparquet', **kwargs)
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
        excel_kwargs = {k: v for k, v in kwargs.items() if k not in ['sheet_name', 'header', 'skiprows']}
        return pd.read_excel(path, sheet_name=sheet, engine='calamine', header=header, skiprows=skiprows, **excel_kwargs)
    
    # Fallback to extension
    if path.suffix == '.parquet':
        return pd.read_parquet(path, engine='fastparquet', **kwargs)
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


def is_cache_valid(source_name: str, source_cfg: Dict[str, Any]) -> bool:
    """Checks if the local cache file is still valid based on ttl_days configuration."""
    local_name = source_cfg.get('local_name')
    path_str = source_cfg.get('path')
    
    if path_str:
        local_path = Path(path_str)
    elif local_name:
        local_path = CACHE_DIR / local_name
    else:
        return False
        
    if not local_path.exists():
        return False
        
    ttl_days = source_cfg.get('ttl_days', 30)
    mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
    age_days = (datetime.now() - mtime).days
    return age_days < ttl_days


def fetch_remote_metadata_datagouv(resource_id: str) -> Optional[Dict[str, Any]]:
    """Fetches resource metadata directly from the data.gouv.fr stable redirect URL."""
    url = f"https://www.data.gouv.fr/api/1/datasets/r/{resource_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        import requests
        from email.utils import parsedate_to_datetime
        logging.info(f"📡 Resolving data.gouv.fr stable redirect for resource: {resource_id}")
        # Use stream=True to only load response headers without downloading the body
        response = requests.get(url, headers=headers, stream=True, allow_redirects=True, timeout=15)
        response.raise_for_status()
        
        last_modified_header = response.headers.get("Last-Modified")
        logging.info(f"Metadata header for {resource_id}: Last-Modified = {last_modified_header}")
        
        # Close the streaming connection immediately
        response.close()
        
        if last_modified_header:
            dt = parsedate_to_datetime(last_modified_header)
            return {
                "last_modified": dt.isoformat(),
                "url": response.url
            }
        else:
            logging.warning(f"⚠️ No 'Last-Modified' header found in response for resource {resource_id}")
            return None
    except Exception as e:
        logging.warning(f"⚠️ Failed to query metadata for resource {resource_id}: {e}")
        return None


def validate_dataset_contract(df: pd.DataFrame, source_name: str, source_cfg: Dict[str, Any]) -> bool:
    """Validates the dataset against config-defined schema contracts (used_columns)."""
    used_cols = source_cfg.get('used_columns')
    
    # 1. Size Check
    if df is None or len(df) == 0:
        logging.warning(f"⚠️ [CONTRACT VALIDATION FAILED] Dataset '{source_name}' is empty or None.")
        return False
        
    # 2. Schema Check
    if used_cols:
        missing_cols = []
        for col in used_cols:
            # Check normal columns or index names
            if col not in df.columns and col != df.index.name and (not isinstance(df.index, pd.MultiIndex) or col not in df.index.names):
                missing_cols.append(col)
                
        if missing_cols:
            logging.warning(f"⚠️ [CONTRACT VALIDATION FAILED] Dataset '{source_name}' is missing columns: {missing_cols}")
            return False
            
    # 3. Non-null primary keys (e.g. codgeo/INSEE_COM)
    pk_cols = [c for c in ['codgeo', 'INSEE_COM', 'Code commune INSEE', 'code_commune'] if c in df.columns]
    for pk in pk_cols:
        null_count = df[pk].isna().sum()
        if null_count > 0:
            pct_null = (null_count / len(df)) * 100
            if pct_null > 5.0:
                logging.warning(f"⚠️ [CONTRACT VALIDATION FAILED] Dataset '{source_name}' has high null rate ({pct_null:.1f}%) in primary identifier '{pk}'.")
                return False
                
    logging.info(f"✅ [CONTRACT PASSED] Dataset '{source_name}' matches schema contract.")
    return True


def atomic_swap(src_path: Path, dst_path: Path):
    """Atomically swaps a staging file to the active cache path."""
    import os
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    if not src_path.exists():
        raise FileNotFoundError(f"Staging source file not found: {src_path}")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        try:
            os.remove(dst_path)
        except Exception as e:
            logging.warning(f"Failed to delete existing target file {dst_path}: {e}")
    os.replace(src_path, dst_path)
    logging.info(f"🔄 Swapped staging {src_path.name} -> active {dst_path.name}")


def get_ingest_paths(source_name: str, config: Dict[str, Any]) -> Tuple[Optional[Path], Path, bool]:
    """
    Resolves ingestion paths for a source.
    Returns (input_path, output_path, is_staging).
    If a staging raw file exists, returns staging raw path, staging clean path, and True.
    Otherwise returns active raw path, active clean path, and False.
    """
    source_cfg = config['sources'].get(source_name)
    if not source_cfg:
        source_cfg = config.get('local_files', {}).get(source_name)
        if not source_cfg:
            raise ValueError(f"Source config not found for '{source_name}'")
            
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
        
    active_clean = CLEAN_DIR / f"{source_name}.parquet"
    staging_clean = CLEAN_DIR / f"staging_{source_name}.parquet"
    
    if staging_raw and staging_raw.exists():
        return staging_raw, staging_clean, True
    return active_raw, active_clean, False


def finalize_ingest(source_name: str, config: Dict[str, Any], is_staging: bool, staging_raw: Optional[Path], staging_clean: Path, active_raw: Optional[Path], active_clean: Path) -> bool:
    """
    Validates clean staging file and swaps it to the active path if valid.
    Discards staging files and retains existing cache on validation failure.
    """
    if not is_staging:
        return True
        
    try:
        if not staging_clean.exists():
            logging.warning(f"⚠️ Staging clean file not found for {source_name}: {staging_clean}")
            return False
            
        # Load staging clean parquet to validate
        df = pd.read_parquet(staging_clean, engine='fastparquet')
        source_cfg = config['sources'].get(source_name) or config.get('local_files', {}).get(source_name)
        
        if validate_dataset_contract(df, source_name, source_cfg):
            # Atomic swaps!
            atomic_swap(staging_clean, active_clean)
            if staging_raw and active_raw and staging_raw.exists():
                atomic_swap(staging_raw, active_raw)
            logging.info(f"✅ Ingested and verified '{source_name}' successfully.")
            return True
        else:
            raise ValueError("Data contract validation failed.")
    except Exception as e:
        logging.warning(f"⚠️ [INGEST WARNING] Dataset '{source_name}' failed validation contract: {e}")
        # Discard staging files
        if staging_clean.exists():
            try: os.remove(staging_clean)
            except: pass
        if staging_raw and staging_raw.exists():
            try: os.remove(staging_raw)
            except: pass
        logging.warning(f"⚠️ Reverted to last known good cache for '{source_name}'.")
        return False

