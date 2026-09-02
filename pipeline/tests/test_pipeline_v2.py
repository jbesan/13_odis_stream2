import os
import time
import zipfile
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

from pipeline.common import (
    is_cache_valid,
    fetch_remote_metadata_datagouv,
    validate_dataset_contract,
)
from pipeline.ingest import fetch_source, run_clean_step_safely, PipelineLogger


@pytest.fixture
def temp_pipeline_dirs(tmp_path):
    """Fixture that creates temporary raw and clean cache directories."""
    raw_dir = tmp_path / "raw"
    clean_dir = tmp_path / "clean"
    output_dir = tmp_path / "output"

    raw_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Patch the CACHE_DIR and CLEAN_DIR in common and ingest
    with (
        patch("pipeline.common.CACHE_DIR", raw_dir),
        patch("pipeline.common.CLEAN_DIR", clean_dir),
        patch("pipeline.common.OUTPUT_DIR", output_dir),
        patch("pipeline.ingest.CACHE_DIR", raw_dir),
        patch("pipeline.ingest.CLEAN_DIR", clean_dir),
        patch("pipeline.ingest.OUTPUT_DIR", output_dir),
    ):
        yield raw_dir, clean_dir, output_dir


# =====================================================================
# 1. TTL & CACHE VALIDITY TESTS
# =====================================================================


def test_is_cache_valid(temp_pipeline_dirs):
    raw_dir, _, _ = temp_pipeline_dirs
    source_name = "test_source"
    local_name = "test_source.csv"
    local_path = raw_dir / local_name

    source_cfg = {"local_name": local_name, "ttl_days": 10}

    # Case 1: Cache file does not exist
    assert not is_cache_valid(source_name, source_cfg)

    # Case 2: Cache file is within TTL
    local_path.write_text("dummy data")
    assert is_cache_valid(source_name, source_cfg)

    # Case 3: Cache file has expired
    # Set modification time to 15 days ago
    past_time = time.time() - (15 * 24 * 3600)
    os.utime(local_path, (past_time, past_time))
    assert not is_cache_valid(source_name, source_cfg)

    # Case 4: TTL is mandatory; no implicit cache policy is allowed.
    with pytest.raises(KeyError, match="ttl_days"):
        is_cache_valid(source_name, {"local_name": local_name})


# =====================================================================
# 2. REMOTE METADATA & VERSION CHECK TESTS
# =====================================================================


@patch("requests.get")
def test_fetch_remote_metadata_datagouv(mock_get):
    resource_id = "test-resource-id-123"

    # Mock successful redirect response with Last-Modified header
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"Last-Modified": "Fri, 22 May 2026 08:00:00 GMT"}
    mock_resp.url = "https://object.data.gouv.fr/test-resource-id-123"
    mock_get.return_value = mock_resp

    metadata = fetch_remote_metadata_datagouv(resource_id)
    assert metadata is not None
    assert metadata["last_modified"] == "2026-05-22T08:00:00+00:00"
    mock_get.assert_called_once_with(
        f"https://www.data.gouv.fr/api/1/datasets/r/{resource_id}",
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        },
        stream=True,
        allow_redirects=True,
        timeout=15,
    )

    # Mock failed response
    mock_get.reset_mock()
    mock_get.side_effect = Exception("Network Down")
    metadata_fail = fetch_remote_metadata_datagouv(resource_id)
    assert metadata_fail is None


# =====================================================================
# 3. SCHEMA CONTRACT VALIDATION TESTS
# =====================================================================


def test_validate_dataset_contract():
    source_cfg = {"used_columns": ["col_a", "col_b", "codgeo"]}

    # Case 1: Empty DataFrame
    df_empty = pd.DataFrame()
    assert not validate_dataset_contract(df_empty, "test_set", source_cfg)

    # Case 2: Missing some columns (should return True under resilient contract)
    df_missing_some = pd.DataFrame({"col_a": [1, 2], "col_c": [3, 4]})
    assert validate_dataset_contract(df_missing_some, "test_set", source_cfg)

    # Case 2b: Missing all columns (should return False)
    df_missing_all = pd.DataFrame({"col_c": [1, 2], "col_d": [3, 4]})
    assert not validate_dataset_contract(df_missing_all, "test_set", source_cfg)

    # Case 3: High null rate in primary identifier (codgeo)
    df_high_null = pd.DataFrame(
        {
            "col_a": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "col_b": [10] * 10,
            "codgeo": [
                None,
                None,
                "31000",
                "31000",
                "31000",
                "31000",
                "31000",
                "31000",
                "31000",
                "31000",
            ],
        }
    )
    # 20% null rate is > 5% allowed threshold
    assert not validate_dataset_contract(df_high_null, "test_set", source_cfg)

    # Case 4: Valid DataFrame
    df_valid = pd.DataFrame(
        {"col_a": [1, 2, 3], "col_b": [4, 5, 6], "codgeo": ["31000", "33000", "75001"]}
    )
    assert validate_dataset_contract(df_valid, "test_set", source_cfg)


# =====================================================================
# 4. DOWNLOAD CACHING, REMOTE CHECKS & ZIP EXTRACTION IN FETCH_SOURCE
# =====================================================================


@patch("requests.get")
def test_fetch_source_caching_and_remote_metadata(mock_get, temp_pipeline_dirs):
    raw_dir, _, _ = temp_pipeline_dirs
    logger = MagicMock(spec=PipelineLogger)

    source_cfg = {
        "datagouv_resource_id": "communes-resource-id",
        "local_name": "communes.geojson",
        "ttl_days": 10,
    }

    # Case 1: Cache is valid (under TTL) -> returns local path without calling remote metadata API
    local_path = raw_dir / "communes.geojson"
    local_path.write_text("cached communes geojson")

    ret_path = fetch_source("communes", source_cfg, logger)
    assert ret_path == local_path
    mock_get.assert_not_called()

    # Case 2: Cache is expired, but remote modification date <= local modification date
    # Let's set local modification date to 15 days ago
    past_mtime = time.time() - (15 * 24 * 3600)
    os.utime(local_path, (past_mtime, past_mtime))

    # Mock remote metadata: returns metadata modification date that is older than local mtime
    # E.g., 20 days ago (local is 15 days ago)
    # Using HTTP Date format
    import email.utils

    remote_mod_dt = datetime.now() - timedelta(days=20)
    remote_mod_date_http = email.utils.formatdate(
        timeval=remote_mod_dt.timestamp(), usegmt=True
    )

    mock_metadata_resp = MagicMock()
    mock_metadata_resp.status_code = 200
    mock_metadata_resp.headers = {"Last-Modified": remote_mod_date_http}
    mock_metadata_resp.url = "https://object.data.gouv.fr/communes.geojson"

    mock_get.return_value = mock_metadata_resp

    ret_path_skip = fetch_source("communes", source_cfg, logger)
    assert ret_path_skip == local_path
    # Verify that the local cache file's TTL was reset (mtime touched)
    current_mtime = local_path.stat().st_mtime
    assert current_mtime > past_mtime + 10  # touched!


@patch("requests.get")
def test_fetch_source_staging_download_and_zip(mock_get, temp_pipeline_dirs):
    raw_dir, _, _ = temp_pipeline_dirs
    logger = MagicMock(spec=PipelineLogger)

    source_cfg = {
        "url": "https://example.com/test_dataset.zip",
        "format": "zip",
        "local_name": "test_dataset.zip",
        "archive_file": "extracted_member.csv",
        "ttl_days": 10,
    }

    # Cache is empty, downloading zip file to staging path
    # Let's prepare a valid mock zip response
    import io

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("extracted_member.csv", "col_a,col_b\n1,2\n3,4")
    zip_bytes = zip_buffer.getvalue()

    mock_download_resp = MagicMock()
    mock_download_resp.status_code = 200
    mock_download_resp.iter_content.return_value = [zip_bytes]
    mock_get.return_value = mock_download_resp

    ret_path = fetch_source("test_zip", source_cfg, logger)

    # Should save the zip as staging_test_dataset.zip
    staging_zip = raw_dir / "staging_test_dataset.zip"
    assert staging_zip.exists()

    # Should extract member into staging_extracted_member.csv
    staging_extracted = raw_dir / "staging_extracted_member.csv"
    assert staging_extracted.exists()
    assert staging_extracted.read_text() == "col_a,col_b\n1,2\n3,4"

    # Should return staging_extracted_member.csv path
    assert ret_path == staging_extracted


# =====================================================================
# 5. BLUE-GREEN SHADOW STAGING (COMMIT / ROLLBACK) via run_clean_step_safely
# =====================================================================


@patch("pipeline.ingest.load_dataset")
def test_run_clean_step_safely_success(mock_load, temp_pipeline_dirs):
    raw_dir, clean_dir, _ = temp_pipeline_dirs
    logger = MagicMock(spec=PipelineLogger)

    config = {
        "sources": {
            "communes": {
                "local_name": "communes.geojson",
                "used_columns": ["codgeo", "name"],
            }
        }
    }
    # Mock load_dataset to return a valid raw dataframe matching used_columns
    mock_load.return_value = pd.DataFrame({"codgeo": ["31000"], "name": ["Toulouse"]})

    # Setup pre-existing active raw and active clean files
    active_raw = raw_dir / "communes.geojson"
    active_raw.write_text("old raw data")
    active_clean = clean_dir / "communes.parquet"
    pd.DataFrame({"codgeo": ["11111"], "name": ["Paris"]}).to_parquet(
        active_clean
    )

    # Setup staging raw file (simulating that fetch_source downloaded an update)
    staging_raw = raw_dir / "staging_communes.geojson"
    staging_raw.write_text("new raw data")

    # Define a clean function that reads communes.geojson and writes to communes.parquet
    def clean_communes(cfg, log):
        # Read the raw communes.geojson
        raw_content = active_raw.read_text()
        assert (
            raw_content == "new raw data"
        )  # Verifies staging was moved to active name before clean ran!

        # Write clean parquet
        df_new = pd.DataFrame(
            {"codgeo": ["31000", "33000"], "name": ["Toulouse", "Bordeaux"]}
        )
        df_new.to_parquet(active_clean)

    run_clean_step_safely("communes", clean_communes, config, logger)

    # SUCCESS CASE:
    # 1. Backups should be deleted
    assert not (raw_dir / "communes.geojson.active_bak").exists()
    assert not (clean_dir / "communes.parquet.active_bak").exists()

    # 2. Active files should contain the new content
    assert active_raw.read_text() == "new raw data"
    df_res = pd.read_parquet(active_clean)
    assert list(df_res["name"]) == ["Toulouse", "Bordeaux"]

    # 3. Staging raw should be cleaned up (discarded/moved)
    assert not staging_raw.exists()


@patch("pipeline.ingest.load_dataset")
def test_run_clean_step_safely_failure_rollback(mock_load, temp_pipeline_dirs):
    raw_dir, clean_dir, _ = temp_pipeline_dirs
    logger = MagicMock(spec=PipelineLogger)

    config = {
        "sources": {
            "communes": {
                "local_name": "communes.geojson",
                "used_columns": ["codgeo", "name"],
            }
        }
    }

    # Setup pre-existing active raw and active clean files
    active_raw = raw_dir / "communes.geojson"
    active_raw.write_text("old raw data")
    active_clean = clean_dir / "communes.parquet"
    df_old = pd.DataFrame({"codgeo": ["11111"], "name": ["Paris"]})
    df_old.to_parquet(active_clean)

    # Case 1: Raw contract validation fails (returns empty df)
    mock_load.return_value = pd.DataFrame()
    staging_raw = raw_dir / "staging_communes.geojson"
    staging_raw.write_text("corrupted raw data")

    clean_executed = False

    def clean_communes_failed(cfg, log):
        nonlocal clean_executed
        clean_executed = True

    with pytest.raises(Exception, match="raw contract validation"):
        run_clean_step_safely("communes", clean_communes_failed, config, logger)

    # Verify early abort (clean function not run, staging discarded, active kept)
    assert not clean_executed
    assert active_raw.read_text() == "old raw data"
    assert not staging_raw.exists()

    # Case 2: Raw contract validation passes, but clean step raises crash
    mock_load.return_value = pd.DataFrame({"codgeo": ["31000"], "name": ["Toulouse"]})
    staging_raw = raw_dir / "staging_communes.geojson"
    staging_raw.write_text("new raw data")

    def clean_communes_crash(cfg, log):
        # Swap happened
        assert active_raw.read_text() == "new raw data"
        raise ValueError("Simulated cleaning crash")

    with pytest.raises(Exception, match="Required clean step"):
        run_clean_step_safely("communes", clean_communes_crash, config, logger)

    # Verify rollback successfully restored active files and discarded staging
    assert active_raw.read_text() == "old raw data"
    df_res = pd.read_parquet(active_clean)
    assert list(df_res["name"]) == ["Paris"]
    assert not (raw_dir / "communes.geojson.active_bak").exists()
    assert not (clean_dir / "communes.parquet.active_bak").exists()
    assert not staging_raw.exists()
