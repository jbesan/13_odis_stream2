import os
import requests
import logging
import pandas as pd
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
        """Fetches table preview from Odace API."""
        url = f"{self.api_url}/api/data/preview/silver/{table_name}"
        payload = {
            "limit": limit,
            "filters": None,
            "sort_by": None,
            "sort_order": "asc"
        }
        
        try:
            logging.info(f"OdaceClient: Fetching {table_name}...")
            response = requests.post(url, headers=self.headers, json=payload, timeout=60)
            response.raise_for_status()
            return response.json()
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
