"""Unit tests for the build-time dataset synchronization script."""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.sync_datasets import sync_active_release


def test_sync_active_release_downloads_and_verifies_artifacts(tmp_path):
    """sync_active_release downloads artifacts matching the active manifest."""
    target_dir = tmp_path / "app/data/datasets/active"
    static_dir = tmp_path / "app/static/data"

    parquet_content = b"PARQUET_CONTENT_MOCK"
    geojson_content = b'{"type": "FeatureCollection", "features": []}'
    parquet_sha = hashlib.sha256(parquet_content).hexdigest()
    geojson_sha = hashlib.sha256(geojson_content).hexdigest()

    manifest_dict = {
        "pipeline_run_id": "run-test-123",
        "outputs": [
            {
                "name": "odis_communes.parquet",
                "sha256": parquet_sha,
                "size_bytes": len(parquet_content),
            },
            {
                "name": "communes_france.geojson",
                "sha256": geojson_sha,
                "size_bytes": len(geojson_content),
            },
        ],
    }
    manifest_bytes = json.dumps(manifest_dict).encode("utf-8")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()

    pointer_dict = {
        "version": "run-test-123",
        "manifest": {
            "name": "data_manifest.json",
            "sha256": manifest_sha,
        },
    }
    pointer_bytes = json.dumps(pointer_dict).encode("utf-8")

    # Mocks
    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_storage.bucket.return_value = mock_bucket

    pointer_blob = MagicMock()
    pointer_blob.exists.return_value = True
    pointer_blob.download_as_bytes.return_value = pointer_bytes

    manifest_blob = MagicMock()
    manifest_blob.download_as_bytes.return_value = manifest_bytes

    parquet_blob = MagicMock()
    parquet_blob.download_to_filename.side_effect = lambda path: Path(path).write_bytes(
        parquet_content
    )

    geojson_blob = MagicMock()
    geojson_blob.download_to_filename.side_effect = lambda path: Path(path).write_bytes(
        geojson_content
    )

    def get_blob(blob_path: str):
        if blob_path.endswith("current.json"):
            return pointer_blob
        if blob_path.endswith("data_manifest.json"):
            return manifest_blob
        if blob_path.endswith("odis_communes.parquet"):
            return parquet_blob
        if blob_path.endswith("communes_france.geojson"):
            return geojson_blob
        blob = MagicMock()
        blob.exists.return_value = False
        return blob

    mock_bucket.blob.side_effect = get_blob

    # Run sync
    summary = sync_active_release(
        bucket_name="test-bucket",
        target_dir=target_dir,
        static_dir=static_dir,
        storage_client=mock_storage,
    )

    assert summary["version"] == "run-test-123"
    assert "odis_communes.parquet" in summary["downloaded_files"]
    assert "communes_france.geojson" in summary["downloaded_files"]

    assert (target_dir / "odis_communes.parquet").exists()
    assert (target_dir / "data_manifest.json").exists()
    assert (target_dir / "current.json").exists()
    assert (static_dir / "communes_france.geojson").exists()

    # Second run should skip already up-to-date files
    second_summary = sync_active_release(
        bucket_name="test-bucket",
        target_dir=target_dir,
        static_dir=static_dir,
        storage_client=mock_storage,
    )
    assert len(second_summary["downloaded_files"]) == 0
    assert "odis_communes.parquet" in second_summary["skipped_files"]
    assert "communes_france.geojson" in second_summary["skipped_files"]


def test_sync_active_release_fails_on_corrupted_checksum(tmp_path):
    """sync_active_release raises an error when an artifact checksum does not match."""
    target_dir = tmp_path / "target"
    static_dir = tmp_path / "static"

    corrupted_content = b"BAD_CONTENT"
    manifest_dict = {
        "pipeline_run_id": "run-test-corrupt",
        "outputs": [
            {
                "name": "odis_communes.parquet",
                "sha256": "0" * 64,  # Intentionally wrong
                "size_bytes": len(corrupted_content),
            }
        ],
    }
    manifest_bytes = json.dumps(manifest_dict).encode("utf-8")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()

    pointer_dict = {
        "version": "run-test-corrupt",
        "manifest": {
            "name": "data_manifest.json",
            "sha256": manifest_sha,
        },
    }

    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_storage.bucket.return_value = mock_bucket

    pointer_blob = MagicMock()
    pointer_blob.exists.return_value = True
    pointer_blob.download_as_bytes.return_value = json.dumps(pointer_dict).encode("utf-8")

    manifest_blob = MagicMock()
    manifest_blob.download_as_bytes.return_value = manifest_bytes

    parquet_blob = MagicMock()
    parquet_blob.download_to_filename.side_effect = lambda path: Path(path).write_bytes(
        corrupted_content
    )

    def get_blob(blob_path: str):
        if blob_path.endswith("current.json"):
            return pointer_blob
        if blob_path.endswith("data_manifest.json"):
            return manifest_blob
        return parquet_blob

    mock_bucket.blob.side_effect = get_blob

    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        sync_active_release(
            bucket_name="test-bucket",
            target_dir=target_dir,
            static_dir=static_dir,
            storage_client=mock_storage,
        )
