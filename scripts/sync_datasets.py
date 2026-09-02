"""Build-time and local developer synchronization script for ODIS datasets.

Downloads the active dataset release artifacts and cartographic GeoJSON from
Google Cloud Storage into the local filesystem before container packaging or
local execution, eliminating runtime download overhead and network latency.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from google.cloud import storage

# Automatically load app/.env or .env in local development
load_dotenv("app/.env")
load_dotenv(".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sync_datasets")


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


def sync_active_release(
    bucket_name: str,
    prefix: str = "datasets",
    target_dir: Optional[Path] = None,
    static_dir: Optional[Path] = None,
    force: bool = False,
    storage_client: Optional[storage.Client] = None,
) -> Dict[str, Any]:
    """Synchronize the active GCS dataset release to local directories.

    Args:
        bucket_name: Name of the GCS bucket.
        prefix: Path prefix in the bucket (default 'datasets').
        target_dir: Local directory for parquet datasets and manifests.
        static_dir: Local directory for cartographic GeoJSON.
        force: If True, re-download all files regardless of local checksums.
        storage_client: Optional injected google.cloud.storage.Client.

    Returns:
        Dictionary with sync summary: version, downloaded_files, skipped_files.

    Raises:
        RuntimeError: If release pointer or manifest is invalid or checksums fail.
    """
    if target_dir is None:
        target_dir = Path("app/data/datasets/active")
    if static_dir is None:
        static_dir = Path("app/static/data")

    target_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)

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
    manifest_info = pointer.get("manifest")
    if not isinstance(release_version, str) or not re.fullmatch(
        r"[A-Za-z0-9._-]+", release_version
    ):
        raise RuntimeError(f"Invalid release version in pointer: {release_version}")
    if not isinstance(manifest_info, dict):
        raise RuntimeError("Pointer metadata missing 'manifest' definition")

    manifest_name = manifest_info.get("name", "data_manifest.json")
    expected_manifest_sha256 = manifest_info.get("sha256")

    logger.info("Found active release: %s", release_version)

    # 2. Download and verify data_manifest.json
    manifest_blob = bucket.blob(
        f"{prefix_clean}/releases/{release_version}/{manifest_name}"
    )
    manifest_bytes = manifest_blob.download_as_bytes()
    actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise RuntimeError(
            f"Checksum mismatch on {manifest_name}: expected {expected_manifest_sha256}, got {actual_manifest_sha256}"
        )

    manifest = json.loads(manifest_bytes)
    outputs: List[Dict[str, Any]] = manifest.get("outputs", [])
    if not outputs:
        raise RuntimeError("Manifest contains no output artifacts")

    downloaded: List[str] = []
    skipped: List[str] = []

    # 3. Download artifacts
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

        logger.info("Downloading %s (%.2f MB)...", name, size_bytes / (1024 * 1024))
        blob = bucket.blob(f"{prefix_clean}/releases/{release_version}/{name}")

        tmp_dest = dest_file.with_suffix(f".tmp.{os.getpid()}")
        try:
            blob.download_to_filename(str(tmp_dest))
            actual_sha = compute_file_sha256(tmp_dest)
            if actual_sha != expected_sha:
                raise RuntimeError(
                    f"Checksum mismatch on {name}: expected {expected_sha}, got {actual_sha}"
                )
            tmp_dest.replace(dest_file)
            downloaded.append(name)
        finally:
            if tmp_dest.exists():
                tmp_dest.unlink()

    # 4. Check for standalone communes_france.geojson if not declared in release outputs
    static_geojson = static_dir / "communes_france.geojson"
    if "communes_france.geojson" not in downloaded and "communes_france.geojson" not in skipped:
        release_geojson_blob = bucket.blob(
            f"{prefix_clean}/releases/{release_version}/communes_france.geojson"
        )
        static_geojson_blob = bucket.blob(f"{prefix_clean}/static/communes_france.geojson")

        target_blob = None
        if release_geojson_blob.exists():
            target_blob = release_geojson_blob
        elif static_geojson_blob.exists():
            target_blob = static_geojson_blob

        if target_blob:
            logger.info("Downloading communes_france.geojson from %s...", target_blob.name)
            tmp_dest = static_geojson.with_suffix(f".tmp.{os.getpid()}")
            try:
                target_blob.download_to_filename(str(tmp_dest))
                tmp_dest.replace(static_geojson)
                downloaded.append("communes_france.geojson")
            finally:
                if tmp_dest.exists():
                    tmp_dest.unlink()

    # 5. Persist manifest and pointer in target directory for local provenance
    (target_dir / "data_manifest.json").write_bytes(manifest_bytes)
    (target_dir / "current.json").write_bytes(pointer_raw)

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


def main() -> None:
    """CLI entrypoint for dataset synchronization."""
    parser = argparse.ArgumentParser(
        description="Sync active GCS dataset release to local directories"
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_DATASETS_BUCKET"),
        help="GCS bucket name (defaults to GCS_DATASETS_BUCKET env var)",
    )
    parser.add_argument(
        "--prefix",
        default=os.getenv("GCS_DATASETS_PREFIX", "datasets"),
        help="GCS prefix (default: datasets)",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path("app/data/datasets/active"),
        help="Target folder for datasets (default: app/data/datasets/active)",
    )
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=Path("app/static/data"),
        help="Target folder for static geojson (default: app/static/data)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force redownload of all artifacts",
    )

    args = parser.parse_args()
    if not args.bucket:
        logger.error("Bucket must be specified via --bucket or GCS_DATASETS_BUCKET")
        sys.exit(1)

    try:
        sync_active_release(
            bucket_name=args.bucket,
            prefix=args.prefix,
            target_dir=args.target_dir,
            static_dir=args.static_dir,
            force=args.force,
        )
    except Exception as exc:
        logger.error("Dataset sync failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
