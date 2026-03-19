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

    def _get_preview(self, table_name: str, limit: int = 50000, ttl_seconds: int = 7 * 24 * 60 * 60, sort_by: str = None) -> Optional[Dict[str, Any]]:
        """Fetches table data from Odace /api/data/query with robust SQL pagination."""
        cache_file = CACHE_DIR / f"odace_{table_name}.json"
        
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

        # Implementation of chunked fetching using SQL OFFSET/LIMIT
        url = f"{self.api_url}/api/data/query"
        all_data = []
        chunk_size = 50000
        offset = 0
        
        try:
            logging.info(f"OdaceClient: Starting chunked fetch for silver_{table_name} via SQL...")
            order_clause = f" ORDER BY {sort_by}" if sort_by else ""
            
            while True:
                fetch_limit = min(chunk_size, limit - len(all_data)) if limit else chunk_size
                sql = f"SELECT * FROM silver_{table_name}{order_clause} LIMIT {fetch_limit} OFFSET {offset}"
                
                payload = {
                    "sql": sql,
                    "limit": fetch_limit
                }
                
                response = requests.post(url, headers=self.headers, json=payload, timeout=60)
                response.raise_for_status()
                chunk_resp = response.json()
                
                chunk_data = chunk_resp.get("data", [])
                all_data.extend(chunk_data)
                
                logging.info(f"OdaceClient: Fetched {len(all_data)} rows...")
                
                if not chunk_data or len(chunk_data) < fetch_limit or (limit and len(all_data) >= limit):
                    break
                    
                offset += chunk_size
                time.sleep(0.5)

            full_resp = {
                "data": all_data,
                "columns": chunk_resp.get("columns", []),
                "total_rows": len(all_data)
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
            error_msg = f"Failed to fetch {table_name} via query: {str(e)}"
            logging.error(error_msg)
            
            # Fallback to expired cache if available
            if cache_file.exists():
                logging.warning(f"OdaceClient: API failed for silver_{table_name}, falling back to existing cache ({cache_file.name})")
                try:
                    with open(cache_file, "r") as f:
                        return json.load(f)
                except:
                    pass

            if self.logger:
                self.logger.log_source(f"odace_{table_name}", "ERROR", error_msg)
            return None

    def fetch_dim_commune(self) -> pd.DataFrame:
        """Fetches dim_commune and returns as DataFrame (1 year TTL)."""
        resp = self._get_preview("dim_commune", ttl_seconds=365 * 24 * 60 * 60, sort_by="commune_sk")
        if not resp or not resp.get("data"):
            return pd.DataFrame()
        
        df = pd.DataFrame(resp["data"])
        return df

    def fetch_dim_gare(self) -> pd.DataFrame:
        """Fetches dim_gare and returns as DataFrame (1 year TTL)."""
        resp = self._get_preview("dim_gare", ttl_seconds=365 * 24 * 60 * 60, sort_by="gare_sk")
        if not resp or not resp.get("data"):
            return pd.DataFrame()
            
        df = pd.DataFrame(resp["data"])
        return df

    def fetch_fact_loyer_annonce(self, limit: int = 200000) -> pd.DataFrame:
        """Fetches fact_loyer_annonce and returns as DataFrame (1 month TTL)."""
        resp = self._get_preview("fact_loyer_annonce", limit=limit, ttl_seconds=30 * 24 * 60 * 60, sort_by="commune_sk")
        if not resp or not resp.get("data"):
            return pd.DataFrame()
            
        df = pd.DataFrame(resp["data"])
        return df

    def fetch_ref_logement_profil(self) -> pd.DataFrame:
        """Fetches ref_logement_profil and returns as DataFrame."""
        resp = self._get_preview("ref_logement_profil", sort_by="logement_profil_sk")
        if not resp or not resp.get("data"):
            return pd.DataFrame()
            
        df = pd.DataFrame(resp["data"])
        return df



def get_odace_client(logger: Optional[PipelineLogger] = None) -> OdaceClient:
    return OdaceClient(logger)
