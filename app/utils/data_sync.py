"""GCS dataset synchronization and graceful fallback manager for ODIS.

Handles container startup, runtime, and build-time synchronization of immutable
dataset releases from Google Cloud Storage to local disk with cryptographic
SHA-256 validation, atomic directory staging, and automatic fallback to previous
valid releases in case of corruption.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import storage

import config as cfg

logger = logging.getLogger(__name__)

_SYNC_LOCK = threading.Lock()

# Complete runtime dataset filenames contract
RUNTIME_DATASET_FILENAMES = (
    cfg.REFERENTIELS_FILE,
    cfg.ODIS_FILE,
    cfg.BV_FILE,
    cfg.POIS_FILE,
    cfg.AGG_ASSOCIATIONS_FILE,
    cfg.AGG_FORMATIONS_FILE,
    cfg.CCAS_FILE,
    cfg.REFUGEE_ASSOCIATIONS_FILE,
    cfg.LIVE_JOBS_FILE,
    cfg.SIAE_JOBS_FILE,
    cfg.SALESFORCE_JACCUEILLE_BDV_FILE,
)


def get_default_datasets_dir() -> Path:
    """Resolve the default local directory for active Parquet datasets.

    Returns:
        Path anchored on environment overrides or cfg.APP_DIR.
    """
    for env_var in ("ODIS_DATASETS_DIR", "ODIS_CACHE_DIR", "ODIS_DATA_CACHE_DIR"):
        if val := os.getenv(env_var):
            return Path(val)
    return Path(cfg.APP_DIR) / "data" / "datasets" / "active"


def get_default_static_dir() -> Path:
    """Resolve the default local directory for static GeoJSON assets.

    Returns:
        Path anchored on cfg.APP_DIR/static/data.
    """
    return Path(cfg.APP_DIR) / "static" / "data"


def compute_file_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file.

    Args:
        file_path: Absolute or relative path to the file.

    Returns:
        Hexadecimal SHA-256 string.
    """
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_and_verify_release(
    bucket: storage.Bucket,
    prefix_clean: str,
    release_version: str,
    target_dir: Path,
    static_dir: Path,
    force: bool = False,
    manifest_blob_name: Optional[str] = None,
    expected_manifest_sha: Optional[str] = None,
    pointer_raw: Optional[bytes] = None,
) -> Dict[str, Any]:
    """Download and verify all artifacts for one release in an isolated staging dir.

    Files are first downloaded and SHA-256 verified inside a temporary staging
    directory. Target files in target_dir and static_dir are only replaced once
    every artifact in the release has passed verification, preventing partial
    overwrites on mid-sync failure.

    Args:
        bucket: GCS bucket instance.
        prefix_clean: Clean GCS prefix (e.g. 'datasets').
        release_version: Immutable release version identifier.
        target_dir: Destination path for Parquet datasets and manifests.
        static_dir: Destination path for static cartographic GeoJSON.
        force: If True, re-download all files regardless of local checksums.
        manifest_blob_name: Optional explicit manifest blob name.
        expected_manifest_sha: Optional expected SHA-256 for the manifest.
        pointer_raw: Optional raw bytes of current.json to persist locally.

    Returns:
        Dictionary with sync summary: version, downloaded_files, skipped_files.

    Raises:
        RuntimeError: If download fails or any SHA-256 checksum mismatches.
    """
    manifest_name = manifest_blob_name or "data_manifest.json"
    manifest_blob = bucket.blob(
        f"{prefix_clean}/releases/{release_version}/{manifest_name}"
    )
    if not manifest_blob.exists():
        manifest_blob = bucket.blob(f"{prefix_clean}/{manifest_name}")

    if not manifest_blob.exists():
        raise RuntimeError(
            f"Release manifest missing: gs://{bucket.name}/{prefix_clean}/releases/{release_version}/{manifest_name}"
        )

    manifest_bytes = manifest_blob.download_as_bytes()
    if expected_manifest_sha:
        actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_manifest_sha != expected_manifest_sha:
            raise RuntimeError(
                f"Release manifest checksum mismatch: expected {expected_manifest_sha}, got {actual_manifest_sha}"
            )

    manifest = json.loads(manifest_bytes)
    outputs: List[Dict[str, Any]] = manifest.get("outputs", [])
    if not outputs:
        raise RuntimeError(
            f"Release manifest for {release_version} contains no outputs"
        )

    downloaded: List[str] = []
    skipped: List[str] = []

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = (
        target_dir.parent
        / f".staging_{release_version}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    )
    staged_moves: List[Tuple[Path, Path, str]] = []

    try:
        staging_dir.mkdir(parents=True, exist_ok=True)

        for item in outputs:
            name = item.get("name")
            expected_sha = item.get("sha256")
            size_bytes = item.get("size_bytes", 0)

            if not name or not expected_sha:
                continue

            if name == "communes_france.geojson":
                dest_file = static_dir / "communes_france.geojson"
            else:
                dest_file = target_dir / name

            if (
                not force
                and dest_file.exists()
                and dest_file.stat().st_size == size_bytes
            ):
                if compute_file_sha256(dest_file) == expected_sha:
                    logger.info("Artifact already up to date: %s", name)
                    skipped.append(name)
                    continue

            logger.info(
                "Downloading %s (%.2f MB) for release %s...",
                name,
                size_bytes / (1024 * 1024),
                release_version,
            )
            blob = bucket.blob(f"{prefix_clean}/releases/{release_version}/{name}")
            staged_file = staging_dir / name
            blob.download_to_filename(str(staged_file))
            actual_sha = compute_file_sha256(staged_file)
            if actual_sha != expected_sha:
                raise RuntimeError(
                    f"Checksum mismatch on {name}: expected {expected_sha}, got {actual_sha}"
                )
            staged_moves.append((staged_file, dest_file, name))

        # Standalone communes_france.geojson if not declared in manifest outputs
        static_geojson = static_dir / "communes_france.geojson"
        already_handled_geojson = "communes_france.geojson" in skipped or any(
            name == "communes_france.geojson" for _, _, name in staged_moves
        )
        if not already_handled_geojson and (force or not static_geojson.exists()):
            release_geojson_blob = bucket.blob(
                f"{prefix_clean}/releases/{release_version}/communes_france.geojson"
            )
            static_geojson_blob = bucket.blob(
                f"{prefix_clean}/static/communes_france.geojson"
            )

            target_blob = None
            if release_geojson_blob.exists():
                target_blob = release_geojson_blob
            elif static_geojson_blob.exists():
                target_blob = static_geojson_blob

            if target_blob:
                logger.info(
                    "Downloading communes_france.geojson from %s...", target_blob.name
                )
                staged_geojson = staging_dir / "communes_france.geojson"
                target_blob.download_to_filename(str(staged_geojson))
                staged_moves.append(
                    (staged_geojson, static_geojson, "communes_france.geojson")
                )

        # Stage manifest and pointer before committing
        staged_manifest = staging_dir / "data_manifest.json"
        staged_manifest.write_bytes(manifest_bytes)
        staged_pointer: Optional[Path] = None
        if pointer_raw:
            staged_pointer = staging_dir / "current.json"
            staged_pointer.write_bytes(pointer_raw)

        # Commit phase: all checksums verified, atomically move into target_dir / static_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        static_dir.mkdir(parents=True, exist_ok=True)

        for staged_file, dest_file, name in staged_moves:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_file), str(dest_file))
            downloaded.append(name)

        shutil.move(str(staged_manifest), str(target_dir / "data_manifest.json"))
        if staged_pointer is not None:
            shutil.move(str(staged_pointer), str(target_dir / "current.json"))

    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)

    logger.info(
        "Sync completed successfully for release %s: %d downloaded, %d already up to date.",
        release_version,
        len(downloaded),
        len(skipped),
    )

    return {
        "version": release_version,
        "downloaded_files": downloaded,
        "skipped_files": skipped,
    }


def sync_active_release(
    bucket_name: str,
    prefix: str = "datasets",
    target_dir: Optional[Path] = None,
    static_dir: Optional[Path] = None,
    force: bool = False,
    storage_client: Optional[storage.Client] = None,
    allow_fallback: bool = True,
) -> Dict[str, Any]:
    """Synchronize the active GCS dataset release with automatic fallback.

    Args:
        bucket_name: Name of the GCS bucket.
        prefix: Path prefix in the bucket (default 'datasets').
        target_dir: Local directory for parquet datasets and manifests.
        static_dir: Local directory for cartographic GeoJSON.
        force: If True, re-download all files regardless of local checksums.
        storage_client: Optional injected google.cloud.storage.Client.
        allow_fallback: If True, falls back to previous_version on error.

    Returns:
        Dictionary with sync summary: version, downloaded_files, skipped_files, and optional fallback flag.

    Raises:
        RuntimeError: If release pointer is missing or release download fails.
    """
    if target_dir is None:
        target_dir = get_default_datasets_dir()
    if static_dir is None:
        static_dir = get_default_static_dir()

    client = storage_client or storage.Client()
    bucket = client.bucket(bucket_name)

    # 1. Download and parse current.json pointer
    prefix_clean = prefix.strip("/")
    pointer_blob = bucket.blob(f"{prefix_clean}/current.json")
    if not pointer_blob.exists():
        raise RuntimeError(
            f"Active dataset pointer missing: gs://{bucket_name}/{prefix_clean}/current.json"
        )

    try:
        pointer_raw = pointer_blob.download_as_bytes()
        pointer = json.loads(pointer_raw)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse active release pointer: {exc}") from exc

    release_version = pointer.get("version")
    previous_version = pointer.get("previous_version")
    manifest_info = pointer.get("manifest")
    if not isinstance(release_version, str) or not re.fullmatch(
        r"[A-Za-z0-9._-]+", release_version
    ):
        raise RuntimeError(f"Invalid release version identifier: {release_version!r}")

    manifest_blob_name = None
    expected_manifest_sha = None
    if isinstance(manifest_info, dict):
        manifest_blob_name = manifest_info.get("name")
        expected_manifest_sha = manifest_info.get("sha256")

    # 2. Attempt download of active release
    try:
        summary = download_and_verify_release(
            bucket=bucket,
            prefix_clean=prefix_clean,
            release_version=release_version,
            target_dir=target_dir,
            static_dir=static_dir,
            force=force,
            manifest_blob_name=manifest_blob_name,
            expected_manifest_sha=expected_manifest_sha,
            pointer_raw=pointer_raw,
        )
        return summary
    except Exception as exc:
        if allow_fallback and previous_version and isinstance(previous_version, str):
            logger.error(
                "❌ [DATA SYNC ERROR] Active release '%s' failed validation: %s. "
                "Attempting graceful fallback to previous release '%s'...",
                release_version,
                exc,
                previous_version,
                exc_info=True,
            )
            try:
                fallback_pointer = dict(pointer)
                fallback_pointer["version"] = previous_version
                fallback_pointer["fallback_from_version"] = release_version
                fallback_pointer.pop("manifest", None)
                fallback_pointer.pop("files", None)
                fallback_pointer_raw = json.dumps(
                    fallback_pointer, ensure_ascii=False, indent=2
                ).encode("utf-8")

                summary = download_and_verify_release(
                    bucket=bucket,
                    prefix_clean=prefix_clean,
                    release_version=previous_version,
                    target_dir=target_dir,
                    static_dir=static_dir,
                    force=force,
                    manifest_blob_name="data_manifest.json",
                    expected_manifest_sha=None,
                    pointer_raw=fallback_pointer_raw,
                )
                summary["fallback"] = True
                summary["target_version"] = release_version
                logger.warning(
                    "⚠️ [DATA SYNC FALLBACK] Service successfully recovered using previous dataset release '%s'.",
                    previous_version,
                )
                return summary
            except Exception as fallback_exc:
                logger.critical(
                    "🚨 [DATA SYNC FATAL] Fallback to previous release '%s' also failed: %s",
                    previous_version,
                    fallback_exc,
                    exc_info=True,
                )
        logger.error(
            "❌ [DATA SYNC ERROR] Dataset synchronization failed for release '%s': %s",
            release_version,
            exc,
            exc_info=True,
        )
        raise


def are_local_datasets_complete(target_dir: Path) -> bool:
    """Check if all runtime datasets and data_manifest.json exist locally.

    Args:
        target_dir: Directory containing local datasets.

    Returns:
        True if all required runtime datasets are present on disk.
    """
    if not target_dir.is_dir():
        return False
    if not (target_dir / "data_manifest.json").is_file():
        return False
    return all((target_dir / fname).is_file() for fname in RUNTIME_DATASET_FILENAMES)


def ensure_datasets_available(
    target_dir: Optional[Path] = None,
    static_dir: Optional[Path] = None,
    bucket_name: Optional[str] = None,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """Ensure datasets are available on local disk, syncing from GCS if needed.

    Protected by a process-wide threading lock so concurrent Streamlit sessions
    never trigger parallel downloads. In local development, if all runtime
    datasets are already present in target_dir, they are used immediately
    without network calls.

    Args:
        target_dir: Target directory for Parquet datasets.
        static_dir: Target directory for static GeoJSON.
        bucket_name: GCS bucket name (defaults to GCS_DATASETS_BUCKET env var).
        force: If True, forces re-download and verification.

    Returns:
        Sync summary dictionary if a sync was executed, or None if already up-to-date.
    """
    with _SYNC_LOCK:
        if target_dir is None:
            target_dir = get_default_datasets_dir()
        if static_dir is None:
            static_dir = get_default_static_dir()

        if not force and are_local_datasets_complete(target_dir):
            logger.info(
                "📦 [DATASETS] All %d runtime datasets are present in %s",
                len(RUNTIME_DATASET_FILENAMES),
                target_dir,
            )
            return None

        resolved_bucket = bucket_name or os.getenv("GCS_DATASETS_BUCKET")
        if not resolved_bucket:
            logger.warning(
                "⚠️ [DATASETS] Local datasets incomplete in %s, but GCS_DATASETS_BUCKET is not set.",
                target_dir,
            )
            return None

        logger.info(
            "📡 [DATASETS] Local datasets missing or incomplete in %s. Synchronizing from gs://%s...",
            target_dir,
            resolved_bucket,
        )
        return sync_active_release(
            bucket_name=resolved_bucket,
            target_dir=target_dir,
            static_dir=static_dir,
            force=force,
        )


def _startup_cli() -> None:
    """Container startup CLI entrypoint to pre-hydrate datasets before Streamlit."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        ensure_datasets_available()
    except Exception as exc:
        logger.critical(
            "🚨 [CONTAINER STARTUP] Fatal dataset initialization failure: %s",
            exc,
            exc_info=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    _startup_cli()
