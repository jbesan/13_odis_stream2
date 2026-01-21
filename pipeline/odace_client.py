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

        # Implementation of chunked fetching to avoid 500 errors on large datasets
        url = f"{self.api_url}/api/data/preview/silver/{table_name}"
        all_data = []
        chunk_size = 50000
        offset = 0
        total_rows = None
        
        try:
            logging.info(f"OdaceClient: Starting chunked fetch for {table_name}...")
            
            while True:
                payload = {
                    "limit": min(chunk_size, limit - len(all_data)) if limit else chunk_size,
                    "offset": offset,
                    "filters": None,
                    "sort_by": None,
                    "sort_order": "asc"
                }
                
                response = requests.post(url, headers=self.headers, json=payload, timeout=60)
                response.raise_for_status()
                chunk_resp = response.json()
                
                chunk_data = chunk_resp.get("data", [])
                all_data.extend(chunk_data)
                
                if total_rows is None:
                    total_rows = chunk_resp.get("total_rows", 0)
                    logging.info(f"OdaceClient: {table_name} total rows: {total_rows}")
                
                logging.info(f"OdaceClient: Fetched {len(all_data)}/{min(limit, total_rows) if limit else total_rows} rows...")
                
                if not chunk_data or (limit and len(all_data) >= limit) or (total_rows and len(all_data) >= total_rows):
                    break
                    
                offset += chunk_size
                time.sleep(0.5) # Subtle throttling

            full_resp = {
                "data": all_data,
                "columns": chunk_resp.get("columns", []),
                "total_rows": total_rows
            }
            
            # Save to cache
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with open(cache_file, "w") as f:
                    json.dump(full_resp, f)
                logging.info(f"OdaceClient: Cached {table_name} to {cache_file} ({len(all_data)} rows)")
            except Exception as e:
                logging.warning(f"OdaceClient: Failed to write cache for {table_name}: {e}")
                
            return full_resp
            
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

    def fetch_fact_loyer_annonce(self, limit: int = 200000) -> pd.DataFrame:
        """Fetches fact_loyer_annonce and returns as DataFrame."""
        resp = self._get_preview("fact_loyer_annonce", limit=limit)
        if not resp or not resp.get("data"):
            return pd.DataFrame()
            
        df = pd.DataFrame(resp["data"])
        # Columns: commune_sk, logement_profil_sk, loyer_m2_moy, score_qualite, maille_observation...
        return df

    def fetch_ref_logement_profil(self) -> pd.DataFrame:
        """Fetches ref_logement_profil and returns as DataFrame."""
        resp = self._get_preview("ref_logement_profil")
        if not resp or not resp.get("data"):
            return pd.DataFrame()
            
        df = pd.DataFrame(resp["data"])
        # Columns: logement_profil_sk, logement_type, typologie, annee...
        return df



def get_odace_client(logger: Optional[PipelineLogger] = None) -> OdaceClient:
    return OdaceClient(logger)
