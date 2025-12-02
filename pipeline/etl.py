import argparse
import logging
from pipeline import ingest, build

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    parser = argparse.ArgumentParser(description="ODIS Data Pipeline ETL")
    parser.add_argument("--step", choices=["ingest", "build", "all"], default="all", help="Step to run")
    args = parser.parse_args()

    if args.step in ["ingest", "all"]:
        logging.info("=== Starting Ingestion Phase ===")
        ingest.main()
        logging.info("=== Ingestion Phase Completed ===")

    if args.step in ["build", "all"]:
        logging.info("=== Starting Build Phase ===")
        build.main()
        logging.info("=== Build Phase Completed ===")

if __name__ == "__main__":
    main()
