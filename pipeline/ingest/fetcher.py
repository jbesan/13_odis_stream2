"""Data acquisition, remote download, metadata validation, and staging extraction."""

from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional
import zipfile

import requests

from pipeline.common import (
    PipelineLogger,
    fetch_remote_metadata_datagouv,
    is_cache_valid,
)
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context


def fetch_source(
    name: str, source_cfg: Dict[str, Any], logger: PipelineLogger
) -> Optional[Path]:
    """Downloads and prepares a single source with caching, metadata checks, and staging."""
    if source_cfg.get("use_odace", False):
        logging.info(f"[Fetch] {name}: managed by Odace. Skipping legacy download.")
        logger.log_source(name, "SKIPPED", "managed by Odace")
        return None

    resource_id = source_cfg.get("datagouv_resource_id")
    url = source_cfg.get("url")
    if not url and resource_id:
        url = f"https://www.data.gouv.fr/api/1/datasets/r/{resource_id}"

    if not url:
        logger.log_source(name, "SKIPPED", "No URL provided")
        return None

    local_name = source_cfg["local_name"]
    local_path = context.CACHE_DIR / local_name

    context.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    ttl_days = source_cfg["ttl_days"]
    if is_cache_valid(name, source_cfg):
        logging.info(
            f"[Fetch] {name}: Local cache is valid (TTL={ttl_days} days). Skipping fetch."
        )
        logger.log_source(name, "CACHED", local_path)
        if source_cfg.get("format") == "zip" and "archive_file" in source_cfg:
            extracted_path = context.CACHE_DIR / source_cfg["archive_file"]
            return extracted_path
        return local_path

    staging_local_path = context.CACHE_DIR / f"staging_{local_name}"
    download_url = url

    if local_path.exists() and resource_id:
        meta = fetch_remote_metadata_datagouv(resource_id)
        if meta and "last_modified" in meta:
            try:
                remote_mtime = datetime.fromisoformat(
                    meta["last_modified"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
                local_mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
                if remote_mtime <= local_mtime:
                    logging.info(
                        f"[Fetch] {name} is up-to-date on data.gouv.fr (remote: {remote_mtime}, local: {local_mtime}). Skipping download and resetting TTL."
                    )
                    os.utime(local_path, None)
                    logger.log_source(name, "CACHED", local_path)
                    if (
                        source_cfg.get("format") == "zip"
                        and "archive_file" in source_cfg
                    ):
                        extracted_path = context.CACHE_DIR / source_cfg["archive_file"]
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

    if local_path.exists() and not resource_id:
        mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        logging.info(
            f"🔔 [REMINDER] Cache for dataset '{name}' is {age_days} days old (TTL={ttl_days}). Please check manually if a new version is available on the provider's site."
        )

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

        cleaned_sources = set(get_domain_contracts().runner.cleaned_sources)

        if source_cfg.get("format") == "zip" and "archive_file" in source_cfg:
            extracted_file = source_cfg["archive_file"]
            staging_extracted_path = context.CACHE_DIR / f"staging_{extracted_file}"
            logging.info(
                f"[Fetch] {name}: Extracting zip member '{extracted_file}' to staging path..."
            )
            with zipfile.ZipFile(staging_local_path, "r") as z:
                with open(staging_extracted_path, "wb") as f_out:
                    f_out.write(z.read(extracted_file))

            if name not in cleaned_sources and not name.startswith("test_"):
                logging.info(
                    f"🔄 [Static Source] Swapping staging files to active for '{name}' immediately."
                )
                active_extracted_path = context.CACHE_DIR / extracted_file
                active_extracted_path.unlink(missing_ok=True)
                os.rename(staging_extracted_path, active_extracted_path)

                local_path.unlink(missing_ok=True)
                os.rename(staging_local_path, local_path)
                return active_extracted_path

            return staging_extracted_path

        if name not in cleaned_sources and not name.startswith("test_"):
            logging.info(
                f"🔄 [Static Source] Swapping staging raw file for '{name}' to active."
            )
            local_path.unlink(missing_ok=True)
            os.rename(staging_local_path, local_path)
            return local_path

        return staging_local_path
    except Exception as e:
        logging.error(f"[Fetch] {name} Failed: {e}")
        if local_path.exists():
            logging.warning(
                f"⚠️ Failed to download updated version of {name}. Falling back to cached copy."
            )
            logger.log_source(name, "FALLBACK_LAST_GOOD", str(e))
            if source_cfg.get("format") == "zip" and "archive_file" in source_cfg:
                return context.CACHE_DIR / source_cfg["archive_file"]
            return local_path
        logger.log_source(name, "FAILED", str(e))
        return None


def fetch_rome_referential(
    config: Dict[str, Any], logger: PipelineLogger
) -> Optional[Path]:
    """Deprecated: Standard cleaner clean_rome() in pipeline.ingest.cleaners.jobs should be used instead.

    Executes clean_rome and returns CLEAN_DIR / "rome.parquet" for backwards compatibility.
    """
    from pipeline.ingest.cleaners.jobs import clean_rome

    clean_rome(config, logger)
    output_path = context.CLEAN_DIR / "rome.parquet"
    return output_path if output_path.exists() else None

