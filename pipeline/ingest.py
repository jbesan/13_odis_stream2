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
    PipelineLogger,
    load_config,
    load_dataset,
    extract_zip,
    CONFIG_FILE,
    CACHE_DIR,
    CLEAN_DIR,
    OUTPUT_DIR,
    STATUS_FILE,
    is_cache_valid,
    fetch_remote_metadata_datagouv,
    validate_dataset_contract,
    atomic_swap,
    get_ingest_paths,
    finalize_ingest,
)
from pipeline.odace_client import get_odace_client
from pipeline.ft_live_ingest import run_etl, get_token as get_ft_token
from pipeline.emplois_inclusion_ingest import run_ingestion as run_inclusion_ingest


def resolve_codgeo(insee_code, dept_code) -> str:
    """Helper to cleanly resolve 5-digit INSEE commune codes from raw inputs."""
    if pd.isna(insee_code):
        return ""
    insee_str = str(insee_code).split(".")[0].strip()
    if not insee_str or insee_str.lower() == "nan":
        return ""

    dept_str = (
        str(dept_code).split(".")[0].strip().zfill(2) if not pd.isna(dept_code) else ""
    )

    # Strip dept from the start of insee_str if present
    comm_str = insee_str
    if dept_str:
        if comm_str.startswith(dept_str):
            comm_str = comm_str[len(dept_str) :]
        elif dept_str.startswith("0") and comm_str.startswith(dept_str[1:]):
            comm_str = comm_str[len(dept_str) - 1 :]

    # Clean up comm_str and pad to 3 digits
    comm_str = comm_str.strip()
    if comm_str:
        comm_str = comm_str.zfill(3)
    else:
        comm_str = "000"

    return dept_str + comm_str


def fetch_source(
    name: str, source_cfg: Dict[str, Any], logger: PipelineLogger
) -> Optional[Path]:
    """Downloads and prepares a single source with caching, metadata checks, and staging."""
    import os

    if source_cfg.get("use_odace", False):
        logging.info(f"[Fetch] {name}: use_odace is enabled. Skipping remote download.")
        local_name = source_cfg.get("local_name")
        if local_name:
            return CACHE_DIR / local_name
        return None

    resource_id = source_cfg.get("datagouv_resource_id")
    url = source_cfg.get("url")
    if not url and resource_id:
        url = f"https://www.data.gouv.fr/api/1/datasets/r/{resource_id}"

    if not url:
        logger.log_source(name, "SKIPPED", "No URL provided")
        return None

    local_name = source_cfg["local_name"]
    local_path = CACHE_DIR / local_name

    # Create cache dir
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Check if cache is still valid
    ttl_days = source_cfg.get("ttl_days", 30)
    if is_cache_valid(name, source_cfg):
        logging.info(
            f"[Fetch] {name}: Local cache is valid (TTL={ttl_days} days). Skipping fetch."
        )
        logger.log_source(name, "CACHED", local_path)
        if source_cfg.get("format") == "zip" and "archive_file" in source_cfg:
            extracted_path = CACHE_DIR / source_cfg["archive_file"]
            return extracted_path
        return local_path

    # Cache is expired or missing. Check if we can do data.gouv.fr remote metadata validation.
    staging_local_path = CACHE_DIR / f"staging_{local_name}"
    download_url = url

    if local_path.exists() and resource_id:
        # We can query remote metadata to check if the remote resource is newer than our local cache.
        meta = fetch_remote_metadata_datagouv(resource_id)
        if meta and "last_modified" in meta:
            try:
                # Remove timezone offset or make naive to compare
                remote_mtime = datetime.fromisoformat(
                    meta["last_modified"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
                local_mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
                if remote_mtime <= local_mtime:
                    logging.info(
                        f"[Fetch] {name} is up-to-date on data.gouv.fr (remote: {remote_mtime}, local: {local_mtime}). Skipping download and resetting TTL."
                    )
                    # Touch local file to refresh its modification time (reset TTL window)
                    os.utime(local_path, None)
                    logger.log_source(name, "CACHED", local_path)
                    if (
                        source_cfg.get("format") == "zip"
                        and "archive_file" in source_cfg
                    ):
                        extracted_path = CACHE_DIR / source_cfg["archive_file"]
                        return extracted_path
                    return local_path
                else:
                    logging.info(
                        f"[Fetch] {name}: Remote version is newer (remote: {remote_mtime}, local: {local_mtime}). Downloading updated data..."
                    )
                    if meta.get("url"):
                        download_url = meta["url"]
            except Exception as e:
                logging.warning(f"⚠️ Error parsing metadata for {name}: {e}")

    # Fallback to reminder alert for other expired non-datagouv sources
    if local_path.exists() and not resource_id:
        mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        logging.info(
            f"🔔 [REMINDER] Cache for dataset '{name}' is {age_days} days old (TTL={ttl_days}). Please check manually if a new version is available on the provider's site."
        )

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
            verify_ssl = source_cfg.get("verify_ssl", True)
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            }
            response = requests.get(
                download_url, stream=True, verify=verify_ssl, headers=headers
            )
            response.raise_for_status()
            with open(staging_local_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.log_source(name, "STAGING_DOWNLOADED", staging_local_path)

        # List of sources that have corresponding clean steps (where staging swap is managed by run_clean_step_safely)
        CLEANED_SOURCES = {
            "communes",
            "services_inclusion",
            "structures_inclusion",
            "population",
            "population_active",
            "logement_vacant",
            "logement_social",
            "caf",
            "education_annuaire",
            "finess_national",
            "maternites",
            "associations",
            "political_nuance",
            "electoral_history",
            "housing_occupation",
            "education_effectifs",
            "bpe",
            "codes_postaux",
            "formations_annuaire",
            "loyers_apparts",
            "population_details",
            "nomenclature_waldec",
            "departements_ref",
            "france_travail_live",
            "inclusion_jobs",
            "mob_transports_pub",
            "jaccueille",
            "logement_social_delay",
            "sante_apl",
            "mob_durable_share",
            "ter_insecurite",
        }

        # Handle Zip Extraction in staging mode
        if source_cfg.get("format") == "zip" and "archive_file" in source_cfg:
            import zipfile

            extracted_file = source_cfg["archive_file"]
            staging_extracted_path = CACHE_DIR / f"staging_{extracted_file}"
            logging.info(
                f"[Fetch] {name}: Extracting zip member '{extracted_file}' to staging path..."
            )
            with zipfile.ZipFile(staging_local_path, "r") as z:
                with open(staging_extracted_path, "wb") as f_out:
                    f_out.write(z.read(extracted_file))

            if name not in CLEANED_SOURCES and not name.startswith("test_"):
                logging.info(
                    f"🔄 [Static Source] Swapping staging files to active for '{name}' immediately."
                )
                active_extracted_path = CACHE_DIR / extracted_file
                if active_extracted_path.exists():
                    try:
                        os.remove(active_extracted_path)
                    except:
                        pass
                os.rename(staging_extracted_path, active_extracted_path)

                if local_path.exists():
                    try:
                        os.remove(local_path)
                    except:
                        pass
                os.rename(staging_local_path, local_path)
                return active_extracted_path

            return staging_extracted_path

        if name not in CLEANED_SOURCES and not name.startswith("test_"):
            logging.info(
                f"🔄 [Static Source] Swapping staging raw file for '{name}' to active."
            )
            if local_path.exists():
                try:
                    os.remove(local_path)
                except:
                    pass
            os.rename(staging_local_path, local_path)
            return local_path

        return staging_local_path
    except Exception as e:
        logging.error(f"[Fetch] {name} Failed: {e}")
        logger.log_source(name, "FAILED", str(e))
        # If download failed, but we have an active cached file, return the active file so we can fall back to it
        if local_path.exists():
            logging.warning(
                f"⚠️ Failed to download updated version of {name}. Falling back to cached copy."
            )
            if source_cfg.get("format") == "zip" and "archive_file" in source_cfg:
                return CACHE_DIR / source_cfg["archive_file"]
            return local_path
        return None


def fetch_rome_referential(
    config: Dict[str, Any], logger: PipelineLogger
) -> Optional[Path]:
    """Fetches ROME referential from France Travail API with 1-year TTL, falls back to static JSON."""
    local_path = CACHE_DIR / "rome_referential_api.parquet"

    # 1. 1-Year TTL Check
    if local_path.exists():
        mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        if age_days < 365:
            logging.info(
                f"[ROME] Referential is {age_days} days old. Using cache (TTL=1 year)."
            )
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
        df = pd.DataFrame(data)
        if "code" in df.columns and "libelle" in df.columns:
            df = df[["code", "libelle"]].rename(columns={"libelle": "label"})
            df.to_parquet(local_path, engine="fastparquet")
            logging.info(
                f"✅ [ROME] Saved {len(df)} métiers to {local_path} from France Travail API"
            )
            logger.log_source("rome_referential", "FETCHED", str(local_path))
            return local_path
        else:
            raise ValueError(f"Unexpected API data format: {df.columns}")

    except Exception as e:
        logging.error(
            f"❌ [ROME] Failed to fetch referential from API: {e}. Attempting static fallback..."
        )
        logger.log_source("rome_referential_api_failure", "WARNING", str(e))

        # 4. Fallback to static zip source
        try:
            rome_cfg = config["sources"].get("rome")
            if not rome_cfg:
                raise ValueError("No static 'rome' configuration found in sources.yaml")

            # Download/Fetch the static zip source
            static_json_path = fetch_source("rome", rome_cfg, logger)
            if not static_json_path or not static_json_path.exists():
                raise FileNotFoundError(
                    f"Static ROME source not available at {static_json_path}"
                )

            # Parse the static JSON
            logging.info(
                f"[ROME] Parsing static JSON referential from {static_json_path}..."
            )
            df = pd.read_json(static_json_path)

            # Normalize column names just in case
            if "code" in df.columns and "libelle" in df.columns:
                df = df[["code", "libelle"]].rename(columns={"libelle": "label"})
                df.to_parquet(local_path, engine="fastparquet")
                logging.info(
                    f"✅ [ROME] Saved {len(df)} métiers to {local_path} from static ROME referential"
                )
                logger.log_source("rome_referential", "FETCHED", str(local_path))
                return local_path
            else:
                raise ValueError(f"Unexpected static JSON format: {df.columns}")
        except Exception as fallback_err:
            logging.error(f"❌ [ROME] Static fallback failed: {fallback_err}")
            logger.log_source(
                "rome_referential", "ERROR", f"API: {e}, Fallback: {fallback_err}"
            )

            # Final fallback: use existing expired cache if it exists
            if local_path.exists():
                logging.warning("[ROME] Returning expired cache referential.")
                return local_path
            return None


def clean_population_active(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Population Active and saves to parquet."""
    logger.log_step("clean_population_active", "STARTED")
    source = config["sources"]["population_active"]

    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_population_active")
            )
            if not df_odace.empty:
                # Map columns to look like the legacy dataset
                df_mapped = df_odace.rename(
                    columns={
                        "annee": "TIME_PERIOD",
                        "commune_insee_code": "GEO",
                        "code_pcs": "PCS",
                        "statut_emploi": "EMPSTA_ENQ",
                        "valeur": "OBS_VALUE",
                    }
                )

                # Re-use legacy logic directly on mapped DataFrame
                max_year = df_mapped["TIME_PERIOD"].max()
                actif_2022 = df_mapped[
                    (df_mapped.TIME_PERIOD == max_year)
                    & (df_mapped.PCS == "_T")
                    & (df_mapped.EMPSTA_ENQ.astype(str).isin(["1T2", "1"]))
                ].pivot_table(
                    index="GEO", columns="EMPSTA_ENQ", values="OBS_VALUE", aggfunc="sum"
                )

                # Align columns to strings
                actif_2022.columns = [str(c) for c in actif_2022.columns]

                if "1T2" in actif_2022.columns and "1" in actif_2022.columns:
                    actif_2022["pop_chomeurs"] = actif_2022["1T2"] - actif_2022["1"]
                    actif_2022.rename(
                        columns={"1T2": "pop_active", "1": "pop_employes"}, inplace=True
                    )
                    actif_2022 = actif_2022[
                        ["pop_active", "pop_employes", "pop_chomeurs"]
                    ]
                    actif_2022.index.name = "codgeo"
                    actif_2022 = actif_2022.reset_index()
                    actif_2022["codgeo"] = actif_2022["codgeo"].astype(str).str.zfill(5)

                    output_path = CLEAN_DIR / "population_active.parquet"
                    actif_2022.to_parquet(output_path, engine="fastparquet")
                    logger.log_step(
                        "clean_population_active",
                        "COMPLETED",
                        {
                            "path": str(output_path),
                            "rows": len(actif_2022),
                            "source": "odace",
                        },
                    )
                    return
                else:
                    logging.warning("Population Active pivot failed on Odace data.")
            else:
                logging.warning(
                    "Odace fetch returned empty data for population_active."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch population_active from Odace: {e}. Falling back to legacy."
            )

    path = CACHE_DIR / source["archive_file"]
    if not path.exists():
        logging.warning("Population Active file not found.")
        return

    actif = load_dataset(path, source)

    required_cols = [
        "TIME_PERIOD",
        "GEO_OBJECT",
        "PCS",
        "EMPSTA_ENQ",
        "GEO",
        "OBS_VALUE",
    ]
    if not all(col in actif.columns for col in required_cols):
        logging.warning("Population Active missing columns")
        return

    max_year = actif["TIME_PERIOD"].max()
    logging.info(f"Population Active: Using max year {max_year}")

    actif_2022 = actif[
        (actif.TIME_PERIOD == max_year)
        & (actif.GEO_OBJECT == "COM")
        & (actif.PCS == "_T")
        & (actif.EMPSTA_ENQ.isin(["1T2", "1"]))
    ].pivot_table(index="GEO", columns="EMPSTA_ENQ", values="OBS_VALUE", aggfunc="sum")

    if "1T2" in actif_2022.columns and "1" in actif_2022.columns:
        actif_2022["pop_chomeurs"] = actif_2022["1T2"] - actif_2022["1"]
        actif_2022.rename(
            columns={"1T2": "pop_active", "1": "pop_employes"}, inplace=True
        )
        actif_2022 = actif_2022[["pop_active", "pop_employes", "pop_chomeurs"]]
        actif_2022.index.name = "codgeo"
        actif_2022.reset_index(inplace=True)
        actif_2022["codgeo"] = actif_2022["codgeo"].astype(str).str.zfill(5)

        output_path = CLEAN_DIR / "population_active.parquet"
        actif_2022.to_parquet(output_path, engine="fastparquet")
        logger.log_step(
            "clean_population_active", "COMPLETED", {"path": str(output_path)}
        )
    else:
        logging.warning("Population Active pivot failed.")


def clean_lovac(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans LOVAC and saves to parquet."""
    logger.log_step("clean_lovac", "STARTED")
    source = config["sources"]["logement_vacant"]

    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_logement_vacant")
            )
            if not df_odace.empty:
                rename_dict = {
                    "commune_insee_code": "codgeo",
                    "nb_logements_vacants_2ans": "pp_vacant_plus_2ans_25",
                    "nb_logements_prives_total": "log_priv_total_24",
                }
                df_out = df_odace.rename(columns=rename_dict)
                for col in ["codgeo", "pp_vacant_plus_2ans_25", "log_priv_total_24"]:
                    if col not in df_out.columns:
                        df_out[col] = 0
                df_out = df_out[
                    ["codgeo", "pp_vacant_plus_2ans_25", "log_priv_total_24"]
                ].copy()
                df_out["codgeo"] = df_out["codgeo"].astype(str).str.zfill(5)
                df_out["pp_vacant_plus_2ans_25"] = pd.to_numeric(
                    df_out["pp_vacant_plus_2ans_25"], errors="coerce"
                ).fillna(0)
                df_out["log_priv_total_24"] = pd.to_numeric(
                    df_out["log_priv_total_24"], errors="coerce"
                ).fillna(0)

                output_path = CLEAN_DIR / "lovac.parquet"
                df_out.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_lovac", "COMPLETED", {"rows": len(df_out), "source": "odace"}
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for logement_vacant. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch logement_vacant from Odace: {e}. Falling back to legacy."
            )

    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    codgeo_col = next((c for c in df.columns if "CODGEO" in c), None)
    if codgeo_col:
        # Dynamic Year Detection
        import re

        # Find all years for vacancy data
        years = []
        year_pattern = re.compile(r"pp_vacant_plus_2ans_(\d+)")

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
            vac_col = f"pp_vacant_plus_2ans_{max_year}"

            # Total Column (Year N-1)
            target_total_year = max_year - 1
            total_col = f"pp_total_{target_total_year}"

            logging.info(
                f"LOVAC: Detected max year {max_year}. Using {vac_col} and {total_col}"
            )
        else:
            # Fallback
            logging.warning("LOVAC: Could not detect years. Using default 25/24.")
            vac_col = "pp_vacant_plus_2ans_25"
            total_col = "pp_total_24"

        # Allow fallback if dynamic total dict doesn't exist but static might?
        # Actually, let's just stick to the specific columns.

        if vac_col not in df.columns:
            # Try finding any valid vac col
            vac_col = next((c for c in df.columns if "vacant_plus_2ans" in c), None)

        if vac_col and vac_col in df.columns:
            df[vac_col] = pd.to_numeric(
                df[vac_col].replace("s", 0), errors="coerce"
            ).fillna(0)

            # Extract Total Housing
            if total_col in df.columns:
                df[total_col] = pd.to_numeric(
                    df[total_col].replace("s", 0), errors="coerce"
                ).fillna(0)
            else:
                logging.warning(
                    f"LOVAC: {total_col} not found in {df.columns}. Setting to 0."
                )
                df[total_col] = 0

            df_out = df[[codgeo_col, vac_col, total_col]].rename(
                columns={
                    codgeo_col: "codgeo",
                    vac_col: "pp_vacant_plus_2ans_25",  # Keep standardized internal name
                    total_col: "log_priv_total_24",  # Keep standardized internal name
                }
            )
            df_out["codgeo"] = df_out["codgeo"].astype(str)

            output_path = CLEAN_DIR / "lovac.parquet"
            df_out.to_parquet(output_path, engine="fastparquet")
            logger.log_step("clean_lovac", "COMPLETED", {"rows": len(df_out)})
        else:
            logging.warning(f"LOVAC: Vacancy column {vac_col} not found.")

    else:
        logging.warning("LOVAC: CODGEO not found.")


def clean_rpls(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans RPLS and saves to parquet."""
    logger.log_step("clean_rpls", "STARTED")
    source = config["sources"]["logement_social"]

    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_logement_social_rpls")
            )
            if not df_odace.empty:
                if "annee" in df_odace.columns:
                    max_year = df_odace["annee"].max()
                    logging.info(f"Odace RPLS: Filtering for max year {max_year}")
                    df_odace = df_odace[df_odace["annee"] == max_year]
                df_commune = client.fetch_dim_commune()
                if not df_commune.empty:
                    merged = df_odace.merge(
                        df_commune[["commune_sk", "commune_insee_code"]],
                        on="commune_sk",
                        how="inner",
                    )
                    rename_dict = {
                        "commune_insee_code": "codgeo",
                        "nb_logements_total_sociaux": "log_soc_total",
                        "nb_logements_vacants": "log_soc_inoccupes",
                    }
                    df_out = merged.rename(columns=rename_dict)
                    for col in ["codgeo", "log_soc_total", "log_soc_inoccupes"]:
                        if col not in df_out.columns:
                            df_out[col] = 0
                    df_out = df_out[
                        ["codgeo", "log_soc_total", "log_soc_inoccupes"]
                    ].copy()
                    df_out["codgeo"] = df_out["codgeo"].astype(str).str.zfill(5)
                    df_out["log_soc_total"] = pd.to_numeric(
                        df_out["log_soc_total"], errors="coerce"
                    ).fillna(0)
                    df_out["log_soc_inoccupes"] = pd.to_numeric(
                        df_out["log_soc_inoccupes"], errors="coerce"
                    ).fillna(0)

                    output_path = CLEAN_DIR / "rpls.parquet"
                    df_out.to_parquet(output_path, engine="fastparquet")
                    logger.log_step(
                        "clean_rpls",
                        "COMPLETED",
                        {"rows": len(df_out), "source": "odace"},
                    )
                    return
                else:
                    logging.warning(
                        "Odace dim_commune empty for RPLS. Falling back to legacy."
                    )
            else:
                logging.warning(
                    "Odace fetch returned empty data for RPLS. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch RPLS from Odace: {e}. Falling back to legacy."
            )

    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)
    df.columns = [str(c).strip() for c in df.columns]

    if "CODGEO" in df.columns:
        df["codgeo"] = df["CODGEO"].astype(str).str.zfill(5)
    elif "DEPCOM_ARM" in df.columns:
        df["codgeo"] = df["DEPCOM_ARM"].astype(str).str.zfill(5)
    elif "DEP" in df.columns and "COM" in df.columns:
        df["codgeo"] = df["DEP"].astype(str).str.zfill(2) + df["COM"].astype(
            str
        ).str.zfill(3)
    else:
        logging.warning("RPLS: No codgeo found")
        return

    cols = df.columns.tolist()
    total_col = next(
        (c for c in cols if "total" in c.lower() and "parc" in c.lower()), None
    )
    if not total_col:
        total_col = next(
            (c for c in cols if c in ["PARC_SOCIAL_NB", "NB_LOG_TOT", "nb_lgt_tot"]),
            None,
        )

    vac_col = next(
        (c for c in cols if "vacant" in c.lower() or "inoccup" in c.lower()), None
    )

    if total_col and vac_col:
        df["log_soc_total"] = pd.to_numeric(df[total_col], errors="coerce").fillna(0)
        df["log_soc_inoccupes"] = pd.to_numeric(df[vac_col], errors="coerce").fillna(0)
        df_out = df[["codgeo", "log_soc_total", "log_soc_inoccupes"]]

        output_path = CLEAN_DIR / "rpls.parquet"
        df_out.to_parquet(output_path, engine="fastparquet")
        logger.log_step("clean_rpls", "COMPLETED", {"path": str(output_path)})


def clean_caf(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans CAF and saves to parquet."""
    logger.log_step("clean_caf", "STARTED")
    source = config["sources"]["caf"]

    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_couverture_petite_enfance")
            )
            if not df_odace.empty:
                df_odace = df_odace.rename(
                    columns={
                        "commune_insee_code": "codgeo",
                        "taux_couverture_commune": "taux_couverture",
                    }
                )
                df_odace["codgeo"] = df_odace["codgeo"].astype(str).str.zfill(5)

                # Filter for max year
                if "annee" in df_odace.columns:
                    max_year = df_odace["annee"].max()
                    df_odace = df_odace[df_odace["annee"] == max_year]

                df_out = df_odace[["codgeo", "taux_couverture"]].copy()
                df_out["taux_couverture"] = pd.to_numeric(
                    df_out["taux_couverture"], errors="coerce"
                ).fillna(0)

                output_path = CLEAN_DIR / "caf.parquet"
                df_out.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_caf",
                    "COMPLETED",
                    {"path": str(output_path), "rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for fact_couverture_petite_enfance. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch fact_couverture_petite_enfance from Odace: {e}. Falling back to legacy."
            )

    # Legacy path
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    codgeo_col = next(
        (
            c
            for c in df.columns
            if "codgeo" in c.lower() or "insee" in c.lower() or c == "numcom"
        ),
        None,
    )
    if codgeo_col:
        df.rename(columns={codgeo_col: "codgeo"}, inplace=True)
        df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)

        if "annee" in df.columns:
            max_year = df["annee"].max()
            df = df[df["annee"] == max_year]

        if "taux_accueil_total" in df.columns:
            df.rename(columns={"taux_accueil_total": "taux_couverture"}, inplace=True)
        elif "txcouv_com" in df.columns:
            df.rename(columns={"txcouv_com": "taux_couverture"}, inplace=True)

        if "taux_couverture" in df.columns:
            df["taux_couverture"] = pd.to_numeric(
                df["taux_couverture"], errors="coerce"
            ).fillna(0)
            df_out = df[["codgeo", "taux_couverture"]].copy()

            output_path = CLEAN_DIR / "caf.parquet"
            df_out.to_parquet(output_path, engine="fastparquet")
            logger.log_step(
                "clean_caf",
                "COMPLETED",
                {"path": str(output_path), "rows": len(df_out), "source": "legacy"},
            )


def clean_education(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Education and saves to parquet (Bypassed in favor of BPE25)."""
    logger.log_step("clean_education", "SKIPPED")
    return


def clean_finess_national(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans FINESS National data and saves to parquet (Bypassed in favor of BPE25)."""
    logger.log_step("clean_finess_national", "SKIPPED")
    return


def clean_maternites(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Maternites (DREES) and saves to json in CACHE_DIR to maintain backward compatibility."""
    logger.log_step("clean_maternites", "STARTED")
    source = config["sources"]["maternites"]

    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "dim_maternite"))
            if not df_odace.empty:
                # Expecting 'fi_et' (or 'FI_ET') in the json file loaded by build.py
                df_out = df_odace[["finess_etablissement_code"]].rename(
                    columns={"finess_etablissement_code": "fi_et"}
                )

                output_path = CACHE_DIR / source["local_name"]
                df_out.to_json(output_path, orient="records")
                logger.log_step(
                    "clean_maternites",
                    "COMPLETED",
                    {"rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for dim_maternite. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch dim_maternite from Odace: {e}. Falling back to legacy."
            )

    # Legacy ingestion path
    logging.info(
        "maternites: use_odace is False or Odace fetch failed. Reverting to legacy local copy."
    )
    logger.log_step("clean_maternites", "COMPLETED", {"source": "legacy"})


def clean_services_inclusion(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Inclusion (Services) and saves to parquet (one row per service)."""
    logger.log_step("clean_services_inclusion", "STARTED")
    source = config["sources"]["services_inclusion"]
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    # Filter required columns
    required_cols = ["id", "nom", "thematiques", "latitude", "longitude", "code_insee"]
    if not all(col in df.columns for col in required_cols):
        logging.warning(f"Services Inclusion: Missing columns. Found: {df.columns}")
        return

    # Filter rows with coordinates and thematiques
    df = df.dropna(subset=["latitude", "longitude", "thematiques"])

    # Parse 'thematiques' (stringified list or list)
    def parse_thematiques(val):
        try:
            raw_extracted = []
            if isinstance(val, str):
                val = val.strip()
                if val.startswith("[") and val.endswith("]"):
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
            elif hasattr(val, "tolist"):  # Handle numpy arrays
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

    df["thematique_list"] = df["thematiques"].apply(parse_thematiques)

    # Explode
    df_exploded = df.explode("thematique_list")
    df_exploded = df_exploded.dropna(subset=["thematique_list"])

    # Clean slug
    def clean_slug(val):
        val = str(val).strip()
        if val.startswith("['") and val.endswith("']"):
            return val[2:-2]
        return val

    df_exploded["service_slug"] = df_exploded["thematique_list"].apply(clean_slug)

    # Select and Rename
    df_out = df_exploded[
        ["id", "nom", "service_slug", "latitude", "longitude", "code_insee"]
    ].rename(columns={"id": "id_structure", "code_insee": "codgeo"})

    # Robust codgeo cleaning
    # 1. Coerce to numeric (handles 'None', '', 'nan' -> NaN)
    df_out["codgeo_numeric"] = pd.to_numeric(df_out["codgeo"], errors="coerce")
    # 2. Drop invalid
    df_out = df_out.dropna(subset=["codgeo_numeric"])
    # 3. Convert to int then str then zfill
    df_out["codgeo"] = df_out["codgeo_numeric"].astype(int).astype(str).str.zfill(5)

    df_out = df_out.drop(columns=["codgeo_numeric"])

    output_path = CLEAN_DIR / "services_inclusion.parquet"
    df_out.to_parquet(output_path, engine="fastparquet")
    logger.log_step(
        "clean_services_inclusion",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_out)},
    )


def clean_structures_inclusion(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Inclusion (Structures) and saves to parquet (one row per structure)."""
    logger.log_step("clean_structures_inclusion", "STARTED")
    source = config.get("local_files", {}).get(
        "structures_inclusion"
    )  # Try local_files first if configured there for some reason
    if not source:
        source = config["sources"]["structures_inclusion"]

    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    # Required columns
    # We need: reseaux_porteurs, code_insee, id, nom, courriel, telephone, site_web, adresse
    # Check if 'reseaux_porteurs' exists
    if "reseaux_porteurs" not in df.columns:
        logging.warning(
            "clean_structures_inclusion: 'reseaux_porteurs' column missing."
        )
        return

    # Parse reseaux_porteurs
    def parse_reseaux(val):
        try:
            # Handle numpy array directly
            if hasattr(val, "tolist"):
                val = val.tolist()

            if pd.isna(val):
                return []
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                import ast

                val = val.strip()
                if val.startswith("[") and val.endswith("]"):
                    try:
                        # Handle "['a', 'b']"
                        return ast.literal_eval(val)
                    except:
                        pass
                return [val]  # Fallback for single string
            return []
        except:
            return []

    df["reseaux_parsed"] = df["reseaux_porteurs"].apply(parse_reseaux)

    # Filter: contains "ccas" or "cias" (case insensitive)
    def has_ccas(lst):
        if not lst:
            return False
        for item in lst:
            if isinstance(item, str):
                s = item.lower()
                if "ccas" in s or "cias" in s:
                    return True
        return False

    df_filtered = df[df["reseaux_parsed"].apply(has_ccas)].copy()

    # Filter: Telephone OR Courriel must exist
    # Normalize empty strings to NaN/None for easier check
    if "telephone" in df_filtered.columns:
        df_filtered["telephone"] = df_filtered["telephone"].replace("", np.nan)
    else:
        df_filtered["telephone"] = np.nan

    if "courriel" in df_filtered.columns:
        df_filtered["courriel"] = df_filtered["courriel"].replace("", np.nan)
    else:
        df_filtered["courriel"] = np.nan

    count_before_contact_filter = len(df_filtered)
    df_filtered = df_filtered.dropna(subset=["telephone", "courriel"], how="all")
    logging.info(
        f"filtered structures: CCAS match={count_before_contact_filter}, +Contact match={len(df_filtered)}"
    )

    if df_filtered.empty:
        logging.warning(
            "clean_structures_inclusion: No structures found after filtering."
        )
        return

    # Normalize codgeo
    if "code_insee" in df_filtered.columns:
        df_filtered["codgeo_numeric"] = pd.to_numeric(
            df_filtered["code_insee"], errors="coerce"
        )
        df_filtered = df_filtered.dropna(subset=["codgeo_numeric"])
        df_filtered["codgeo"] = (
            df_filtered["codgeo_numeric"].astype(int).astype(str).str.zfill(5)
        )
    else:
        logging.warning("clean_structures_inclusion: 'code_insee' missing.")
        return

    # Select columns
    cols_to_keep = [
        "id",
        "nom",
        "codgeo",
        "courriel",
        "telephone",
        "site_web",
        "adresse",
        "commune",
    ]
    # Ensure they exist
    existing_cols = [c for c in cols_to_keep if c in df_filtered.columns]

    df_out = df_filtered[existing_cols]

    output_path = CLEAN_DIR / "structures_inclusion.parquet"
    df_out.to_parquet(output_path, engine="fastparquet")
    logger.log_step(
        "clean_structures_inclusion",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_out)},
    )


def clean_associations(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Associations and saves to parquet."""
    logger.log_step("clean_associations", "STARTED")
    source = config["sources"]["associations"]
    output_path = CLEAN_DIR / "associations_vertical.parquet"

    # Odace pathway
    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "dim_association"))
            if not df_odace.empty:
                df_odace["id_waldec"] = (
                    df_odace["objet_social_code"].astype(str).str.zfill(6)
                )
                df_odace["codgeo"] = (
                    df_odace["commune_insee_code"].astype(str).str.zfill(5)
                )

                df_out = (
                    df_odace.groupby(["codgeo", "id_waldec"])
                    .size()
                    .rename("count")
                    .reset_index()
                )

                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_out.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_associations",
                    "COMPLETED",
                    {"path": str(output_path), "rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for dim_association. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch associations from Odace: {e}. Falling back to legacy."
            )

    # Legacy pathway
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    if "adrs_codeinsee" in df.columns:
        df.rename(columns={"adrs_codeinsee": "codgeo"}, inplace=True)
    if "objet_social1" in df.columns:
        df.rename(columns={"objet_social1": "id_waldec"}, inplace=True)

    if "codgeo" in df.columns and "id_waldec" in df.columns:
        # Need config for WALDEC codes.
        # We can load them from app config or hardcode/duplicate for pipeline isolation.
        # For now, let's try to load from app.config if possible, or just use a known list.
        # To avoid dependency issues, I will read them from config.py if I can, or just skip filtering here?
        # No, I need to filter to get 'lien_social'.

        df["id_waldec"] = df["id_waldec"].astype(str).str.zfill(6)
        df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)

        # Save detailed associations for vertical table
        # We keep all associations to allow dynamic filtering in the app (Core vs Affinities)
        # We aggregate by codgeo and id_waldec to save space and provide a count
        df_out = (
            df.groupby(["codgeo", "id_waldec"]).size().rename("count").reset_index()
        )

        df_out.to_parquet(output_path, engine="fastparquet")
        logger.log_step("clean_associations", "COMPLETED", {"path": str(output_path)})


def clean_refugee_associations(config: Dict[str, Any], logger: PipelineLogger):
    """Filters RNA for refugee associations and augments data."""
    logger.log_step("clean_refugee_associations", "STARTED")
    source = config["sources"]["associations"]
    path = CACHE_DIR / source["local_name"]
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
        logging.info(
            "📡 [RNA RAG] Fetching detailed refugee associations from BigQuery..."
        )
        df_refug = client.query(query).to_dataframe()
    except Exception as e:
        logging.error(f"Failed to fetch refugee associations from BQ: {e}")
        return

    if df_refug.empty:
        logging.warning("No refugee associations found in BigQuery.")
        return

    # Normalize codgeo
    df_refug["codgeo"] = df_refug["codgeo"].astype(str).str.zfill(5)

    # Add Bassin de Vie mapping (INSEE Source)
    mapping_source = config["sources"]["bassins_de_vie"]
    mapping_path = CACHE_DIR / mapping_source["archive_file"]
    if mapping_path.exists():
        df_mapping = load_dataset(
            mapping_path, mapping_source
        )  # Already handles sheet_name/header from yaml
        # Rename columns to match odis_communes standard
        df_mapping = df_mapping.rename(
            columns={
                "Code géographique": "codgeo",
                "Bassin de vie 2022": "bassin_de_vie",
            }
        )
        if "codgeo" in df_mapping.columns and "bassin_de_vie" in df_mapping.columns:
            df_mapping["codgeo"] = df_mapping["codgeo"].astype(str).str.zfill(5)
            # Ensure bassin_de_vie is string and not float-string "12345.0"
            df_mapping["bassin_de_vie"] = (
                df_mapping["bassin_de_vie"]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )
            df_refug = df_refug.merge(
                df_mapping[["codgeo", "bassin_de_vie"]], on="codgeo", how="left"
            )

    # Save detailed associations for list display
    # Keep: id, codgeo, bassin_de_vie, name, description, waldec_code
    useful_cols = [
        "id",
        "codgeo",
        "bassin_de_vie",
        "name",
        "description",
        "waldec_code",
    ]
    df_out = df_refug[[c for c in useful_cols if c in df_refug.columns]].copy()

    output_path = CLEAN_DIR / "refugee_associations.parquet"
    df_out.to_parquet(output_path, engine="fastparquet")
    logger.log_step(
        "clean_refugee_associations",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_out)},
    )


def clean_population(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Population and saves to parquet."""
    logger.log_step("clean_population", "STARTED")
    source = config["sources"]["population"]

    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_population_municipale")
            )
            if not df_odace.empty:
                # The export Parquet now has all columns including geo_code and pop_2023 (including arrondissements)
                df_out = df_odace[["geo_code", "pop_2023"]].rename(
                    columns={"geo_code": "codgeo", "pop_2023": "population"}
                )
                df_out["codgeo"] = df_out["codgeo"].astype(str).str.zfill(5)
                df_out["population"] = pd.to_numeric(
                    df_out["population"], errors="coerce"
                ).fillna(0)

                # No longer overriding population for Paris, Marseille, and Lyon as Odace API now returns correct populations for parent codes

                output_path = CLEAN_DIR / "population.parquet"
                df_out.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_population",
                    "COMPLETED",
                    {"rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning("Odace fetch returned empty data for population.")
        except Exception as e:
            logging.error(
                f"Failed to fetch population from Odace: {e}. Falling back to legacy."
            )

    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)

    pop_col = next((c for c in df.columns if "pop" in c.lower()), None)
    geo_col = next(
        (c for c in df.columns if "codgeo" in c.lower() or "com" in c.lower()), None
    )

    if pop_col and geo_col:
        df = df[[geo_col, pop_col]].rename(
            columns={geo_col: "codgeo", pop_col: "population"}
        )
        df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)

        output_path = CLEAN_DIR / "population.parquet"
        df.to_parquet(output_path, engine="fastparquet")
        logger.log_step("clean_population", "COMPLETED", {"path": str(output_path)})


def clean_communes(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Communes and saves to parquet."""
    logger.log_step("clean_communes", "STARTED")
    source = config["sources"]["communes"]
    output_path = CLEAN_DIR / "communes.parquet"

    # Odace pathway
    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "ref_commune_geo"))
            if not df_odace.empty:
                from shapely.geometry import shape

                # 1. Fetch and build EPCI mapping
                epci_cfg = config["sources"].get(
                    "ref_epci",
                    {
                        "datagouv_resource_id": "4f02ce39-2a91-4a8b-85cb-c6a0f912516b",
                        "local_name": "ref_epci.json",
                        "ttl_days": 90,
                    },
                )
                epci_path = fetch_source("ref_epci", epci_cfg, logger)
                epci_map = {}
                if epci_path and epci_path.exists():
                    try:
                        with open(epci_path, "r", encoding="utf-8") as f:
                            epci_data = json.load(f)
                        for epci in epci_data:
                            epci_code = epci.get("code")
                            for m in epci.get("membres", []):
                                if m.get("code"):
                                    epci_map[str(m["code"]).zfill(5)] = str(
                                        epci_code
                                    ).zfill(9)
                    except Exception as e:
                        logging.error(f"Error parsing ref_epci mapping: {e}")

                # 2. Fetch dim_commune for labels, departement, and region
                df_commune = client.fetch_table("dim_commune")

                df_odace.rename(columns={"commune_insee_code": "codgeo"}, inplace=True)
                df_odace["codgeo"] = df_odace["codgeo"].astype(str).str.zfill(5)

                # Merge with dim_commune attributes
                if not df_commune.empty:
                    df_commune.rename(
                        columns={"commune_insee_code": "codgeo"}, inplace=True
                    )
                    df_commune["codgeo"] = df_commune["codgeo"].astype(str).str.zfill(5)
                    df_odace = df_odace.merge(
                        df_commune[
                            [
                                "codgeo",
                                "commune_label",
                                "departement_code",
                                "region_code",
                            ]
                        ],
                        on="codgeo",
                        how="left",
                    )
                    df_odace.rename(
                        columns={
                            "commune_label": "nom",
                            "departement_code": "departement",
                            "region_code": "region",
                        },
                        inplace=True,
                    )
                else:
                    df_odace["nom"] = ""
                    df_odace["departement"] = ""
                    df_odace["region"] = ""

                # Add commune name duplicate and plm flag
                df_odace["commune"] = df_odace["nom"]
                df_odace["plm"] = np.where(
                    df_odace["codgeo"].isin(["75056", "13055", "69123"]), 1.0, np.nan
                )

                # Map EPCI
                df_odace["epci"] = df_odace["codgeo"].map(epci_map)

                # Parse GeoJSON into geometries
                geoms = df_odace["geometrie_geojson"].apply(
                    lambda x: shape(json.loads(x)) if x else None
                )
                gdf = gpd.GeoDataFrame(df_odace, geometry=geoms)

                # Convert geometry to WKB polygon
                gdf["polygon"] = gdf.geometry.to_wkb()

                # Filter to only the required output columns to maintain exact compat
                out_cols = [
                    "codgeo",
                    "nom",
                    "departement",
                    "region",
                    "commune",
                    "plm",
                    "epci",
                    "polygon",
                ]
                df_final = pd.DataFrame(gdf[[c for c in out_cols if c in gdf.columns]])

                df_final.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_communes",
                    "COMPLETED",
                    {
                        "path": str(output_path),
                        "rows": len(df_final),
                        "source": "odace",
                    },
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for ref_commune_geo. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch ref_commune_geo from Odace: {e}. Falling back to legacy."
            )

    # Legacy pathway
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    gdf = load_dataset(path, source)

    if "codgeo" not in gdf.columns:
        if "INSEE_COM" in gdf.columns:
            gdf.rename(columns={"INSEE_COM": "codgeo"}, inplace=True)
        elif "code" in gdf.columns:
            gdf.rename(columns={"code": "codgeo"}, inplace=True)

    if "codgeo" in gdf.columns:
        if "geometry" in gdf.columns:
            gdf["polygon"] = gdf.geometry.to_wkb()
            gdf.drop(columns=["geometry"], inplace=True)
        pd.DataFrame(gdf).to_parquet(output_path, engine="fastparquet")
        logger.log_step("clean_communes", "COMPLETED", {"path": str(output_path)})


def clean_political(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Political Nuance and saves to parquet."""
    logger.log_step("clean_political", "STARTED")
    source = config["sources"]["political_nuance"]
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    codgeo_col = next(
        (c for c in df.columns if "Code Insee" in c or "cog_commune" in c), None
    )
    nuance_col = next(
        (
            c
            for c in df.columns
            if ("Nuance" in c or "nuance_politique" in c) and "Libellé" not in c
        ),
        None,
    )
    famille_col = next(
        (c for c in df.columns if "famille" in c.lower() or "famille_nuance" in c), None
    )

    if codgeo_col:
        # Normalize columns
        nuance_val = df[nuance_col].astype(str).str.strip().str.upper() if nuance_col else pd.Series("", index=df.index)
        famille_val = df[famille_col].astype(str).str.strip() if famille_col else pd.Series("", index=df.index)
        
        far_right_nuances = {"RN", "LRN", "REC", "LREC", "EXD", "LEXD", "UXD", "LUXD", "BC-RN", "BC-UXD", "BC-EXD"}
        
        df["maire_extreme_droite"] = (
            (famille_val == "Extrême droite") | 
            (nuance_val.isin(far_right_nuances))
        )
        df["pol_num"] = np.where(df["maire_extreme_droite"], 0.0, 1.0)
        df["codgeo"] = df[codgeo_col].astype(str).str.zfill(5)

        df_out = df[["codgeo", "pol_num", "maire_extreme_droite"]]
        output_path = CLEAN_DIR / "political.parquet"
        df_out.to_parquet(output_path, engine="fastparquet")
        logger.log_step("clean_political", "COMPLETED", {"path": str(output_path)})
    else:
        logging.warning(f"Political: Columns not found. Found: {df.columns}")


def clean_electoral_history(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Electoral History from candidats_results.parquet and saves to parquet."""
    logger.log_step("clean_electoral_history", "STARTED")
    source = config["sources"]["electoral_history"]
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        logging.warning(f"Electoral history source file not found at: {path}")
        return

    cols = ["id_election", "code_commune", "nuance", "libelle_abrege_liste", "nom", "voix"]
    
    # Pre-determined major elections list for PyArrow filter to avoid loading 3.4GB+
    allowed_elections = [
        "2026_muni_t2", "2026_muni_t1",
        "2024_legi_t2", "2024_legi_t1", "2024_euro_t1",
        "2022_pres_t2", "2022_pres_t1", "2022_legi_t2", "2022_legi_t1",
        "2020_muni_t2", "2020_muni_t1", "2019_euro_t1",
        "2017_pres_t2", "2017_pres_t1", "2017_legi_t2", "2017_legi_t1",
        "2014_euro_t1", "2014_muni_t2", "2014_muni_t1",
        "2012_pres_t2", "2012_pres_t1", "2012_legi_t2", "2012_legi_t1"
    ]
    
    # Load and filter at pyarrow level
    df = pd.read_parquet(
        path,
        columns=cols,
        filters=[("id_election", "in", allowed_elections)],
        engine="pyarrow"
    )

    df["nuance"] = df["nuance"].fillna("").astype(str)
    df["libelle_abrege_liste"] = df["libelle_abrege_liste"].fillna("").astype(str)
    df["nom"] = df["nom"].fillna("").astype(str)
    
    agg = df.groupby(["code_commune", "id_election", "nuance", "libelle_abrege_liste", "nom"], as_index=False)["voix"].sum()
    
    # Compute total votes per commune + election
    totals = agg.groupby(["code_commune", "id_election"])["voix"].transform("sum")
    agg["total_voix"] = totals
    agg["pct"] = np.where(agg["total_voix"] > 0, (agg["voix"] / agg["total_voix"] * 100).round(1), 0.0)
    
    # Get index of row with max votes for each group
    idx = agg.groupby(["code_commune", "id_election"])["voix"].idxmax()
    winners = agg.loc[idx].copy()
    
    NUANCE_LABELS = {
        "UG": "Union de la Gauche",
        "LUG": "Union de la Gauche",
        "BC-UG": "Union de la Gauche",
        "BC-UGE": "Union de la Gauche & Écologistes",
        "SOC": "Socialiste",
        "LSOC": "Parti Socialiste",
        "BC-SOC": "Parti Socialiste",
        "COM": "Parti Communiste Français",
        "LCOM": "Parti Communiste Français",
        "BC-COM": "Parti Communiste Français",
        "FI": "La France Insoumise",
        "LFI": "La France Insoumise",
        "BC-FI": "La France Insoumise",
        "VEC": "Les Écologistes",
        "LVEC": "Les Écologistes",
        "BC-ECO": "Écologistes",
        "ECO": "Écologiste",
        "LECO": "Écologistes",
        "NUP": "NUPES",
        "DVG": "Divers Gauche",
        "LDVG": "Divers Gauche",
        "BC-DVG": "Divers Gauche",
        "EXG": "Extrême Gauche",
        "LEXG": "Extrême Gauche",
        "DXG": "Divers Extrême Gauche",
        "BC-EXG": "Extrême Gauche",
        "REN": "Renaissance",
        "LREN": "Renaissance / Ensemble",
        "LREM": "La République En Marche",
        "REM": "La République En Marche",
        "BC-REM": "La République En Marche",
        "MDM": "MoDem",
        "LMDM": "MoDem",
        "BC-MDM": "MoDem",
        "HOR": "Horizons",
        "LHOR": "Horizons",
        "ENS": "Ensemble",
        "LENS": "Ensemble",
        "DVC": "Divers Centre",
        "LDVC": "Divers Centre",
        "BC-DVC": "Divers Centre",
        "UC": "Union du Centre",
        "LUC": "Union du Centre",
        "BC-UC": "Union du Centre",
        "LR": "Les Républicains",
        "LLR": "Les Républicains",
        "BC-LR": "Les Républicains",
        "DVD": "Divers Droite",
        "LDVD": "Divers Droite",
        "BC-DVD": "Divers Droite",
        "UDI": "Union des Démocrates et Indépendants",
        "LUDI": "Union des Démocrates et Indépendants",
        "BC-UDI": "Union des Démocrates et Indépendants",
        "UD": "Union de la Droite",
        "LUD": "Union de la Droite",
        "BC-UD": "Union de la Droite",
        "BC-UCD": "Union du Centre-Droite",
        "UCD": "Union du Centre-Droite",
        "LUCD": "Union du Centre-Droite",
        "DSV": "Droite Souverainiste",
        "LDSV": "Droite Souverainiste",
        "BC-DSV": "Droite Souverainiste",
        "LDLF": "Debout la France",
        "RN": "Rassemblement National",
        "LRN": "Rassemblement National",
        "BC-RN": "Rassemblement National",
        "REC": "Reconquête",
        "LREC": "Reconquête",
        "EXD": "Extrême Droite",
        "LEXD": "Extrême Droite",
        "UXD": "Union de l'Extrême Droite",
        "LUXD": "Union de l'Extrême Droite",
        "BC-UXD": "Union de l'Extrême Droite",
        "DXD": "Divers Extrême Droite",
        "BC-EXD": "Extrême Droite",
        "DIV": "Divers",
        "LDIV": "Divers",
        "BC-DIV": "Divers",
        "REG": "Régionaliste",
        "LREG": "Régionaliste",
        "BC-REG": "Régionaliste",
        "NC": "Non Classé",
        "LNC": "Non Classé",
        "GJ": "Gilets Jaunes",
        "LGJ": "Gilets Jaunes",
        "BC-GJ": "Gilets Jaunes",
    }

    ELECTION_DATES = {
        "2026_muni_t2": "2026-06-28",
        "2026_muni_t1": "2026-06-21",
        "2024_legi_t2": "2024-07-07",
        "2024_legi_t1": "2024-06-30",
        "2024_euro_t1": "2024-06-09",
        "2022_legi_t2": "2022-06-19",
        "2022_legi_t1": "2022-06-12",
        "2022_pres_t2": "2022-04-24",
        "2022_pres_t1": "2022-04-10",
        "2020_muni_t2": "2020-06-28",
        "2020_muni_t1": "2020-03-15",
        "2019_euro_t1": "2019-05-26",
        "2017_legi_t2": "2017-06-18",
        "2017_legi_t1": "2017-06-11",
        "2017_pres_t2": "2017-05-07",
        "2017_pres_t1": "2017-04-23",
        "2014_euro_t1": "2014-05-25",
        "2014_muni_t2": "2014-03-30",
        "2014_muni_t1": "2014-03-23",
        "2012_legi_t2": "2012-06-17",
        "2012_legi_t1": "2012-06-10",
        "2012_pres_t2": "2012-05-06",
        "2012_pres_t1": "2012-04-22",
    }

    ELECTION_LABELS = {
        "muni": "Municipales",
        "legi": "Législatives",
        "pres": "Présidentielle",
        "euro": "Européennes",
    }

    def format_election_name(id_election: str) -> str:
        parts = id_election.split("_")
        year = parts[0]
        el_type = parts[1]
        label = ELECTION_LABELS.get(el_type, el_type.capitalize())
        return f"{label} {year}"

    def get_winner_label(row):
        nuance = row["nuance"]
        list_name = row["libelle_abrege_liste"]
        nom = row["nom"]
        
        if nuance:
            return NUANCE_LABELS.get(nuance, nuance)
        elif list_name:
            return list_name
        elif nom:
            return nom
        else:
            return "Inconnu"
            
    winners["winner_label"] = winners.apply(get_winner_label, axis=1)
    
    winners["date"] = winners["id_election"].map(ELECTION_DATES).fillna("1900-01-01")
    winners = winners.sort_values(by=["code_commune", "date"], ascending=[True, False])
    
    commune_histories = {}
    for code_commune, group in winners.groupby("code_commune"):
        history = []
        for _, row in group.head(5).iterrows():
            history.append({
                "election": format_election_name(row["id_election"]),
                "nuance": row["winner_label"],
                "percentage": float(row["pct"])
            })
        commune_histories[code_commune] = json.dumps(history, ensure_ascii=False)
        
    df_out = pd.DataFrame([
        {"codgeo": str(k).zfill(5), "electoral_history": v}
        for k, v in commune_histories.items()
    ])
    
    output_path = CLEAN_DIR / "electoral_history.parquet"
    df_out.to_parquet(output_path, engine="fastparquet")
    logger.log_step("clean_electoral_history", "COMPLETED", {"path": str(output_path)})


def clean_housing_occupation(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Housing Occupation and saves to parquet."""
    logger.log_step("clean_housing_occupation", "STARTED")
    source = config["sources"]["housing_occupation"]
    path = CACHE_DIR / source["archive_file"]
    if not path.exists():
        return

    # Load with correct separator (likely ';')
    try:
        df = pd.read_csv(path, sep=";")
        if len(df.columns) < 2:
            df = pd.read_csv(path, sep=",")
    except:
        df = pd.read_csv(path, sep=",")

    # Filter
    if "TIME_PERIOD" in df.columns:
        max_year = df["TIME_PERIOD"].max()
        logging.info(f"Housing Occupation: Using max year {max_year}")
        df = df[df["TIME_PERIOD"] == max_year]
    if "GEO_OBJECT" in df.columns:
        df = df[df["GEO_OBJECT"] == "COM"]

    # We need Taux d'occupation.
    # Assuming OCC_IND has 'STD_OCC' (Standard), 'OVER_OCC' (Suroccupation), 'UNDER_OCC' (Sous-occupation)
    # And OBS_VALUE is the count of dwellings.
    # We want the rate of "Good" occupation? Or rate of "Under" (room to spare)?
    # User said "build a scale based of OCC_IND".
    # Let's save the raw counts pivoted by OCC_IND and let build.py calculate the ratio.

    if "GEO" in df.columns and "OCC_IND" in df.columns and "OBS_VALUE" in df.columns:
        df_pivot = df.pivot_table(
            index="GEO", columns="OCC_IND", values="OBS_VALUE", aggfunc="sum"
        ).reset_index()
        df_pivot.rename(columns={"GEO": "codgeo"}, inplace=True)
        df_pivot["codgeo"] = df_pivot["codgeo"].astype(str).str.zfill(5)

        output_path = CLEAN_DIR / "housing_occupation.parquet"
        df_pivot.to_parquet(output_path, engine="fastparquet")
        logger.log_step(
            "clean_housing_occupation", "COMPLETED", {"path": str(output_path)}
        )


def clean_school_effectifs(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans School Effectifs and saves to parquet."""
    logger.log_step("clean_school_effectifs", "STARTED")
    source = config["sources"]["education_effectifs"]
    path = CACHE_DIR / source["local_name"]

    # Load Codes Postaux for mapping
    cp_source = config["sources"]["codes_postaux"]
    cp_path = CACHE_DIR / cp_source["local_name"]

    if not path.exists() or not cp_path.exists():
        logging.warning("Education Effectifs or Codes Postaux not found.")
        return

    df = load_dataset(path, source)
    df_cp = load_dataset(cp_path, cp_source)

    # Prepare CP data
    # Index(['#Code_commune_INSEE', 'Nom_de_la_commune', 'Code_postal', ...])
    df_cp = df_cp.rename(
        columns={
            "#Code_commune_INSEE": "code_insee",
            "Code_postal": "code_postal",
            "Nom_de_la_commune": "nom_commune",
        }
    )
    df_cp["code_postal"] = df_cp["code_postal"].astype(str).str.zfill(5)

    def normalize_city(s):
        if not isinstance(s, str):
            return ""
        # Replace hyphens with spaces
        s = s.upper().replace("-", " ").replace("'", " ")
        # Standardize Saint/Sainte
        s = s.replace("SAINT ", "ST ").replace("SAINTE ", "STE ")
        # Strip extra spaces
        return " ".join(s.split())

    df_cp["nom_commune_norm"] = df_cp["nom_commune"].apply(normalize_city)

    # Filter for Latest Year
    if "rentree_scolaire" in df.columns:
        # Normalize to datetime or string if needed, or just compare
        # Assuming format is comparable or datetime
        latest_year = df["rentree_scolaire"].max()
        logging.info(f"Education Effectifs: Using latest year {latest_year}")
        df = df[df["rentree_scolaire"] == latest_year].copy()
    else:
        logging.warning(
            "Education Effectifs: 'rentree_scolaire' column missing. Using full dataset (risk of duplication)."
        )

    # 2. Education Annuaire (Reference for UAI -> Commune)
    annuaire_cfg = config["sources"]["education_annuaire"]
    annuaire_path = CACHE_DIR / annuaire_cfg["local_name"]

    if not annuaire_path.exists():
        logging.warning("Education Annuaire not found. Cannot map effectifs.")
        return

    df_annuaire = pd.read_parquet(annuaire_path, engine="fastparquet")

    # Check columns
    # We expect 'numero_uai' and 'code_commune' (or similar)
    uai_col = next(
        (
            c
            for c in df_annuaire.columns
            if "numero_uai" in c or "identifiant_de_l_etablissement" in c
        ),
        None,
    )
    insee_col = next((c for c in df_annuaire.columns if "code_commune" in c), None)

    if not uai_col or not insee_col:
        logging.warning(
            f"Education Annuaire: Missing UAI ({uai_col}) or INSEE ({insee_col}) columns."
        )
        return

    # Prepare Annuaire for link
    # Drop duplicates on UAI just in case
    df_ref = (
        df_annuaire[[uai_col, insee_col]]
        .drop_duplicates(subset=[uai_col])
        .rename(columns={uai_col: "numero_ecole", insee_col: "codgeo"})
    )
    df_ref["codgeo"] = df_ref["codgeo"].astype(str).str.zfill(5)

    # 3. Merge Effectifs -> Annuaire (on UAI)
    merged = df.merge(df_ref, on="numero_ecole", how="left")

    mapped_count = merged["codgeo"].notna().sum()
    logging.info(
        f"Education Mapping (UAI): {mapped_count} / {len(merged)} ({mapped_count / len(merged):.1%}) mapped."
    )

    if len(merged) - mapped_count > 0:
        logging.warning(
            f"Education Effectifs: {len(merged) - mapped_count} rows failed to map via UAI."
        )
        # Optional: Fallback to old method?
        # User said "Mapping on the address is ugly", so we stick to UAI or fail/warn.

    valid = merged.dropna(subset=["codgeo"]).copy()

    # 4. Compute Metrics
    effectif_col = "nombre_total_eleves"
    classes_col = "nombre_total_classes"

    if effectif_col not in valid.columns or classes_col not in valid.columns:
        logging.warning("Missing effectifs/classes columns.")
        return

    # Calculate students per class
    # Avoid division by zero
    valid["students_per_class"] = np.where(
        valid[classes_col] > 0, valid[effectif_col] / valid[classes_col], 0.0
    )

    # Risk Metric: Threshold < 20 (User confirmed)
    # We want "Likely to close" -> Low number of students per class.
    # Score logic: Higher count of risky schools -> Worse score.
    THRESHOLD = 20
    valid["is_risky"] = (valid["students_per_class"] < THRESHOLD).astype(int)

    # Group by Commune
    df_agg = (
        valid.groupby("codgeo")
        .agg(
            {
                effectif_col: "sum",
                classes_col: "sum",
                "is_risky": "sum",
                "numero_ecole": "nunique",
            }
        )
        .reset_index()
    )

    df_agg.rename(
        columns={
            effectif_col: "total_eleves",
            classes_col: "total_classes",
            "is_risky": "risky_schools_count",
            "numero_ecole": "ecoles_count",
        },
        inplace=True,
    )

    # Also calculate average students per class for the whole commune (optional context)
    df_agg["avg_students_per_class_commune"] = np.where(
        df_agg["total_classes"] > 0,
        df_agg["total_eleves"] / df_agg["total_classes"],
        0.0,
    )

    output_path = CLEAN_DIR / "school_effectifs.parquet"
    df_agg.to_parquet(output_path, engine="fastparquet")
    logger.log_step(
        "clean_school_effectifs",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_agg)},
    )


def clean_bpe(config: Dict[str, Any], logger: PipelineLogger):
    """Extracts points of interest and aggregated counts from BPE."""
    output_edu_cols = CLEAN_DIR / "bpe_education_cols.parquet"
    output_sante_cols = CLEAN_DIR / "bpe_sante_cols.parquet"
    output_heb_cols = CLEAN_DIR / "bpe_hebergement_cols.parquet"
    output_act_cols = CLEAN_DIR / "bpe_action_sociale_cols.parquet"
    output_gares_cols = CLEAN_DIR / "bpe_gares_cols.parquet"
    output_pois = CLEAN_DIR / "bpe_pois.parquet"

    # 1-Year TTL Check (as requested by user)
    needs_refresh = True
    if (
        output_edu_cols.exists()
        and output_sante_cols.exists()
        and output_heb_cols.exists()
        and output_act_cols.exists()
        and output_gares_cols.exists()
        and output_pois.exists()
    ):
        mtime = datetime.fromtimestamp(output_edu_cols.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        if age_days < 365:
            logging.info(
                f"[BPE] BPE stats are {age_days} days old. Using cache (TTL=1 year)."
            )
            needs_refresh = False

    if not needs_refresh:
        return

    logger.log_step("clean_bpe", "STARTED")
    source = config["sources"]["bpe"]

    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        logging.error(f"BPE25 parquet file not found at {path}")
        return

    df = load_dataset(path, source)

    # Construct CODGEO
    if "DEPCOM" in df.columns:
        df["codgeo"] = df["DEPCOM"].astype(str).str.zfill(5)
    elif "DEP" in df.columns and "COM" in df.columns:
        df["codgeo"] = df["DEP"].astype(str).str.zfill(2) + df["COM"].astype(
            str
        ).str.zfill(3)
    elif "CODGEO" in df.columns:
        df["codgeo"] = df["CODGEO"].astype(str).str.zfill(5)

    if "TYPEQU" not in df.columns:
        logging.warning("BPE: TYPEQU column not found.")
        return

    # Project coords if LATITUDE/LONGITUDE are missing but LAMBERT_X/LAMBERT_Y are present
    df["longitude"] = pd.to_numeric(df["LONGITUDE"], errors="coerce")
    df["latitude"] = pd.to_numeric(df["LATITUDE"], errors="coerce")

    missing_coords = df["longitude"].isna() | df["latitude"].isna()
    valid_lambert = (
        ~df["LAMBERT_X"].isna()
        & ~df["LAMBERT_Y"].isna()
        & (df["LAMBERT_X"] != 0)
        & (df["LAMBERT_Y"] != 0)
    )

    reproject_mask = missing_coords & valid_lambert
    if reproject_mask.any():
        try:
            df_to_reproj = df[reproject_mask].copy()
            gdf_reproj = gpd.GeoDataFrame(
                df_to_reproj,
                geometry=gpd.points_from_xy(
                    df_to_reproj["LAMBERT_X"], df_to_reproj["LAMBERT_Y"]
                ),
                crs="EPSG:2154",
            ).to_crs("EPSG:4326")
            df.loc[reproject_mask, "longitude"] = gdf_reproj.geometry.x
            df.loc[reproject_mask, "latitude"] = gdf_reproj.geometry.y
            logging.info(
                f"BPE: Reprojected {reproject_mask.sum()} coords from Lambert EPSG:2154"
            )
        except Exception as e:
            logging.warning(f"BPE: Reprojection failed: {e}")

    # --- 1. Education & Early Childhood (Éducation & Petite Enfance) ---
    is_edu = df["TYPEQU"].str.startswith("C", na=False)
    # Apply SECTEUR filter ONLY to education codes (Public and Private sous contrat)
    # EAJE, Relais, ALSH, Micro-crèche do not have SECTEUR codes or are not filtered
    df_edu_all = df[
        (is_edu & df["SECTEUR"].isin(["1", "2"]))
        | (df["TYPEQU"].isin(["D502", "D504", "D505", "D509"]))
    ].copy()

    df_edu_all["is_maternelle"] = (
        df_edu_all["TYPEQU"].isin(["C107", "C108"]).astype(int)
    )
    df_edu_all["is_elementaire"] = (
        df_edu_all["TYPEQU"].isin(["C109", "C108"]).astype(int)
    )
    df_edu_all["is_college"] = (df_edu_all["TYPEQU"] == "C201").astype(int)
    df_edu_all["is_lycee"] = (
        df_edu_all["TYPEQU"].isin(["C301", "C302", "C303", "C305"]).astype(int)
    )
    df_edu_all["is_eaje"] = (df_edu_all["TYPEQU"] == "D502").astype(int)
    df_edu_all["is_relais_petite_enfance"] = (df_edu_all["TYPEQU"] == "D504").astype(
        int
    )
    df_edu_all["is_alsh"] = (df_edu_all["TYPEQU"] == "D505").astype(int)
    df_edu_all["is_micro_creche"] = (df_edu_all["TYPEQU"] == "D509").astype(int)

    df_edu_cols = (
        df_edu_all.groupby("codgeo")
        .agg(
            {
                "is_maternelle": "sum",
                "is_elementaire": "sum",
                "is_college": "sum",
                "is_lycee": "sum",
                "is_eaje": "sum",
                "is_relais_petite_enfance": "sum",
                "is_alsh": "sum",
                "is_micro_creche": "sum",
            }
        )
        .rename(
            columns={
                "is_maternelle": "edu_maternelle_ct",
                "is_elementaire": "edu_elementaire_ct",
                "is_college": "edu_college_ct",
                "is_lycee": "edu_lycee_ct",
                "is_eaje": "edu_eaje_ct",
                "is_relais_petite_enfance": "edu_relais_petite_enfance_ct",
                "is_alsh": "edu_alsh_ct",
                "is_micro_creche": "edu_micro_creche_ct",
            }
        )
        .reset_index()
    )
    df_edu_cols.to_parquet(output_edu_cols, engine="fastparquet")

    # --- 2. Housing & Accommodation (Logement & Hébergement) ---
    df_heb = df[df["TYPEQU"].isin(["D703", "D704", "D705", "D710"])].copy()

    mask_fjt = (df_heb["TYPEQU"] == "D710") & df_heb["NOMRS"].str.contains(
        "fjt|foyer jeunes travailleurs", case=False, na=False, regex=True
    )
    mask_pension = (df_heb["TYPEQU"] == "D710") & df_heb["NOMRS"].str.contains(
        "pension", case=False, na=False, regex=True
    )

    df_heb["sub_type"] = None
    df_heb.loc[df_heb["TYPEQU"] == "D703", "sub_type"] = "CHRS"
    df_heb.loc[df_heb["TYPEQU"] == "D704", "sub_type"] = "CPH"
    df_heb.loc[df_heb["TYPEQU"] == "D705", "sub_type"] = "CADA"
    df_heb.loc[mask_fjt, "sub_type"] = "FJT"
    df_heb.loc[mask_pension, "sub_type"] = "Pension"

    # Drop rows that don't match FJT, Pension, CHRS, CPH, CADA
    df_heb = df_heb.dropna(subset=["sub_type"]).copy()

    df_heb["is_chrs"] = (df_heb["sub_type"] == "CHRS").astype(int)
    df_heb["is_cph"] = (df_heb["sub_type"] == "CPH").astype(int)
    df_heb["is_cada"] = (df_heb["sub_type"] == "CADA").astype(int)
    df_heb["is_fjt"] = (df_heb["sub_type"] == "FJT").astype(int)
    df_heb["is_pension"] = (df_heb["sub_type"] == "Pension").astype(int)

    df_heb_cols = (
        df_heb.groupby("codgeo")
        .agg(
            {
                "is_chrs": "sum",
                "is_cph": "sum",
                "is_cada": "sum",
                "is_fjt": "sum",
                "is_pension": "sum",
            }
        )
        .rename(
            columns={
                "is_chrs": "heb_chrs_count",
                "is_cph": "heb_cph_count",
                "is_cada": "heb_cada_count",
                "is_fjt": "heb_fjt_count",
                "is_pension": "heb_pension_count",
            }
        )
        .reset_index()
    )
    df_heb_cols.to_parquet(output_heb_cols, engine="fastparquet")

    # --- 3. Health (Santé) ---
    df_sante = df[
        df["TYPEQU"].isin(
            ["D101", "D107", "D108", "D109", "D111", "D113", "D114", "D115"]
        )
    ].copy()
    df_sante["is_hopital"] = (df_sante["TYPEQU"] == "D101").astype(int)
    df_sante["is_maternite"] = (df_sante["TYPEQU"] == "D107").astype(int)
    df_sante["is_centre_sante"] = (df_sante["TYPEQU"] == "D108").astype(int)
    df_sante["is_psy"] = (df_sante["TYPEQU"] == "D109").astype(int)
    df_sante["is_dialyse"] = (df_sante["TYPEQU"] == "D111").astype(int)
    df_sante["is_maison_sante"] = (df_sante["TYPEQU"] == "D113").astype(int)
    df_sante["is_addictologie"] = (df_sante["TYPEQU"] == "D114").astype(int)
    df_sante["is_pmi"] = (df_sante["TYPEQU"] == "D115").astype(int)

    df_sante_cols = (
        df_sante.groupby("codgeo")
        .agg(
            {
                "is_hopital": "sum",
                "is_maternite": "sum",
                "is_centre_sante": "sum",
                "is_psy": "sum",
                "is_dialyse": "sum",
                "is_maison_sante": "sum",
                "is_addictologie": "sum",
                "is_pmi": "sum",
            }
        )
        .rename(
            columns={
                "is_hopital": "count_hopital",
                "is_maternite": "count_maternite",
                "is_centre_sante": "count_centre_sante",
                "is_psy": "count_psy",
                "is_dialyse": "count_dialyse",
                "is_maison_sante": "count_maison_sante",
                "is_addictologie": "count_addictologie",
                "is_pmi": "count_pmi",
            }
        )
        .reset_index()
    )
    df_sante_cols.to_parquet(output_sante_cols, engine="fastparquet")

    # --- 4. Action Sociale ---
    df_act = df[df["TYPEQU"].isin(["A125", "A128", "A129", "D711"])].copy()
    df_act["is_antenne_justice"] = (df_act["TYPEQU"] == "A125").astype(int)
    df_act["is_france_services"] = (df_act["TYPEQU"] == "A128").astype(int)
    df_act["is_mairie"] = (df_act["TYPEQU"] == "A129").astype(int)
    df_act["is_femmes_vuln"] = (df_act["TYPEQU"] == "D711").astype(int)

    df_act_cols = (
        df_act.groupby("codgeo")
        .agg(
            {
                "is_antenne_justice": "sum",
                "is_france_services": "sum",
                "is_mairie": "sum",
                "is_femmes_vuln": "sum",
            }
        )
        .rename(
            columns={
                "is_antenne_justice": "act_antenne_justice_count",
                "is_france_services": "act_france_services_count",
                "is_mairie": "act_mairie_count",
                "is_femmes_vuln": "act_femmes_vuln_count",
            }
        )
        .reset_index()
    )
    df_act_cols.to_parquet(output_act_cols, engine="fastparquet")

    # --- 5. Gares ---
    df_gares = df[df["TYPEQU"].isin(["E107", "E108", "E109"])].copy()
    df_gares["is_gare"] = 1
    df_gares_cols = (
        df_gares.groupby("codgeo")
        .agg({"is_gare": "sum"})
        .rename(columns={"is_gare": "gare_count"})
        .reset_index()
    )
    df_gares_cols["has_gare"] = (df_gares_cols["gare_count"] > 0).astype(int)
    df_gares_cols.to_parquet(output_gares_cols, engine="fastparquet")

    # --- 6. Unified POIs output ---
    pois_parts = []

    # 1. Education POIs
    df_edu_pois = df_edu_all.dropna(subset=["longitude", "latitude"]).copy()
    if not df_edu_pois.empty:
        type_lbls = {
            "C107": "École Maternelle",
            "C108": "École Primaire",
            "C109": "École Élémentaire",
            "C201": "Collège",
            "C301": "Lycée Général/Tech",
            "C302": "Lycée Professionnel",
            "C303": "Lycée Agricole",
            "C305": "Section Enseignement Pro",
            "D502": "Crèche / EAJE",
            "D504": "Relais Petite Enfance",
            "D505": "Accueil de loisirs (ALSH)",
            "D509": "Micro-crèche",
        }
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_edu_pois.index.astype(str) + "_edu",
                    "name": df_edu_pois["NOMRS"].astype(str),
                    "type": df_edu_pois["TYPEQU"].map(type_lbls),
                    "category": "education",
                    "lat": df_edu_pois["latitude"],
                    "lon": df_edu_pois["longitude"],
                    "codgeo": df_edu_pois["codgeo"],
                }
            )
        )

    # 2. Housing POIs
    df_heb_pois = df_heb.dropna(subset=["longitude", "latitude"]).copy()
    if not df_heb_pois.empty:
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_heb_pois.index.astype(str) + "_heb",
                    "name": df_heb_pois["NOMRS"].astype(str),
                    "type": df_heb_pois["sub_type"],
                    "category": "hebergement",
                    "lat": df_heb_pois["latitude"],
                    "lon": df_heb_pois["longitude"],
                    "codgeo": df_heb_pois["codgeo"],
                }
            )
        )

    # 3. Health POIs
    df_sante_pois = df_sante.dropna(subset=["longitude", "latitude"]).copy()
    if not df_sante_pois.empty:
        type_lbls = {
            "D101": "Hôpital",
            "D107": "Maternité",
            "D108": "Centre de santé",
            "D109": "Soutien Psychologique",
            "D111": "Dialyse",
            "D113": "Maison de santé",
            "D114": "Addictologie",
            "D115": "Santé maternelle et infantile (PMI)",
        }
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_sante_pois.index.astype(str) + "_sante",
                    "name": df_sante_pois["NOMRS"].astype(str),
                    "type": df_sante_pois["TYPEQU"].map(type_lbls),
                    "category": "sante",
                    "lat": df_sante_pois["latitude"],
                    "lon": df_sante_pois["longitude"],
                    "codgeo": df_sante_pois["codgeo"],
                }
            )
        )

    # 4. Action Sociale & Mairie POIs
    df_act_pois = df_act.dropna(subset=["longitude", "latitude"]).copy()
    if not df_act_pois.empty:
        type_lbls = {
            "A125": "Antenne de justice",
            "A128": "France Services",
            "A129": "Mairie",
            "D711": "Aide femmes vulnérables",
        }
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_act_pois.index.astype(str) + "_act",
                    "name": df_act_pois["NOMRS"].astype(str),
                    "type": df_act_pois["TYPEQU"].map(type_lbls),
                    "category": df_act_pois["TYPEQU"].apply(
                        lambda x: "mairie" if x == "A129" else "action_sociale"
                    ),
                    "lat": df_act_pois["latitude"],
                    "lon": df_act_pois["longitude"],
                    "codgeo": df_act_pois["codgeo"],
                }
            )
        )

    # 5. Gares POIs
    df_gares_pois = df_gares.dropna(subset=["longitude", "latitude"]).copy()
    if not df_gares_pois.empty:
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_gares_pois.index.astype(str) + "_gare",
                    "name": df_gares_pois["NOMRS"].astype(str),
                    "type": "Gare SNCF",
                    "category": "mobilite",
                    "lat": df_gares_pois["latitude"],
                    "lon": df_gares_pois["longitude"],
                    "codgeo": df_gares_pois["codgeo"],
                }
            )
        )

    if pois_parts:
        pois_df = pd.concat(pois_parts, ignore_index=True)
        pois_df.to_parquet(output_pois, engine="fastparquet")
        logger.log_step(
            "clean_bpe",
            "COMPLETED",
            {
                "edu": str(output_edu_cols),
                "sante": str(output_sante_cols),
                "heb": str(output_heb_cols),
                "act": str(output_act_cols),
                "gares": str(output_gares_cols),
                "pois": str(output_pois),
            },
        )
    else:
        logger.log_step("clean_bpe", "PARTIAL", {"msg": "No POIs generated"})


def compute_rna_rag_counts(query_text: str, threshold: float = 0.65) -> pd.DataFrame:
    """Computes semantic counts for a query using BigQuery Vector Search (ML.DISTANCE)."""
    import os

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "odis-stream2")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
    client = bigquery.Client(project=project)
    genai_client = genai.Client(vertexai=True, project=project, location=location)

    # Generate Embedding
    response = genai_client.models.embed_content(
        model="text-multilingual-embedding-002",
        contents=[query_text],
        config={"output_dimensionality": 128},
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
            bigquery.ScalarQueryParameter(
                "dist_threshold", "FLOAT64", distance_threshold
            ),
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
            logging.info(
                f"[RNA RAG] Hebergement RNA stats are {age_days} days old. Using cache (TTL=1 year)."
            )
            return

    logger.log_step("clean_hebergement_rna", "STARTED")

    try:
        # A. IML Counts
        df_iml = compute_rna_rag_counts(
            "Bail solidaire et Intermediation Locative (IML)"
        )
        df_iml = df_iml.rename(columns={"count": "heb_loc_iml_count"})

        # B. Citoyen Counts
        df_cit = compute_rna_rag_counts("hébergement citoyen chez l'habitant")
        df_cit = df_cit.rename(columns={"count": "heb_habitant_count"})

        # Merge and finalize
        agg = df_iml.merge(df_cit, on="codgeo", how="outer").fillna(0)
        agg["codgeo"] = agg["codgeo"].astype(str).str.zfill(5)

        agg.to_parquet(output_agg, engine="fastparquet")
        logger.log_step(
            "clean_hebergement_rna",
            "COMPLETED",
            {"path": str(output_agg), "rows": len(agg)},
        )

    except Exception as e:
        logging.error(f"❌ [RNA RAG] Pivot failed: {e}")
        logger.log_step("clean_hebergement_rna", "ERROR", {"error": str(e)})


def clean_loyers(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Loyers data (Appartements) and saves to parquet."""
    logger.log_step("clean_loyers", "STARTED")
    source = config["sources"]["loyers_apparts"]
    path = CACHE_DIR / source["local_name"]

    if not path.exists():
        return

    # Load with correct options
    sep = source.get("sep", ";")
    encoding = source.get("encoding", "utf-8")

    df = pd.read_csv(path, sep=sep, encoding=encoding, dtype={"INSEE_C": str})

    # Expected columns: INSEE_C (code commune), loypredm2 (loyer moyen m2)
    if "INSEE_C" in df.columns:
        df.rename(columns={"INSEE_C": "codgeo"}, inplace=True)

    if "codgeo" not in df.columns:
        # Try to find a code column
        codgeo_col = next((c for c in df.columns if "INSEE" in c or "COD" in c), None)
        if codgeo_col:
            df.rename(columns={codgeo_col: "codgeo"}, inplace=True)

    if "codgeo" not in df.columns:
        logging.warning(f"Loyers: CODGEO not found. Found: {df.columns}")
        return

    df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)

    # Loyer column
    val_col = "loypredm2"
    if val_col not in df.columns:
        logging.warning(f"Loyers: {val_col} not found. Found: {df.columns}")
        return

    # Extract and clean
    df[val_col] = pd.to_numeric(
        df[val_col].astype(str).str.replace(",", "."), errors="coerce"
    )

    df_out = df[["codgeo", val_col]].rename(columns={val_col: "loyer_app_m2"})
    df_out = df_out.groupby("codgeo")["loyer_app_m2"].mean().reset_index()

    output_path = CLEAN_DIR / "loyers.parquet"
    df_out.to_parquet(output_path, engine="fastparquet")
    logger.log_step("clean_loyers", "COMPLETED", {"path": str(output_path)})


def clean_population_details(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Population Details (Age Breakdown) and saves to parquet."""
    logger.log_step("clean_population_details", "STARTED")
    source = config["sources"]["population_details"]

    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "fact_demographie"))
            if not df_odace.empty:
                # Map columns to look like the legacy dataset
                df_mapped = df_odace.rename(
                    columns={
                        "annee": "TIME_PERIOD",
                        "commune_insee_code": "GEO",
                        "tranche_age": "AGE",
                        "sexe": "SEX",
                        "valeur": "OBS_VALUE",
                    }
                )

                df_filtered = df_mapped[
                    (df_mapped["SEX"] == "_T")
                    & (df_mapped["AGE"].isin(["Y_LT15", "Y25T39", "Y40T54"]))
                ]

                df_filtered = df_filtered.rename(columns={"GEO": "codgeo"})
                df_filtered["codgeo"] = df_filtered["codgeo"].astype(str).str.zfill(5)
                df_filtered["year"] = df_filtered["TIME_PERIOD"].astype(str)
                df_filtered["count"] = pd.to_numeric(
                    df_filtered["OBS_VALUE"], errors="coerce"
                ).fillna(0)

                age_mapping = {
                    "Y_LT15": "jeune",
                    "Y25T39": "active",
                    "Y40T54": "active",
                }
                df_filtered["age_group"] = df_filtered["AGE"].map(age_mapping)

                df_pivot = df_filtered.pivot_table(
                    index="codgeo",
                    columns=["age_group", "year"],
                    values="count",
                    aggfunc="sum",
                )
                df_pivot.columns = [f"pop_{c[0]}_{c[1]}" for c in df_pivot.columns]
                df_pivot = df_pivot.reset_index()

                expected_cols = [
                    "pop_jeune_2016",
                    "pop_jeune_2022",
                    "pop_active_2016",
                    "pop_active_2022",
                ]
                for col in expected_cols:
                    if col not in df_pivot.columns:
                        df_pivot[col] = 0.0

                output_path = CLEAN_DIR / "population_details.parquet"
                df_pivot.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_population_details",
                    "COMPLETED",
                    {"rows": len(df_pivot), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for population_details."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch population_details from Odace: {e}. Falling back to legacy."
            )

    path = CACHE_DIR / source["archive_file"]
    if not path.exists():
        logging.warning("Population Details file not found.")
        return

    # Load CSV (Long Format)
    # "AGE";"GEO";"GEO_OBJECT";"RP_MEASURE";"SEX";"TIME_PERIOD";"OBS_VALUE"
    df = pd.read_csv(path, sep=";", low_memory=False)

    # Filter Checks
    required_cols = ["AGE", "GEO", "GEO_OBJECT", "SEX", "TIME_PERIOD", "OBS_VALUE"]
    if not all(col in df.columns for col in required_cols):
        logging.warning(f"Population Details: Missing columns. Found: {df.columns}")
        return

    # Filter Rows
    # GEO_OBJECT == 'COM'
    # SEX == '_T' (Total)
    df = df[(df["GEO_OBJECT"] == "COM") & (df["SEX"] == "_T")]

    # We need Age Groups:
    # Youth: < 15 -> 'Y_LT15'
    # Active: 25-54 -> 'Y25T39' + 'Y40T54'

    target_ages = ["Y_LT15", "Y25T39", "Y40T54"]
    df = df[df["AGE"].isin(target_ages)]

    # Normalize GEO -> codgeo
    df.rename(columns={"GEO": "codgeo"}, inplace=True)
    df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)

    # Normalize Year
    df["year"] = df["TIME_PERIOD"].astype(str)

    # Value to numeric
    df["count"] = pd.to_numeric(df["OBS_VALUE"], errors="coerce").fillna(0)

    # Aggregate Active Group
    # Map ages to broad categories
    age_mapping = {"Y_LT15": "jeune", "Y25T39": "active", "Y40T54": "active"}
    df["age_group"] = df["AGE"].map(age_mapping)

    # Pivot
    # Index: codgeo
    # Columns: {age_group}_{year}
    # Values: Sum of count

    df_pivot = df.pivot_table(
        index="codgeo", columns=["age_group", "year"], values="count", aggfunc="sum"
    )

    # Flatten Columns
    # e.g. active_2016, active_2022
    df_pivot.columns = [f"pop_{c[0]}_{c[1]}" for c in df_pivot.columns]
    df_pivot.reset_index(inplace=True)

    # Ensure expected columns exist (fill 0 if checking years 2016/2022)
    expected_cols = [
        "pop_jeune_2016",
        "pop_jeune_2022",
        "pop_active_2016",
        "pop_active_2022",
    ]
    for col in expected_cols:
        if col not in df_pivot.columns:
            logging.warning(
                f"Population Details: Missing expected column {col}. Setting to 0."
            )
            df_pivot[col] = 0.0

    output_path = CLEAN_DIR / "population_details.parquet"
    df_pivot.to_parquet(output_path, engine="fastparquet")
    logger.log_step(
        "clean_population_details",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_pivot)},
    )


def clean_nomenclature_waldec(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans WALDEC Nomenclature and saves to parquet."""
    logger.log_step("clean_nomenclature_waldec", "STARTED")
    source = config["sources"]["nomenclature_waldec"]
    path = CACHE_DIR / source["local_name"]

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
        code_col = next(
            (
                c
                for c in df.columns
                if c in ["code", "id", "id_waldec", "objet_social_id"]
            ),
            None,
        )
        label_col = next(
            (
                c
                for c in df.columns
                if c in ["libelle", "label", "titre", "lib", "objet_social_lib"]
            ),
            None,
        )

        if code_col and label_col:
            df_out = df[[code_col, label_col]].rename(
                columns={code_col: "code", label_col: "label"}
            )
            # Ensure strings and zero-padding (6 digits)
            df_out["code"] = df_out["code"].astype(str).str.zfill(6)
            df_out["label"] = df_out["label"].astype(str)

            output_path = CLEAN_DIR / "referentiel_waldec.parquet"
            df_out.to_parquet(output_path, engine="fastparquet")
            logger.log_step(
                "clean_nomenclature_waldec",
                "COMPLETED",
                {"path": str(output_path), "rows": len(df_out)},
            )
        else:
            logging.warning(
                f"WALDEC: Could not identify code/label columns. Found: {df.columns}"
            )
            logger.log_step(
                "clean_nomenclature_waldec", "FAILED", {"reason": "Columns not found"}
            )

    except Exception as e:
        logger.log_step("clean_nomenclature_waldec", "ERROR", {"error": str(e)})
        logging.error(f"WALDEC clean failed: {e}")


def clean_inclusion_jobs(
    config: Dict[str, Any], logger: PipelineLogger, skip: bool = False
):
    """Fetches job openings from Les emplois de l'inclusion."""
    logger.log_step("clean_inclusion_jobs", "STARTED")

    status = get_inclusion_jobs_status()
    should_run = not skip

    if should_run:
        if status["within_ttl"]:
            logging.info(
                f"Inclusion Jobs: Data is {status['age_days']:.1f} days old (TTL={status['ttl_days']}). Skipping fetch."
            )
            should_run = False
        elif not status["exists"]:
            logging.info("Inclusion Jobs: No existing data found.")
        else:
            logging.info(
                f"Inclusion Jobs: Data is {status['age_days']:.1f} days old (TTL expired)."
            )

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
        ttl_days = (
            config.get("local_files", {})
            .get("france_travail_live", {})
            .get("ttl_days", 7)
        )
    except:
        ttl_days = 7

    files = [cache_path, data_path]
    mtimes = []
    for f in files:
        if f.exists():
            mtimes.append(f.stat().st_mtime)

    if not mtimes:
        return {
            "age_days": None,
            "within_ttl": False,
            "exists": False,
            "ttl_days": ttl_days,
        }

    newest_mtime = max(mtimes)
    age_days = (time.time() - newest_mtime) / (24 * 3600)

    return {
        "age_days": age_days,
        "within_ttl": age_days < ttl_days,
        "exists": True,
        "ttl_days": ttl_days,
    }


def get_inclusion_jobs_status() -> Dict[str, Any]:
    """Checks the age of Inclusion Jobs data in cache and deployed data."""
    cache_path = OUTPUT_DIR / "odis_inclusion_jobs.parquet"
    data_path = Path("data/odis_inclusion_jobs.parquet")

    # Dynamic TTL check
    try:
        config = load_config(CONFIG_FILE)
        ttl_days = (
            config.get("local_files", {}).get("inclusion_jobs", {}).get("ttl_days", 7)
        )
    except:
        ttl_days = 7

    files = [cache_path, data_path]
    mtimes = []
    for f in files:
        if f.exists():
            mtimes.append(f.stat().st_mtime)

    if not mtimes:
        return {
            "age_days": None,
            "within_ttl": False,
            "exists": False,
            "ttl_days": ttl_days,
        }

    newest_mtime = max(mtimes)
    age_days = (time.time() - newest_mtime) / (24 * 3600)

    return {
        "age_days": age_days,
        "within_ttl": age_days < ttl_days,
        "exists": True,
        "ttl_days": ttl_days,
    }


def clean_live_jobs(config: Dict[str, Any], logger: PipelineLogger, skip: bool = False):
    """Fetches and aggregates Live Job offers from France Travail."""
    logger.log_step("clean_live_jobs", "STARTED")

    status = get_live_jobs_status()
    should_run = not skip

    if should_run:
        if status["within_ttl"]:
            logging.info(
                f"Live Jobs: Data is {status['age_days']:.1f} days old (TTL={status['ttl_days']}). Skipping fetch."
            )
            should_run = False
        elif not status["exists"]:
            logging.info("Live Jobs: No existing data found.")
        else:
            logging.info(
                f"Live Jobs: Data is {status['age_days']:.1f} days old (TTL expired)."
            )

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
        self.status = getattr(real_logger, "status", None)

    def log_step(
        self, step_name: str, status: str, details: Optional[Dict[str, Any]] = None
    ):
        pass

    def log_source(
        self, source_name: str, status: str, file_path: Optional[str] = None
    ):
        self.real_logger.log_source(source_name, status, file_path)


def run_clean_step_safely(
    step_name: str,
    clean_func,
    config: Dict[str, Any],
    logger: PipelineLogger,
    *args,
    **kwargs,
):
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

    # Map step names to sources.yaml/local_files keys if they differ
    STEP_TO_SOURCE_MAP = {
        "lovac": "logement_vacant",
        "rpls": "logement_social",
        "education": "education_annuaire",
        "political": "political_nuance",
        "school_effectifs": "education_effectifs",
        "loyers": "loyers_apparts",
        "departements": "departements_ref",
        "mob_durable": "mob_durable_share",
        "log_soc_delay": "logement_social_delay",
        "live_jobs": "france_travail_live",
        "inclusion_jobs": "inclusion_jobs",
        "gares": "dim_gare",
        "odace_rent": "fact_loyer_annonce",
        "formations": "formations_annuaire",
        "electoral_history": "electoral_history",
    }

    config_key = STEP_TO_SOURCE_MAP.get(step_name, step_name)
    source_cfg = config["sources"].get(config_key) or config.get("local_files", {}).get(
        config_key
    )

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

    local_name = source_cfg.get("local_name")
    path_str = source_cfg.get("path")

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
    archive_file = source_cfg.get("archive_file")
    if archive_file:
        active_ext = CACHE_DIR / archive_file
        staging_ext = CACHE_DIR / f"staging_{archive_file}"
    else:
        active_ext = None
        staging_ext = None

    # Dynamic Clean Filename Resolver
    clean_filenames = {
        "associations": "associations_vertical.parquet",
        "nomenclature_waldec": "referentiel_waldec.parquet",
        "hebergement_rna": "hebergement_rna_cols.parquet",
        "jaccueille": "jaccueille_bdv.parquet",
        "bpe": "bpe_pois.parquet",
        "odace_rent": "odace_loyer_annonce.parquet",
        "formations": "formations_annuaire.parquet",
        "finess_national": "../raw/finess_national.parquet",
        "maternites": "../raw/maternites_drees.json",
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
                    df_clean = pd.read_parquet(active_clean, engine="fastparquet")
                    details = {"rows": len(df_clean), "path": str(active_clean)}
                    if len(df_clean) == 0:
                        logging.warning(
                            f"⚠️ [SANITY WARNING] Active clean file for '{step_name}' is empty."
                        )
                except Exception as e:
                    logging.warning(
                        f"⚠️ [SANITY WARNING] Failed to read active clean file for '{step_name}': {e}"
                    )

            logger.log_step(f"clean_{step_name}", "COMPLETED", details)
        except Exception as e:
            logger.log_step(f"clean_{step_name}", "ERROR", {"error": str(e)})
            logging.exception(f"Error running step clean_{step_name}")
        return

    # Blue-Green: validate RAW, back up, and swap staging files into place
    logging.info(
        f"🔄 [Staging Mode] Staging files detected for '{step_name}'. Performing safe dry-run."
    )

    # 1. Validate RAW schema contract
    raw_data_path = (
        staging_ext
        if (archive_file and staging_ext and staging_ext.exists())
        else staging_raw
    )
    if raw_data_path and raw_data_path.exists():
        try:
            logging.info(
                f"📋 Validating raw schema contract for '{step_name}' using {raw_data_path.name}"
            )
            df_raw = load_dataset(raw_data_path, source_cfg)
            if not validate_dataset_contract(df_raw, step_name, source_cfg):
                raise ValueError("Raw schema contract validation failed.")
        except Exception as e:
            logging.error(
                f"❌ [INGEST FAILURE] '{step_name}' raw schema validation failed: {e}"
            )
            logger.log_step(
                f"clean_{step_name}",
                "ERROR",
                {"error": f"Raw validation failed: {str(e)}"},
            )
            # Discard staging files and abort
            if staging_raw and staging_raw.exists():
                try:
                    os.remove(staging_raw)
                except:
                    pass
            if staging_ext and staging_ext.exists():
                try:
                    os.remove(staging_ext)
                except:
                    pass
            logging.warning(f"⚠️ [ABORTED] Retained existing cache for '{step_name}'.")
            return

    backups = {}  # Map of active_path -> backup_path
    moved_staging = []  # List of (active_path, staging_path)

    try:
        # 2. Back up active raw files
        if active_raw and active_raw.exists():
            bak_raw = active_raw.with_name(active_raw.name + ".active_bak")
            if bak_raw.exists():
                os.remove(bak_raw)
            os.replace(active_raw, bak_raw)
            backups[active_raw] = bak_raw

        if active_ext and active_ext.exists():
            bak_ext = active_ext.with_name(active_ext.name + ".active_bak")
            if bak_ext.exists():
                os.remove(bak_ext)
            os.replace(active_ext, bak_ext)
            backups[active_ext] = bak_ext

        # 3. Back up active clean parquet
        if active_clean.exists():
            bak_clean = active_clean.with_name(active_clean.name + ".active_bak")
            if bak_clean.exists():
                os.remove(bak_clean)
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
            raise FileNotFoundError(
                f"Clean step did not generate the clean parquet file: {active_clean}"
            )

        df_clean = pd.read_parquet(active_clean, engine="fastparquet")
        if len(df_clean) == 0:
            raise ValueError("Cleaned output dataset is empty.")

        # Success! Commit changes (delete backups)
        for bak_path in backups.values():
            try:
                os.remove(bak_path)
            except:
                pass
        logging.info(
            f"✅ [SUCCESS] Ingested and verified '{step_name}' successfully. Staging committed."
        )
        logger.log_step(
            f"clean_{step_name}",
            "COMPLETED",
            {"rows": len(df_clean), "path": str(active_clean)},
        )

    except Exception as e:
        logging.error(
            f"❌ [INGEST FAILURE] '{step_name}' failed validation/cleaning: {e}"
        )
        logger.log_step(f"clean_{step_name}", "ERROR", {"error": str(e)})

        # Rollback!
        # Delete failed active clean parquet
        if active_clean.exists():
            try:
                os.remove(active_clean)
            except:
                pass

        # Move active files back to staging (re-create staging files if we want to preserve them, or delete them)
        # To match Option A "discards staging files", we can delete any active files that were staging
        for active_path, _ in moved_staging:
            if active_path.exists():
                try:
                    os.remove(active_path)
                except:
                    pass

        # Restore original active files from backups
        for active_path, bak_path in backups.items():
            if bak_path.exists():
                os.replace(bak_path, active_path)

        logging.warning(
            f"⚠️ [ROLLBACK COMPLETE] Reverted '{step_name}' to last known good cache."
        )


def main(argv=None):
    logging.getLogger().setLevel(logging.INFO)
    parser = argparse.ArgumentParser(description="ODIS Ingest Pipeline")
    parser.add_argument(
        "--steps",
        type=str,
        help="Comma-separated list of steps to run (e.g. communes,inclusion)",
    )
    parser.add_argument(
        "--skip-live-jobs",
        action="store_true",
        help="Skip France Travail Live Jobs fetch",
    )
    parser.add_argument(
        "--skip-inclusion-jobs", action="store_true", help="Skip Inclusion Jobs fetch"
    )
    args = parser.parse_args(argv)

    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)

    logger.log_step("ingest_all", "STARTED")

    # --- 1. Fetch ROME Referential (New) ---
    fetch_rome_referential(config, logger)

    # --- 2. Fetch RNA RAG Stats from BigQuery (New) ---
    fetch_rna_rag_stats(logger)

    # 2. Fetch others
    for name, source_cfg in config["sources"].items():
        fetch_source(name, source_cfg, logger)

    # 2. Clean
    steps_map = {
        "communes": clean_communes,
        "services_inclusion": clean_services_inclusion,
        "structures_inclusion": clean_structures_inclusion,
        "population": clean_population,
        "population_active": clean_population_active,
        "lovac": clean_lovac,
        "rpls": clean_rpls,
        "caf": clean_caf,
        "education": clean_education,
        "finess_national": clean_finess_national,
        "maternites": clean_maternites,
        "associations": clean_associations,
        "refugee_associations": clean_refugee_associations,
        "political": clean_political,
        "electoral_history": clean_electoral_history,
        "housing_occupation": clean_housing_occupation,
        "school_effectifs": clean_school_effectifs,
        "bpe": clean_bpe,
        "codes_postaux": clean_codes_postaux,
        "formations": clean_formations,
        "gares": clean_odace_gares,
        "odace_rent": clean_odace_rent,
        "loyers": clean_loyers,
        "population_details": clean_population_details,
        "nomenclature_waldec": clean_nomenclature_waldec,
        "departements": clean_departements,
        "live_jobs": clean_live_jobs,
        "inclusion_jobs": clean_inclusion_jobs,
        "mob_transports_pub": clean_mob_transports_pub,
        "hebergement_rna": clean_hebergement_rna,
        "jaccueille": clean_jaccueille,
        "log_soc_delay": clean_log_soc_delay,
        "sante_apl": clean_sante_apl,
        "mob_durable": clean_mob_durable,
        "ter_insecurite": clean_ter_insecurite,
    }

    selected_steps = args.steps.split(",") if args.steps else steps_map.keys()

    for step_name in selected_steps:
        if step_name in steps_map:
            try:
                if step_name == "live_jobs":
                    skip_live = getattr(args, "skip_live_jobs", False)
                    run_clean_step_safely(
                        step_name, steps_map[step_name], config, logger, skip=skip_live
                    )
                elif step_name == "inclusion_jobs":
                    skip_inc = getattr(args, "skip_inclusion_jobs", False)
                    run_clean_step_safely(
                        step_name, steps_map[step_name], config, logger, skip=skip_inc
                    )
                else:
                    run_clean_step_safely(
                        step_name, steps_map[step_name], config, logger
                    )
            except Exception as e:
                logging.exception(
                    f"❌ [INGEST FAILURE] Error running step '{step_name}'"
                )
        else:
            logging.warning(f"Unknown step: {step_name}")

    logger.log_step("ingest_all", "COMPLETED")


def clean_codes_postaux(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Codes Postaux and saves to parquet."""
    logger.log_step("clean_codes_postaux", "STARTED")
    try:
        source = config["sources"]["codes_postaux"]
        path = CACHE_DIR / source["local_name"]
        if not path.exists():
            return

        df = load_dataset(path, source)

        # Normalize columns
        df.columns = [c.strip() for c in df.columns]

        # Identify columns
        cp_col = next(
            (c for c in df.columns if "Code_postal" in c or "code_postal" in c), None
        )
        insee_col = next(
            (
                c
                for c in df.columns
                if "Code_commune_INSEE" in c or "code_commune_insee" in c
            ),
            None,
        )

        if cp_col and insee_col:
            df = df[[cp_col, insee_col]].copy()
            df["code_postal"] = df[cp_col].astype(str).str.zfill(5)
            df["codgeo"] = df[insee_col].astype(str).str.zfill(5)

            df_out = df[["code_postal", "codgeo"]].drop_duplicates()

            output_path = CLEAN_DIR / "codes_postaux.parquet"
            df_out.to_parquet(output_path, engine="fastparquet")
            logger.log_step(
                "clean_codes_postaux", "COMPLETED", {"path": str(output_path)}
            )
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

    cp_df = pd.read_parquet(cp_path, engine="fastparquet")
    # cp_df has 'code_postal', 'codgeo'

    # 2. Formations Referentiel (XLSX)
    ref_cfg = config["sources"]["formations_referentiel"]
    ref_path = CACHE_DIR / ref_cfg["local_name"]

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
            df_ref.columns = ["code", "label"]
            df_ref["code"] = (
                df_ref["code"].astype(str).str.replace(".0", "", regex=False)
            )

            output_ref = CLEAN_DIR / "formations_referentiel.parquet"
            df_ref.to_parquet(output_ref, engine="fastparquet")
            logger.log_step(
                "clean_formations", "REFERENTIEL", {"path": str(output_ref)}
            )
        else:
            logging.warning("Formations Referentiel: Unexpected columns.")

    # 3. Formations Annuaire (CSV)
    annuaire_cfg = config["sources"]["formations_annuaire"]
    annuaire_path = CACHE_DIR / annuaire_cfg["local_name"]

    if annuaire_path.exists():
        # Load CSV (semicolon likely)
        try:
            df_annuaire = pd.read_csv(
                annuaire_path, sep=";", on_bad_lines="skip", low_memory=False
            )
        except:
            df_annuaire = pd.read_csv(
                annuaire_path, sep=",", on_bad_lines="skip", low_memory=False
            )

        # Normalize columns
        df_annuaire.columns = [c.strip() for c in df_annuaire.columns]

        # Identify columns
        # We need 'code_postal' (to map to codgeo) and 'domaines_formation' (codes)
        # Let's look for them.
        cp_col = next(
            (
                c
                for c in df_annuaire.columns
                if "code_postal" in c.lower() or "codepostal" in c.lower()
            ),
            None,
        )

        # For formation codes, we need to know the column name.
        # Based on typical data.gouv files, it might be 'domaines_formation' or 'code_domaine'.
        # If we don't know, we can't proceed.
        # But I'll assume 'domaines_formation' or similar based on user description "The formations annuaire which lists all the entities".
        # We need 'Code UAI' and 'Patronyme uai' (Name)th 'formation' or 'domaine'
        formation_col = next(
            (
                c
                for c in df_annuaire.columns
                if "domaine" in c.lower() or "formation" in c.lower()
            ),
            None,
        )

        if cp_col and formation_col:
            df_annuaire["code_postal"] = df_annuaire[cp_col].astype(str).str.zfill(5)
        cp_col = next(
            (
                c
                for c in df_annuaire.columns
                if "adressePhysiqueOrganismeFormation.codePostal" in c
            ),
            None,
        )
        if not cp_col:
            cp_col = next(
                (
                    c
                    for c in df_annuaire.columns
                    if "code_postal" in c.lower() or "codepostal" in c.lower()
                ),
                None,
            )

        # Identify Formation Code Columns
        # Raw: informationsDeclarees.specialitesDeFormation.codeSpecialite1, 2, 3
        formation_cols = [c for c in df_annuaire.columns if "codeSpecialite" in c]

        if cp_col and formation_cols:
            # Melt to get one row per formation code
            df_melted = df_annuaire.melt(
                id_vars=[cp_col], value_vars=formation_cols, value_name="formation_code"
            ).dropna(subset=["formation_code"])

            # Fix Postal Codes (handle float strings like "75011.0")
            df_melted["code_postal"] = (
                pd.to_numeric(df_melted[cp_col], errors="coerce")
                .fillna(0)
                .astype(int)
                .astype(str)
                .str.zfill(5)
            )

            # Merge with codes postaux to get codgeo
            merged = df_melted.merge(cp_df, on="code_postal", how="inner")

            merged["formation_code"] = (
                merged["formation_code"]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
                .str.strip()
            )

            # Filter out invalid codes (optional, maybe length check?)
            merged = merged[merged["formation_code"] != "nan"]

            df_out = merged[["codgeo", "formation_code"]].drop_duplicates()

            output_annuaire = CLEAN_DIR / "formations_annuaire.parquet"
            df_out.to_parquet(output_annuaire, engine="fastparquet")
            logger.log_step(
                "clean_formations",
                "ANNUAIRE",
                {"path": str(output_annuaire), "rows": len(df_out)},
            )
        else:
            logging.warning(
                f"Formations Annuaire: Columns not found. CP: {cp_col}, Formations: {formation_cols}"
            )


def clean_odace_gares(config: Dict[str, Any], logger: PipelineLogger):
    """Fetches and cleans Gare data from Odace API (Bypassed in favor of BPE25)."""
    logger.log_step("clean_odace_gares", "SKIPPED")
    return


def clean_odace_rent(config: Dict[str, Any], logger: PipelineLogger):
    """Fetches and cleans Rent data from Odace API."""
    logger.log_step("clean_odace_rent", "STARTED")

    client = get_odace_client(logger)

    # Fetch Data
    df_rent = client.fetch_fact_loyer_annonce()
    df_profil = client.fetch_ref_logement_profil()

    if df_rent.empty or df_profil.empty:
        logging.warning(
            "Odace API returned empty data for rent facts or housing profiles."
        )
        return

    # Filter for relevant data (Prioritize 'commune', fallback to 'maille' if commune not available)
    if "maille_observation" in df_rent.columns:
        # Define priority (smaller number = higher priority)
        priority_map = {"commune": 1, "maille": 2, "EPCI": 3}
        df_rent["maille_priority"] = (
            df_rent["maille_observation"].map(priority_map).fillna(99)
        )

        # Sort and drop duplicates for each (commune_sk, logement_profil_sk)
        df_rent = df_rent.sort_values(
            ["commune_sk", "logement_profil_sk", "maille_priority"]
        )
        df_rent = df_rent.drop_duplicates(subset=["commune_sk", "logement_profil_sk"])

        logging.info(
            f"Odace Rent: Filtered and deduplicated. {len(df_rent)} rows remaining."
        )
        df_rent = df_rent.drop(columns=["maille_priority"])

    # Save to Clean Dir
    df_rent.to_parquet(
        CLEAN_DIR / "odace_loyer_annonce.parquet", index=False, engine="fastparquet"
    )
    df_profil.to_parquet(
        CLEAN_DIR / "odace_logement_profil.parquet", index=False, engine="fastparquet"
    )

    logger.log_step(
        "clean_odace_rent",
        "COMPLETED",
        {"rent_rows": len(df_rent), "profil_rows": len(df_profil)},
    )


def clean_departements(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Departements referential and saves to parquet."""
    logger.log_step("clean_departements", "STARTED")
    source = config["sources"]["departements_ref"]
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)

    # Expected columns: DEP, REG, LIBELLE
    if "DEP" in df.columns and "LIBELLE" in df.columns:
        cols_to_keep = ["DEP", "LIBELLE"]
        if "REG" in df.columns:
            cols_to_keep.append("REG")

        df_out = df[cols_to_keep].rename(
            columns={"DEP": "code", "LIBELLE": "label", "REG": "reg_code"}
        )
        df_out["code"] = df_out["code"].astype(str).str.zfill(2)
        if "reg_code" in df_out.columns:
            df_out["reg_code"] = df_out["reg_code"].astype(str).str.zfill(2)

        output_path = CLEAN_DIR / "departements.parquet"
        df_out.to_parquet(output_path, engine="fastparquet")
        logger.log_step(
            "clean_departements",
            "COMPLETED",
            {"path": str(output_path), "rows": len(df_out)},
        )
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
            logging.info(
                f"[RNA RAG] Stats are {age_days} days old. Using cache (TTL=30 days)."
            )
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
        df_pivot = (
            df_cats.pivot(index="codgeo", columns="primary_category", values="count")
            .fillna(0)
            .reset_index()
        )
        df_pivot.columns = [
            f"inc_rna_{col}_count" if col != "codgeo" else col
            for col in df_pivot.columns
        ]
        df_pivot["codgeo"] = df_pivot["codgeo"].astype(str).str.zfill(5)

        # Merge with refugee counts
        if not df_refug.empty:
            df_refug["codgeo"] = df_refug["codgeo"].astype(str).str.zfill(5)
            df_pivot = df_pivot.merge(df_refug, on="codgeo", how="left")
            df_pivot["inc_asso_refug_count"] = df_pivot["inc_asso_refug_count"].fillna(
                0
            )
        else:
            df_pivot["inc_asso_refug_count"] = 0

        df_pivot.to_parquet(local_path, engine="fastparquet")
        logging.info(
            f"✅ [RNA RAG] Saved {len(df_pivot)} commune stats to {local_path}"
        )
        logger.log_step(
            "fetch_rna_rag_stats",
            "COMPLETED",
            {"path": str(local_path), "rows": len(df_pivot)},
        )
        return local_path

    except Exception as e:
        logging.error(f"❌ [RNA RAG] BigQuery fetch failed: {e}")
        logger.log_step("fetch_rna_rag_stats", "ERROR", {"error": str(e)})
        return None


def clean_mob_transports_pub(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Public Transport Stations data and saves to parquet."""
    logger.log_step("clean_mob_transports_pub", "STARTED")
    source = config["sources"]["mob_transports_pub"]

    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_transport_commun")
            )
            if not df_odace.empty:
                # Normalize type_transport values and pivot
                df_odace["type_transport"] = (
                    df_odace["type_transport"].astype(str).str.lower().str.strip()
                )
                df_pivot = (
                    df_odace.pivot_table(
                        index="commune_insee_code",
                        columns="type_transport",
                        values="nb_stations",
                        aggfunc="sum",
                    )
                    .reset_index()
                    .fillna(0)
                )

                col_mapping = {
                    "commune_insee_code": "codgeo",
                    "bus": "nb_stops_bus",
                    "tramway": "nb_stops_tram",
                    "métro": "nb_stops_metro",
                    "train": "nb_stops_train",
                }
                df_pivot.rename(columns=col_mapping, inplace=True)

                for col in [
                    "nb_stops_bus",
                    "nb_stops_tram",
                    "nb_stops_metro",
                    "nb_stops_train",
                ]:
                    if col not in df_pivot.columns:
                        df_pivot[col] = 0.0

                df_pivot["codgeo"] = df_pivot["codgeo"].astype(str).str.zfill(5)
                df_pivot["nb_stops_total"] = (
                    df_pivot["nb_stops_bus"]
                    + df_pivot["nb_stops_tram"]
                    + df_pivot["nb_stops_metro"]
                    + df_pivot["nb_stops_train"]
                )

                output_path = CLEAN_DIR / "mob_transports_pub.parquet"
                df_pivot.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_mob_transports_pub",
                    "COMPLETED",
                    {"rows": len(df_pivot), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for transports. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch transports from Odace: {e}. Falling back to legacy."
            )

    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        logging.warning("Public Transport Stations file not found.")
        return

    df = load_dataset(path, source)

    # Required columns: geocode_commune, type_transport_en_commun, valeur
    required_cols = ["geocode_commune", "type_transport_en_commun", "valeur"]
    if not all(col in df.columns for col in required_cols):
        logging.warning(
            f"Public Transport Stations: Missing columns. Found: {df.columns}"
        )
        return

    # Pivot the data: 1 row per commune
    # type_transport_en_commun values: 'Bus', 'Tramway', 'Métropolitain', 'Train' (assuming)
    df_pivot = (
        df.pivot_table(
            index="geocode_commune",
            columns="type_transport_en_commun",
            values="valeur",
            aggfunc="sum",
        )
        .reset_index()
        .fillna(0)
    )

    # Rename columns to standardized names
    # The raw data has lowercase keys according to my earlier print: 'bus', 'tramway', 'métro', 'train'
    col_mapping = {
        "geocode_commune": "codgeo",
        "bus": "nb_stops_bus",
        "tramway": "nb_stops_tram",
        "métro": "nb_stops_metro",
        "train": "nb_stops_train",
    }

    # Apply mapping
    df_pivot.rename(columns=col_mapping, inplace=True)

    # Ensure all columns exist (in case some types are missing in the data)
    for col in ["nb_stops_bus", "nb_stops_tram", "nb_stops_metro", "nb_stops_train"]:
        if col not in df_pivot.columns:
            df_pivot[col] = 0.0

    df_pivot["codgeo"] = df_pivot["codgeo"].astype(str).str.zfill(5)
    df_pivot["nb_stops_total"] = (
        df_pivot["nb_stops_bus"]
        + df_pivot["nb_stops_tram"]
        + df_pivot["nb_stops_metro"]
        + df_pivot["nb_stops_train"]
    )

    output_path = CLEAN_DIR / "mob_transports_pub.parquet"
    df_pivot.to_parquet(output_path, engine="fastparquet")
    logger.log_step(
        "clean_mob_transports_pub",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_pivot)},
    )


def clean_log_soc_delay(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans USH Housing Delay and saves to parquet."""
    logger.log_step("clean_log_soc_delay", "STARTED")
    source = config["sources"]["logement_social_delay"]
    output_path = CLEAN_DIR / "log_soc_delay.parquet"

    # Odace pathway
    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_delai_attribution_logement")
            )
            if not df_odace.empty:
                # Filter out header/footer metadata lines (delay is NaN)
                df_odace = df_odace.dropna(subset=["delai_attribution_moyen_mois"])

                # Format columns
                df_odace = df_odace[["siret", "delai_attribution_moyen_mois"]].rename(
                    columns={
                        "siret": "epci_code",
                        "delai_attribution_moyen_mois": "log_soc_delay",
                    }
                )
                df_odace["epci_code"] = (
                    df_odace["epci_code"].astype(str).str.strip().str.zfill(9)
                )
                df_odace["log_soc_delay"] = pd.to_numeric(
                    df_odace["log_soc_delay"], errors="coerce"
                ).fillna(0)

                # Group by epci_code and average
                df_clean = (
                    df_odace.groupby("epci_code")["log_soc_delay"].mean().reset_index()
                )

                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_clean.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_log_soc_delay",
                    "COMPLETED",
                    {"rows": len(df_clean), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for logement_social_delay. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch logement_social_delay from Odace: {e}. Falling back to legacy."
            )

    # Legacy pathway
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    # USH range A3:B1263. load_dataset uses header=2 (row 3)
    df = load_dataset(path, source)

    if len(df) > 1260:
        df = df.iloc[:1260]

    if "SIRET" in df.columns and "Délai d'attribution moyen" in df.columns:
        df.rename(
            columns={
                "SIRET": "epci_code",
                "Délai d'attribution moyen": "log_soc_delay",
            },
            inplace=True,
        )
        df["epci_code"] = df["epci_code"].astype(str).str.strip().str.zfill(9)
        df["log_soc_delay"] = pd.to_numeric(
            df["log_soc_delay"], errors="coerce"
        ).fillna(0)

        # Group by epci_code and average
        df_clean = df.groupby("epci_code")["log_soc_delay"].mean().reset_index()

        df_clean.to_parquet(output_path, engine="fastparquet")
        logger.log_step("clean_log_soc_delay", "COMPLETED", {"rows": len(df_clean)})


def clean_sante_apl(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans DREES APL and saves to parquet."""
    logger.log_step("clean_sante_apl", "STARTED")
    source = config["sources"]["sante_apl"]
    output_path = CLEAN_DIR / "sante_apl.parquet"

    # Odace pathway
    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "fact_apl_medecin"))
            if not df_odace.empty:
                # Filter latest year if multiple years exist
                if "annee" in df_odace.columns:
                    latest = df_odace["annee"].max()
                    df_odace = df_odace[df_odace["annee"] == latest]

                df_out = df_odace[
                    ["commune_insee_code", "apl_medecin_generaliste"]
                ].rename(
                    columns={
                        "commune_insee_code": "codgeo",
                        "apl_medecin_generaliste": "sante_apl",
                    }
                )
                df_out["codgeo"] = df_out["codgeo"].astype(str).str.zfill(5)
                df_out["sante_apl"] = pd.to_numeric(
                    df_out["sante_apl"], errors="coerce"
                ).fillna(0)

                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_out.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_sante_apl",
                    "COMPLETED",
                    {"rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for sante_apl. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch sante_apl from Odace: {e}. Falling back to legacy."
            )

    # Legacy pathway
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)

    codgeo_col = "Code commune INSEE"
    val_col = "APL aux médecins généralistes"

    if codgeo_col in df.columns and val_col in df.columns:
        df = df[[codgeo_col, val_col]].rename(
            columns={codgeo_col: "codgeo", val_col: "sante_apl"}
        )
        df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)
        df["sante_apl"] = pd.to_numeric(df["sante_apl"], errors="coerce").fillna(0)

        df.to_parquet(output_path, engine="fastparquet")
        logger.log_step("clean_sante_apl", "COMPLETED", {"rows": len(df)})


def clean_mob_durable(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans Ecolab Mobility and saves to parquet."""
    logger.log_step("clean_mob_durable", "STARTED")
    source = config["sources"]["mob_durable_share"]
    output_path = CLEAN_DIR / "mob_durable.parquet"

    # Odace pathway
    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_mobilite_durable")
            )
            if not df_odace.empty:
                # Pivot
                df_pivot = df_odace.pivot_table(
                    index="commune_insee_code",
                    columns="mode_transport",
                    values="part_modale",
                    aggfunc="sum",
                ).reset_index()
                df_pivot.rename(columns={"commune_insee_code": "codgeo"}, inplace=True)
                df_pivot["codgeo"] = df_pivot["codgeo"].astype(str).str.zfill(5)

                durable_modes = ["Transports en commun", "Marche", "Vélo", "V\u00e9lo"]
                present_durable = [m for m in durable_modes if m in df_pivot.columns]

                mode_cols = [c for c in df_pivot.columns if c != "codgeo"]
                df_pivot["total_valeur"] = df_pivot[mode_cols].sum(axis=1)

                df_pivot["mob_dur_share"] = np.where(
                    df_pivot["total_valeur"] > 0,
                    df_pivot[present_durable].sum(axis=1) / df_pivot["total_valeur"],
                    0.0,
                )

                df_out = df_pivot[["codgeo", "mob_dur_share"]]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_out.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_mob_durable",
                    "COMPLETED",
                    {"rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for mob_durable_share. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch mob_durable_share from Odace: {e}. Falling back to legacy."
            )

    # Legacy pathway
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)

    if all(c in df.columns for c in ["geocode_commune", "mode_transport", "valeur"]):
        if "date_mesure" in df.columns:
            latest = df["date_mesure"].max()
            df = df[df["date_mesure"] == latest]

        df_pivot = df.pivot_table(
            index="geocode_commune",
            columns="mode_transport",
            values="valeur",
            aggfunc="sum",
        ).reset_index()
        df_pivot.rename(columns={"geocode_commune": "codgeo"}, inplace=True)
        df_pivot["codgeo"] = df_pivot["codgeo"].astype(str).str.zfill(5)

        durable_modes = ["Transports en commun", "Marche", "Vélo", "V\u00e9lo"]
        present_durable = [m for m in durable_modes if m in df_pivot.columns]

        mode_cols = [c for c in df_pivot.columns if c != "codgeo"]
        df_pivot["total_valeur"] = df_pivot[mode_cols].sum(axis=1)

        df_pivot["mob_dur_share"] = np.where(
            df_pivot["total_valeur"] > 0,
            df_pivot[present_durable].sum(axis=1) / df_pivot["total_valeur"],
            0.0,
        )

        df_out = df_pivot[["codgeo", "mob_dur_share"]]
        df_out.to_parquet(output_path, engine="fastparquet")
        logger.log_step("clean_mob_durable", "COMPLETED", {"rows": len(df_out)})


def clean_ter_insecurite(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans SSMSI Insecurity and saves to parquet."""
    logger.log_step("clean_ter_insecurite", "STARTED")
    source = config["sources"]["ter_insecurite"]
    output_path = CLEAN_DIR / "ter_insecurite.parquet"

    # Odace pathway
    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_insecurite_commune")
            )
            if not df_odace.empty:
                # Filter for latest year
                latest = df_odace["annee"].max()
                df_odace = df_odace[df_odace["annee"] == latest]

                df_agg = (
                    df_odace.groupby("commune_insee_code")["taux_pour_mille"]
                    .sum()
                    .reset_index()
                )
                df_agg.rename(
                    columns={
                        "commune_insee_code": "codgeo",
                        "taux_pour_mille": "ter_insecurite",
                    },
                    inplace=True,
                )
                df_agg["codgeo"] = df_agg["codgeo"].astype(str).str.zfill(5)

                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_agg.to_parquet(output_path, engine="fastparquet")
                logger.log_step(
                    "clean_ter_insecurite",
                    "COMPLETED",
                    {"rows": len(df_agg), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for ter_insecurite. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch ter_insecurite from Odace: {e}. Falling back to legacy."
            )

    # Legacy pathway
    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = load_dataset(path, source)

    if all(
        c in df.columns
        for c in ["CODGEO_2025", "annee", "indicateur", "taux_pour_mille"]
    ):
        latest = df["annee"].max()
        df = df[df["annee"] == latest]

        df_agg = df.groupby("CODGEO_2025")["taux_pour_mille"].sum().reset_index()
        df_agg.rename(
            columns={"CODGEO_2025": "codgeo", "taux_pour_mille": "ter_insecurite"},
            inplace=True,
        )
        df_agg["codgeo"] = df_agg["codgeo"].astype(str).str.zfill(5)

        df_agg.to_parquet(output_path, engine="fastparquet")
        logger.log_step("clean_ter_insecurite", "COMPLETED", {"rows": len(df_agg)})


def clean_jaccueille(config: Dict[str, Any], logger: PipelineLogger):
    """Cleans J'Accueille data and aggregates by Bassin de Vie."""
    logger.log_step("clean_jaccueille", "STARTED")
    source = config.get("local_files", {}).get("jaccueille")
    if not source:
        source = config["sources"].get("jaccueille")

    if not source:
        logging.warning("J'Accueille source config not found.")
        return

    if source.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "dim_accueillant"))
            if not df_odace.empty:
                # Group by commune_sk to count hosts
                df_counts = (
                    df_odace.groupby("commune_sk")
                    .size()
                    .rename("heb_jaccueille_count")
                    .reset_index()
                )

                # Join with dim_commune to get commune_insee_code
                df_commune = client.fetch_dim_commune()
                if not df_commune.empty:
                    merged = df_counts.merge(
                        df_commune[["commune_sk", "commune_insee_code"]],
                        on="commune_sk",
                        how="inner",
                    )
                    merged.rename(
                        columns={"commune_insee_code": "codgeo"}, inplace=True
                    )
                    merged["codgeo"] = merged["codgeo"].astype(str).str.zfill(5)

                    # Merge with Bassin de Vie mapping
                    bdv_cfg = config["sources"]["bassins_de_vie"]
                    bdv_path = CACHE_DIR / bdv_cfg["archive_file"]

                    if bdv_path.exists():
                        df_bdv = load_dataset(bdv_path, bdv_cfg)
                        codgeo_col = next(
                            (
                                c
                                for c in df_bdv.columns
                                if "Code géographique" in c or "CODGEO" in c
                            ),
                            None,
                        )
                        bdv_col = next(
                            (c for c in df_bdv.columns if "Bassin de vie" in c), None
                        )

                        if codgeo_col and bdv_col:
                            df_bdv = df_bdv[[codgeo_col, bdv_col]].rename(
                                columns={codgeo_col: "codgeo", bdv_col: "bassin_de_vie"}
                            )
                            df_bdv["codgeo"] = df_bdv["codgeo"].astype(str).str.zfill(5)
                            df_bdv["bassin_de_vie"] = (
                                df_bdv["bassin_de_vie"]
                                .astype(str)
                                .str.replace(r"\.0$", "", regex=True)
                            )

                            merged_bdv = merged.merge(df_bdv, on="codgeo", how="inner")
                            df_agg = (
                                merged_bdv.groupby("bassin_de_vie")[
                                    "heb_jaccueille_count"
                                ]
                                .sum()
                                .reset_index()
                            )

                            output_path = CLEAN_DIR / "jaccueille_bdv.parquet"
                            df_agg.to_parquet(output_path, engine="fastparquet")
                            logger.log_step(
                                "clean_jaccueille",
                                "COMPLETED",
                                {"rows": len(df_agg), "source": "odace"},
                            )
                            return
                        else:
                            logging.warning(
                                "Bassin de vie columns not identified for Odace J'Accueille mapping."
                            )
                    else:
                        logging.warning(
                            "Bassin de vie file not found for Odace J'Accueille mapping."
                        )
                else:
                    logging.warning(
                        "Odace dim_commune empty for J'Accueille. Falling back to legacy."
                    )
            else:
                logging.warning(
                    "Odace fetch returned empty data for J'Accueille. Falling back to legacy."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch J'Accueille from Odace: {e}. Falling back to legacy."
            )

    path = CACHE_DIR / source["local_name"]
    if not path.exists():
        # Maybe it's directly in local? The fetch_source should have copied it.
        logging.warning(f"J'Accueille file not found at {path}.")
        return

    # 1. Load J'Accueille CSV
    try:
        df = pd.read_csv(path)
    except Exception as e:
        df = pd.read_csv(path, sep=";")  # fallback

    # Expected columns: 'Code postal', 'Nombre d'enregistrements'
    cp_col = next(
        (c for c in df.columns if "Code postal" in c or "code_postal" in c), None
    )
    val_col = next(
        (
            c
            for c in df.columns
            if "Nombre d'enregistrements" in c
            or "accueillants" in c.lower()
            or "count" in c.lower()
        ),
        None,
    )

    if not cp_col or not val_col:
        logging.warning(f"J'Accueille: Could not identify columns. Found: {df.columns}")
        return

    df = df.rename(columns={cp_col: "code_postal", val_col: "heb_jaccueille_count"})
    df["code_postal"] = (
        df["code_postal"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5)
    )
    df["heb_jaccueille_count"] = pd.to_numeric(
        df["heb_jaccueille_count"], errors="coerce"
    ).fillna(0)

    # 2. Map Code Postal -> Code Commune -> Bassin de Vie
    # A. Code Postal -> Commune (using official mapping)
    cp_mapping_path = CLEAN_DIR / "codes_postaux.parquet"
    if not cp_mapping_path.exists():
        logging.warning("Codes Postaux mapping not found, cannot map J'Accueille data.")
        return

    df_cp = pd.read_parquet(
        cp_mapping_path, engine="fastparquet"
    )  # 'code_postal', 'codgeo'

    # Take the first commune for a given postal code (since 1 CP maps to 1 BDV eventually)
    df_cp_unique = df_cp.drop_duplicates(subset=["code_postal"], keep="first")

    merged = df.merge(df_cp_unique, on="code_postal", how="inner")

    # B. Commune -> Bassin de Vie (using our pre-processed mapping or from BDV dataset)
    # The BDV mapping is usually applied in `build.py`, but we can extract it from the raw BDV file or communes_pre if it exists.
    # Let's read it from the raw BDV file loaded earlier (bassins_de_vie)
    bdv_cfg = config["sources"]["bassins_de_vie"]
    bdv_path = CACHE_DIR / bdv_cfg["archive_file"]

    if not bdv_path.exists():
        logging.warning("Bassin de vie raw file not found.")
        return

    df_bdv = load_dataset(bdv_path, bdv_cfg)

    codgeo_col = next(
        (c for c in df_bdv.columns if "Code géographique" in c or "CODGEO" in c), None
    )
    bdv_col = next((c for c in df_bdv.columns if "Bassin de vie" in c), None)

    if codgeo_col and bdv_col:
        df_bdv = df_bdv[[codgeo_col, bdv_col]].rename(
            columns={codgeo_col: "codgeo", bdv_col: "bassin_de_vie"}
        )
        df_bdv["codgeo"] = df_bdv["codgeo"].astype(str).str.zfill(5)
        df_bdv["bassin_de_vie"] = (
            df_bdv["bassin_de_vie"].astype(str).str.replace(r"\.0$", "", regex=True)
        )

        merged_bdv = merged.merge(df_bdv, on="codgeo", how="inner")

        # Aggregate by BDV
        df_agg = (
            merged_bdv.groupby("bassin_de_vie")["heb_jaccueille_count"]
            .sum()
            .reset_index()
        )

        output_path = CLEAN_DIR / "jaccueille_bdv.parquet"
        df_agg.to_parquet(output_path, engine="fastparquet")
        logger.log_step(
            "clean_jaccueille",
            "COMPLETED",
            {"path": str(output_path), "rows": len(df_agg)},
        )
    else:
        logging.warning("Bassin de vie columns not identified for mapping.")


if __name__ == "__main__":
    main()
