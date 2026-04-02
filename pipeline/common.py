import os
import yaml
import pandas as pd
import geopandas as gpd
import logging
import json
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

# Constants
CONFIG_FILE = "pipeline/sources.yaml"
CACHE_DIR = Path("pipeline/cache/raw")
CLEAN_DIR = Path("pipeline/cache/clean")
OUTPUT_DIR = Path("pipeline/cache/output")
STATUS_FILE = Path("pipeline/status.json")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
