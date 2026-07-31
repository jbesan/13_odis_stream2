"""Archived manual uploader for the retired J'Accueille BigQuery tables.

The application no longer reads these tables. Retain this only for a documented
historical-reproduction exercise; do not run it as an active-data fallback.
"""

import logging
from pathlib import Path

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

logger = logging.getLogger(__name__)

PROJECT_ID = "odis-stream2"
DATASET_ID = "jaccueille"
HOSTS_TABLE_ID = "jaccueille_accueillants_bdv"
PROSPECTS_TABLE_ID = "jaccueille_prospects_bdv"


def upload_legacy_jaccueille_bq(hosts_path: Path, prospects_path: Path) -> None:
    """Upload retired manual artifacts only when historical recovery is approved."""
    client = bigquery.Client(project=PROJECT_ID)
    dataset_ref = client.dataset(DATASET_ID)
    try:
        client.get_dataset(dataset_ref)
    except NotFound:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = "EU"
        client.create_dataset(dataset)

    for source_path, table_id in (
        (hosts_path, HOSTS_TABLE_ID),
        (prospects_path, PROSPECTS_TABLE_ID),
    ):
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        dataframe = pd.read_parquet(source_path)
        job = client.load_table_from_dataframe(
            dataframe,
            dataset_ref.table(table_id),
            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
        )
        job.result()
        logger.info("Uploaded %s rows to %s.%s", len(dataframe), DATASET_ID, table_id)
