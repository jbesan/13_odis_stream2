import argparse
import logging
import sys
import os

# Add app directory to sys.path to allow imports from app/
sys.path.append(os.path.join(os.getcwd(), "app"))

from pipeline import ingest, build, prescoring
import shutil
import os
from pathlib import Path
from google.cloud import storage

SOURCE_DIR = Path("pipeline/cache/output")
DEST_DATA_DIR = Path("app/data")
DEST_DATASETS_DIR = Path("app/data/datasets")

BOOTSTRAP_FILES = [
    "odis_referentiels.parquet",
    "data_manifest.json",
]

DATASET_FILES = [
    "odis_communes.parquet",
    "odis_bassins_de_vie.parquet",
    "odis_pois.parquet",
    "odis_associations_agg.parquet",
    "odis_formations_agg.parquet",
    "odis_ccas.parquet",
    "odis_refugee_associations.parquet",
    "odis_ft_jobs_agg.parquet",
    "odis_inclusion_jobs.parquet",
    "salesforce_jaccueille_bdv.parquet",
]



def main():
    parser = argparse.ArgumentParser(description="ODIS Data Pipeline ETL")
    parser.add_argument(
        "--step",
        choices=["ingest", "build", "prescoring", "deploy", "all"],
        default="all",
        help="Step to run",
    )
    parser.add_argument(
        "--table",
        "--tables",
        "--steps",
        dest="steps",
        type=str,
        help="Specific table(s) or step(s) to process (comma-separated, e.g. communes,population)",
    )
    parser.add_argument(
        "--skip-live-jobs",
        action="store_true",
        help="Skip France Travail Live Jobs fetch",
    )
    parser.add_argument(
        "--skip-inclusion-jobs", action="store_true", help="Skip Inclusion Jobs fetch"
    )
    args = parser.parse_args()

    skip_live_jobs = args.skip_live_jobs
    skip_inclusion_jobs = args.skip_inclusion_jobs

    # Early check for France Travail fetch if ingest/all is selected and not explicitly skipped
    if args.step in ["ingest", "all"]:
        from pipeline.ingest import get_live_jobs_status

        status = get_live_jobs_status()

        if not status["within_ttl"] and not skip_live_jobs:
            print("\n" + "=" * 50)
            if not status["exists"]:
                print("[?] France Travail Live Jobs data is MISSING.")
            else:
                print(
                    f"[?] France Travail Live Jobs data is {status['age_days']:.1f} days old (TTL={status['ttl_days']})."
                )

            choice = (
                input("    Do you want to refresh the metadata? (y/N): ")
                .lower()
                .strip()
            )
            if choice != "y":
                print("    >> Skipping Live Jobs fetch.")
                skip_live_jobs = True
            else:
                print("    >> Live Jobs fetch will run during ingestion.")
            print("=" * 50 + "\n")

        # Inclusion Jobs check
        from pipeline.ingest import get_inclusion_jobs_status

        status_inc = get_inclusion_jobs_status()
        if not status_inc["within_ttl"] and not skip_inclusion_jobs:
            print("\n" + "=" * 50)
            if not status_inc["exists"]:
                print("[?] Inclusion Jobs data is MISSING.")
            else:
                print(
                    f"[?] Inclusion Jobs data is {status_inc['age_days']:.1f} days old (TTL={status_inc['ttl_days']})."
                )

            choice = (
                input("    Do you want to refresh the Inclusion Jobs? (y/N): ")
                .lower()
                .strip()
            )
            if choice != "y":
                print("    >> Skipping Inclusion Jobs fetch.")
                skip_inclusion_jobs = True
            else:
                print(
                    "    >> Inclusion Jobs fetch will run during ingestion using credentials from .env."
                )
            print("=" * 50 + "\n")

    if args.step in ["ingest", "all"]:
        # Print reminders for non-datagouv sources that have expired caches
        try:
            from pipeline.common import (
                load_config,
                CONFIG_FILE,
                CACHE_DIR,
                is_cache_valid,
            )
            from datetime import datetime

            config = load_config(CONFIG_FILE)
            expired_reminders = []
            for name, source_cfg in config["sources"].items():
                if not source_cfg.get("datagouv_resource_id"):
                    local_name = source_cfg.get("local_name")
                    if local_name:
                        local_path = CACHE_DIR / local_name
                        if local_path.exists() and not is_cache_valid(name, source_cfg):
                            mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
                            age_days = (datetime.now() - mtime).days
                            ttl = source_cfg.get("ttl_days", 30)
                            expired_reminders.append(
                                f"  - {name}: age={age_days} days (TTL={ttl} days)"
                            )
            if expired_reminders:
                print("\n" + "🔔" * 15 + " CACHE EXPIRATION REMINDERS " + "🔔" * 15)
                for reminder in expired_reminders:
                    print(reminder)
                print(
                    "Please check manually if new versions of these datasets are available."
                )
                print("🔔" * 58 + "\n")
        except Exception as e:
            logging.debug(f"Failed to compile early reminders: {e}")

        logging.info("=== Starting Ingestion Phase ===")
        ingest_args = []
        if skip_live_jobs:
            ingest_args.append("--skip-live-jobs")
        if skip_inclusion_jobs:
            ingest_args.append("--skip-inclusion-jobs")
        if args.steps:
            ingest_args.extend(["--steps", args.steps])
        ingest.main(ingest_args)
        logging.info("=== Ingestion Phase Completed ===")

    if args.step in ["build", "all"]:
        logging.info("=== Starting Build Phase ===")
        build_args = []
        if args.steps:
            build_args.extend(["--steps", args.steps])
        build.main(build_args)
        logging.info("=== Build Phase Completed ===")

    if args.step in ["prescoring", "all"]:
        logging.info("=== Starting Prescoring Phase ===")
        prescoring.main([])
        logging.info("=== Prescoring Phase Completed ===")
        
        logging.info("=== Generating Data Manifest ===")
        try:
            from pipeline.manifest import generate_manifest
            from pipeline.odace_client import get_odace_client
            from pathlib import Path
            odace_client = None
            try:
                odace_client = get_odace_client()
            except Exception as e:
                logging.warning(f"Could not initialize OdaceClient for manifest generation: {e}")

            generate_manifest(output_path=Path("pipeline/cache/output/data_manifest.json"), odace_client=odace_client)
            logging.info("=== Data Manifest Generation Completed ===")
        except Exception as e:
            logging.error(f"Failed to generate Data Manifest: {e}", exc_info=True)


    if args.step in ["deploy", "all"]:
        logging.info("=== Starting Deployment Phase ===")
        DEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
        DEST_DATASETS_DIR.mkdir(parents=True, exist_ok=True)

        # 1. Copy Bootstrap files to app/data/
        for f in BOOTSTRAP_FILES:
            src = SOURCE_DIR / f
            dst = DEST_DATA_DIR / f
            if src.exists():
                shutil.copy2(src, dst)
                logging.info(f"Copied bootstrap file {f} to {DEST_DATA_DIR}")
            else:
                logging.error(f"Bootstrap source file {f} not found in {SOURCE_DIR}")

        # 2. Copy Dataset files to app/data/datasets/ (local dev mirror)
        for f in DATASET_FILES:
            src = SOURCE_DIR / f
            dst = DEST_DATASETS_DIR / f
            if src.exists():
                shutil.copy2(src, dst)
                logging.info(f"Copied dataset file {f} to {DEST_DATASETS_DIR}")
            else:
                logging.warning(f"Dataset source file {f} not found in {SOURCE_DIR}")

        # 3. Upload Datasets to GCS bucket (gs://odis-stream2-eu/datasets/)
        bucket_name = os.getenv("GCS_DATASETS_BUCKET", "odis-stream2-eu")
        try:
            gcs_client = storage.Client()
            bucket = gcs_client.bucket(bucket_name)
            logging.info(f"Uploading datasets to GCS bucket 'gs://{bucket_name}/datasets/'...")
            
            all_files_to_upload = BOOTSTRAP_FILES + DATASET_FILES
            for f in all_files_to_upload:
                src = SOURCE_DIR / f
                if src.exists():
                    blob_path = f"datasets/{f}"
                    blob = bucket.blob(blob_path)
                    blob.upload_from_filename(str(src))
                    logging.info(f"Uploaded {f} -> gs://{bucket_name}/{blob_path}")
        except Exception as e:
            logging.warning(
                f"GCS Upload skipped or failed (GCP credentials/connection issue): {e}"
            )

        logging.info("=== Deployment Phase Completed ===")


if __name__ == "__main__":
    main()
