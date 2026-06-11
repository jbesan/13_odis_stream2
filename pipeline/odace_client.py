import os
import requests
import logging
import pandas as pd
import json
import time
from typing import Optional, Dict, Any
from pathlib import Path
from pipeline.common import PipelineLogger, CACHE_DIR

class OdaceClient:
    def __init__(self, logger: Optional[PipelineLogger] = None):
        self.api_key = os.environ.get("ODACE_API_KEY")
        self.api_url = os.environ.get("ODACE_API_URL")
        self.logger = logger
        
        if not self.api_key or not self.api_url:
            raise ValueError("ODACE_API_KEY and ODACE_API_URL must be set in environment variables.")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def _fetch_table_export(self, table_name: str, ttl_seconds: int = 30 * 24 * 60 * 60) -> pd.DataFrame:
        """Downloads table data via the new export endpoint and reads the Parquet file."""
        cache_file = CACHE_DIR / f"odace_{table_name}.parquet"
        
        # Check cache
        if cache_file.exists():
            file_age = time.time() - cache_file.stat().st_mtime
            if file_age < ttl_seconds:
                try:
                    logging.info(f"OdaceClient: Loading {table_name} from Parquet cache (age: {file_age/3600:.1f}h, TTL: {ttl_seconds/(24*3600):.1f} days)")
                    return pd.read_parquet(cache_file, engine='fastparquet')
                except Exception as e:
                    logging.warning(f"OdaceClient: Failed to read Parquet cache for {table_name}: {e}")
                    
        # Download from export endpoint
        url = f"{self.api_url}/api/data/export/{table_name}?format=parquet"
        try:
            logging.info(f"OdaceClient: Downloading {table_name} via export API...")
            response = requests.get(url, headers=self.headers, stream=True, timeout=120)
            response.raise_for_status()
            
            # Save directly to cache parquet file
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=65536):
                    f.write(chunk)
            
            logging.info(f"OdaceClient: Cached {table_name} to {cache_file}")
            return pd.read_parquet(cache_file, engine='fastparquet')
            
        except Exception as e:
            error_msg = f"Failed to export {table_name} from Odace: {str(e)}"
            logging.error(error_msg)
            
            # Fallback to expired cache if available
            if cache_file.exists():
                logging.warning(f"OdaceClient: API failed for {table_name}, falling back to expired Parquet cache ({cache_file.name})")
                try:
                    return pd.read_parquet(cache_file, engine='fastparquet')
                except:
                    pass
                    
            if self.logger:
                self.logger.log_source(f"odace_{table_name}", "ERROR", error_msg)
            return pd.DataFrame()

    def fetch_dim_commune(self) -> pd.DataFrame:
        """Fetches dim_commune and returns as DataFrame (1 year TTL)."""
        return self._fetch_table_export("dim_commune", ttl_seconds=365 * 24 * 60 * 60)

    def fetch_dim_gare(self) -> pd.DataFrame:
        """Fetches dim_gare and returns as DataFrame (1 year TTL)."""
        return self._fetch_table_export("dim_gare", ttl_seconds=365 * 24 * 60 * 60)

    def fetch_fact_loyer_annonce(self, limit: int = 200000) -> pd.DataFrame:
        """Fetches fact_loyer_annonce and returns as DataFrame (1 month TTL)."""
        return self._fetch_table_export("fact_loyer_annonce", ttl_seconds=30 * 24 * 60 * 60)

    def fetch_ref_logement_profil(self) -> pd.DataFrame:
        """Fetches ref_logement_profil and returns as DataFrame."""
        return self._fetch_table_export("ref_logement_profil", ttl_seconds=365 * 24 * 60 * 60)

    def fetch_table(self, table_name: str, limit: int = 150000, ttl_days: int = 30, sort_by: str = None) -> pd.DataFrame:
        """Generic fetch for any silver table from Odace API."""
        return self._fetch_table_export(table_name, ttl_seconds=ttl_days * 24 * 60 * 60)



def get_odace_client(logger: Optional[PipelineLogger] = None) -> OdaceClient:
    return OdaceClient(logger)
