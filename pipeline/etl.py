import argparse
import logging
from pipeline import ingest, build, prescoring
import shutil
import os

SOURCE_DIR = 'pipeline/cache/output'
DEST_DIR = 'data'

FILES_TO_COPY = [
    'odis_communes.parquet',
    'odis_bassins_de_vie.parquet',
    'odis_pois.parquet',
    'odis_metiers_agg.parquet',
    'odis_associations_agg.parquet',
    'odis_referentiels.parquet',
    'odis_formations_agg.parquet',
    'odis_ccas.parquet'
]

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    parser = argparse.ArgumentParser(description="ODIS Data Pipeline ETL")
    parser.add_argument("--step", choices=["ingest", "build", "prescoring", "deploy", "all"], default="all", help="Step to run")
    args = parser.parse_args()

    if args.step in ["ingest", "all"]:
        logging.info("=== Starting Ingestion Phase ===")
        ingest.main([])
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
