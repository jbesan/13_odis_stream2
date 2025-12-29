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

    def _get_preview(self, table_name: str, limit: int = 50000) -> Optional[Dict[str, Any]]:
        """Fetches table preview from Odace API with local caching (1 week TTL)."""
        cache_file = CACHE_DIR / f"odace_{table_name}.json"
        ttl_seconds = 7 * 24 * 60 * 60 # 1 week
        
        # Check cache
        if cache_file.exists():
            file_age = time.time() - cache_file.stat().st_mtime
            if file_age < ttl_seconds:
                try:
                    logging.info(f"OdaceClient: Loading {table_name} from cache (age: {file_age/3600:.1f}h)")
                    with open(cache_file, "r") as f:
                        return json.load(f)
                except Exception as e:
                    logging.warning(f"OdaceClient: Failed to read cache for {table_name}: {e}")

        url = f"{self.api_url}/api/data/preview/silver/{table_name}"
        payload = {
            "limit": limit,
            "filters": None,
            "sort_by": None,
            "sort_order": "asc"
        }
        
        try:
            logging.info(f"OdaceClient: Fetching {table_name} from API...")
            response = requests.post(url, headers=self.headers, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            
            # Save to cache
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with open(cache_file, "w") as f:
                    json.dump(data, f)
                logging.info(f"OdaceClient: Cached {table_name} to {cache_file}")
            except Exception as e:
                logging.warning(f"OdaceClient: Failed to write cache for {table_name}: {e}")
                
            return data
        except Exception as e:
            error_msg = f"Failed to fetch {table_name}: {str(e)}"
            logging.error(error_msg)
            if self.logger:
                self.logger.log_source(f"odace_{table_name}", "ERROR", error_msg)
            return None

    def fetch_dim_commune(self) -> pd.DataFrame:
        """Fetches dim_commune and returns as DataFrame."""
        resp = self._get_preview("dim_commune")
        if not resp or not resp.get("data"):
            return pd.DataFrame()
        
        df = pd.DataFrame(resp["data"])
        # Columns: commune_sk, commune_insee_code, commune_label, departement_code, region_code
        return df

    def fetch_dim_gare(self) -> pd.DataFrame:
        """Fetches dim_gare and returns as DataFrame."""
        resp = self._get_preview("dim_gare")
        if not resp or not resp.get("data"):
            return pd.DataFrame()
            
        df = pd.DataFrame(resp["data"])
        # Columns: gare_sk, commune_sk, gare_code, gare_label...
        return df



def get_odace_client(logger: Optional[PipelineLogger] = None) -> OdaceClient:
    return OdaceClient(logger)
