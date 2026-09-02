import os
import requests
import logging
import pandas as pd
import time
from pathlib import Path
from typing import Optional, Dict, Any
from pipeline.common import PipelineLogger, CACHE_DIR


class OdaceClient:
    def __init__(self, logger: Optional[PipelineLogger] = None):
        self.api_key = os.environ.get("ODACE_API_KEY")
        self.api_url = os.environ.get("ODACE_API_URL")
        self.logger = logger

        if not self.api_key or not self.api_url:
            raise ValueError(
                "ODACE_API_KEY and ODACE_API_URL must be set in environment variables."
            )

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Central configuration lookup for TTL mapping
        from pipeline.common import CONFIG_FILE, load_config

        try:
            self.config = load_config(CONFIG_FILE)
        except Exception as e:
            logging.warning(
                f"OdaceClient: Failed to load config from {CONFIG_FILE}: {e}"
            )
            self.config = {}

    def _fetch_table_export(self, table_name: str, ttl_seconds: int) -> pd.DataFrame:
        """Downloads table data via the new export endpoint and reads the Parquet file."""
        cache_file = CACHE_DIR / f"odace_{table_name}.parquet"

        # Check cache
        if cache_file.exists():
            file_age = time.time() - cache_file.stat().st_mtime
            if file_age < ttl_seconds:
                try:
                    logging.info(
                        f"OdaceClient: Loading {table_name} from Parquet cache (age: {file_age / 3600:.1f}h, TTL: {ttl_seconds / (24 * 3600):.1f} days)"
                    )
                    return pd.read_parquet(cache_file)
                except Exception as e:
                    logging.warning(
                        f"OdaceClient: Failed to read Parquet cache for {table_name}: {e}"
                    )

        # Download from export endpoint
        url = f"{self.api_url}/api/data/export/{table_name}?format=parquet"
        try:
            logging.info(f"OdaceClient: Downloading {table_name} via export API...")
            response = requests.get(url, headers=self.headers, stream=True, timeout=300)
            response.raise_for_status()

            # Download and parse a staging file before atomically replacing the
            # last-known-good cache. A failed or truncated export therefore
            # cannot erase the usable Odace source snapshot.
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            staging_file = Path(f"{cache_file}.staging")
            with open(staging_file, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    f.write(chunk)

            downloaded = pd.read_parquet(staging_file)
            if downloaded.empty:
                raise ValueError(f"Odace export '{table_name}' is empty")
            os.replace(staging_file, cache_file)
            logging.info(f"OdaceClient: Cached {table_name} to {cache_file}")
            return downloaded

        except Exception as e:
            if "staging_file" in locals() and staging_file.exists():
                staging_file.unlink()
            error_msg = f"CRITICAL ERROR: Failed to export {table_name} from Odace API: {str(e)}"
            logging.error(error_msg)
            print(f"ERROR [OdaceClient]: {error_msg}")

            # Fallback to expired cache if available
            if cache_file.exists():
                logging.warning(
                    f"OdaceClient: API failed for {table_name}, falling back to expired Parquet cache ({cache_file.name})"
                )
                try:
                    return pd.read_parquet(cache_file)
                except Exception as cache_err:
                    logging.error(
                        f"OdaceClient: Expired cache read also failed for {table_name}: {cache_err}"
                    )

            if self.logger:
                self.logger.log_source(f"odace_{table_name}", "ERROR", error_msg)
            return pd.DataFrame()

    def fetch_dim_commune(self, ttl_days: int) -> pd.DataFrame:
        """Fetches dim_commune (falling back to dim_geo) with a caller-configured TTL."""
        ttl_seconds = ttl_days * 24 * 60 * 60
        try:
            df_geo = self._fetch_table_export("dim_geo", ttl_seconds=ttl_seconds)
            if not df_geo.empty:
                # Include both communes and arrondissements for Paris, Lyon, Marseille (PLM) compatibility
                df_commune = df_geo[
                    df_geo["geo_level"].isin(["commune", "arrondissement"])
                ].copy()
                if not df_commune.empty:
                    # Drop existing columns to avoid duplicate column names after rename
                    cols_to_drop = [
                        c
                        for c in ["commune_insee_code", "commune_label"]
                        if c in df_commune.columns
                    ]
                    if cols_to_drop:
                        df_commune = df_commune.drop(columns=cols_to_drop)
                    df_commune = df_commune.rename(
                        columns={
                            "geo_code": "commune_insee_code",
                            "geo_label": "commune_label",
                        }
                    )
                    logging.info(
                        f"OdaceClient: Successfully resolved dim_commune from dim_geo ({len(df_commune)} rows)."
                    )
                    return df_commune
        except Exception as e:
            logging.warning(
                f"OdaceClient: Failed to fetch dim_commune from dim_geo: {e}"
            )

        logging.warning("OdaceClient: Falling back to direct dim_commune fetch.")
        return self._fetch_table_export("dim_commune", ttl_seconds=ttl_seconds)

    def fetch_dim_gare(self, ttl_days: int) -> pd.DataFrame:
        """Fetches dim_gare with a caller-configured TTL."""
        return self._fetch_table_export("dim_gare", ttl_seconds=ttl_days * 24 * 60 * 60)

    def fetch_fact_loyer_annonce(
        self, ttl_days: int, limit: int = 200000
    ) -> pd.DataFrame:
        """Fetches fact_loyer_annonce with a caller-configured TTL."""
        return self._fetch_table_export(
            "fact_loyer_annonce", ttl_seconds=ttl_days * 24 * 60 * 60
        )

    def fetch_ref_logement_profil(self, ttl_days: int) -> pd.DataFrame:
        """Fetches ref_logement_profil with a caller-configured TTL."""
        return self._fetch_table_export(
            "ref_logement_profil", ttl_seconds=ttl_days * 24 * 60 * 60
        )

    def fetch_table(
        self,
        table_name: str,
        limit: int = 150000,
        ttl_days: Optional[int] = None,
        sort_by: str = None,
    ) -> pd.DataFrame:
        """Generic fetch for any silver table from Odace API."""
        if ttl_days is None:
            # Resolve the table policy exclusively from sources.yaml.
            if self.config and "sources" in self.config:
                for name, source_cfg in self.config["sources"].items():
                    if source_cfg.get("odace_table") == table_name:
                        ttl_days = source_cfg["ttl_days"]
                        logging.info(
                            f"OdaceClient: Resolved TTL for '{table_name}' from sources configuration: {ttl_days} days"
                        )
                        break
        if ttl_days is None:
            raise ValueError(f"No TTL configured for Odace table '{table_name}'")
        if table_name == "dim_commune":
            return self.fetch_dim_commune(ttl_days)

        return self._fetch_table_export(table_name, ttl_seconds=ttl_days * 24 * 60 * 60)

    def execute_query(
        self, sql: str, cache_name: str, ttl_seconds: int
    ) -> pd.DataFrame:
        """Executes a custom SQL query via the Odace query API (with pagination) and caches the result."""
        cache_file = CACHE_DIR / f"{cache_name}.parquet"

        # Check cache
        if cache_file.exists():
            file_age = time.time() - cache_file.stat().st_mtime
            if file_age < ttl_seconds:
                try:
                    logging.info(
                        f"OdaceClient: Loading query '{cache_name}' from Parquet cache (age: {file_age / 3600:.1f}h, TTL: {ttl_seconds / (24 * 3600):.1f} days)"
                    )
                    return pd.read_parquet(cache_file)
                except Exception as e:
                    logging.warning(
                        f"OdaceClient: Failed to read Parquet cache for query '{cache_name}': {e}"
                    )

        # Run query with pagination
        url = f"{self.api_url}/api/data/query"
        try:
            logging.info(
                f"OdaceClient: Running custom query for '{cache_name}' via query API..."
            )
            all_data = []
            columns = []
            offset = 0
            has_more = True

            while has_more:
                payload = {"sql": sql, "limit": 10000, "offset": offset}
                response = requests.post(
                    url, headers=self.headers, json=payload, timeout=60
                )
                response.raise_for_status()

                result = response.json()
                if not columns:
                    columns = result.get("columns", [])

                page_data = result.get("data", [])
                all_data.extend(page_data)

                has_more = result.get("has_more", False)
                row_count = len(page_data)
                offset += row_count

                logging.info(
                    f"OdaceClient: Fetched page (offset={offset - row_count}, rows={row_count}, has_more={has_more})"
                )
                if row_count == 0:
                    break

            df = pd.DataFrame(all_data, columns=columns)

            # Save to cache parquet file
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            df.to_parquet(cache_file)

            logging.info(
                f"OdaceClient: Cached query '{cache_name}' to {cache_file} (total rows: {len(df)})"
            )
            return df

        except Exception as e:
            error_msg = f"Failed to execute query for '{cache_name}' on Odace: {str(e)}"
            logging.error(error_msg)

            # Fallback to expired cache if available
            if cache_file.exists():
                logging.warning(
                    f"OdaceClient: Query failed for '{cache_name}', falling back to expired Parquet cache ({cache_file.name})"
                )
                try:
                    return pd.read_parquet(cache_file)
                except Exception as exc:
                    logging.warning(
                        f"OdaceClient: Failed to read expired fallback cache '{cache_file}': {exc}"
                    )

            if self.logger:
                self.logger.log_source(f"odace_query_{cache_name}", "ERROR", error_msg)
            return pd.DataFrame()

    def fetch_silver_table_detail(self, table_name: str) -> Optional[Dict[str, Any]]:
        """Fetches table metadata and catalog info from GET /api/data/catalog/silver/{table_name}."""
        if not self.api_url or not self.api_key:
            return None

        url = f"{self.api_url}/api/data/catalog/silver/{table_name}"
        try:
            logging.info(f"OdaceClient: Fetching catalog detail for {table_name}...")
            response = requests.get(url, headers=self.headers, timeout=15)
            if response.status_code == 200:
                return response.json()
            else:
                logging.warning(
                    f"OdaceClient: Catalog detail returned HTTP {response.status_code} for {table_name}"
                )
                return None
        except Exception as e:
            logging.warning(
                f"OdaceClient: Failed to fetch catalog detail for {table_name}: {e}"
            )
            return None


def get_odace_client(logger: Optional[PipelineLogger] = None) -> OdaceClient:
    return OdaceClient(logger)
