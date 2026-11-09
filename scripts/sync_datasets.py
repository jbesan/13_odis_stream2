"""Build-time and CLI synchronization script for ODIS datasets.

Re-exports core synchronization routines from app.utils.data_sync and provides
the CLI entrypoint used during Cloud Build and manual operations.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Automatically load app/.env or .env in local development
load_dotenv("app/.env")
load_dotenv(".env")

# Ensure app directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from utils.data_sync import (  # noqa: E402
    compute_file_sha256,
    download_and_verify_release,
    ensure_datasets_available,
    get_default_datasets_dir,
    get_default_static_dir,
    sync_active_release,
)

__all__ = [
    "compute_file_sha256",
    "download_and_verify_release",
    "ensure_datasets_available",
    "get_default_datasets_dir",
    "get_default_static_dir",
    "sync_active_release",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sync_datasets")


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
        default=get_default_datasets_dir(),
        help="Target folder for datasets (default: app/data/datasets/active)",
    )
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=get_default_static_dir(),
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
