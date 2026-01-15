import argparse
import logging
import sys
import os

# Add app directory to sys.path to allow imports from app/
sys.path.append(os.path.join(os.getcwd(), 'app'))

from pipeline import ingest, build, prescoring
import shutil
import os

SOURCE_DIR = 'pipeline/cache/output'
DEST_DIR = 'data'

FILES_TO_COPY = [
    'odis_communes.parquet',
    'odis_communes_pre.parquet',
    'odis_bassins_de_vie.parquet',
    'odis_pois.parquet',
    'odis_associations_agg.parquet',

    'odis_referentiels.parquet',
    'odis_formations_agg.parquet',
    'odis_ccas.parquet',
    'odis_refugee_associations.parquet',
    'odis_live_jobs_agg.parquet',
    'odis_asso_mini.parquet'
]

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    parser = argparse.ArgumentParser(description="ODIS Data Pipeline ETL")
    parser.add_argument("--step", choices=["ingest", "build", "prescoring", "deploy", "all"], default="all", help="Step to run")
    args = parser.parse_args()

    skip_live_jobs = False
    
    # Early check for France Travail fetch if ingest/all is selected
    if args.step in ["ingest", "all"]:
        from pipeline.ingest import get_live_jobs_status
        status = get_live_jobs_status()
        
        if not status["within_ttl"]:
            print("\n" + "="*50)
            if not status["exists"]:
                print("[?] France Travail Live Jobs data is MISSING.")
            else:
                print(f"[?] France Travail Live Jobs data is {status['age_days']:.1f} days old (TTL={status['ttl_days']}).")
            
            choice = input("    Do you want to refresh the metadata? (y/N): ").lower().strip()
            if choice != 'y':
                print("    >> Skipping Live Jobs fetch.")
                skip_live_jobs = True
            else:
                print("    >> Live Jobs fetch will run during ingestion.")
            print("="*50 + "\n")

    if args.step in ["ingest", "all"]:
        logging.info("=== Starting Ingestion Phase ===")
        ingest_args = []
        if skip_live_jobs:
            ingest_args = ["--skip-live-jobs"]
        ingest.main(ingest_args)
        logging.info("=== Ingestion Phase Completed ===")

    if args.step in ["build", "all"]:
        logging.info("=== Starting Build Phase ===")
        build.main([])
        logging.info("=== Build Phase Completed ===")

    if args.step in ["prescoring", "all"]:
        logging.info("=== Starting Prescoring Phase ===")
        prescoring.main([])
        logging.info("=== Prescoring Phase Completed ===")

    if args.step in ["deploy", "all"]:
        logging.info("=== Starting Deployment Phase ===")
        if not os.path.exists(DEST_DIR):
            os.makedirs(DEST_DIR)
            logging.info(f"Created {DEST_DIR}")

        # Cleanup legacy files
        for f in os.listdir(DEST_DIR):
            if f.startswith("odis_rel_") and f.endswith(".parquet"):
                try:
                    os.remove(os.path.join(DEST_DIR, f))
                    logging.info(f"Removed legacy file: {f}")
                except Exception as e:
                    logging.warning(f"Failed to remove legacy file {f}: {e}")

        for f in FILES_TO_COPY:
            src = os.path.join(SOURCE_DIR, f)
            dst = os.path.join(DEST_DIR, f)
            
            if os.path.exists(src):
                if os.path.exists(dst):
                    os.remove(dst)
                shutil.copy2(src, dst)
                logging.info(f"Copied {f} to {DEST_DIR}")
            else:
                logging.error(f"Source file {f} not found in {SOURCE_DIR}")
        logging.info("=== Deployment Phase Completed ===")

if __name__ == "__main__":
    main()
