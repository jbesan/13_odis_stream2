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
    pointer_blob.download_as_bytes.return_value = json.dumps(pointer_dict).encode(
        "utf-8"
    )

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


def test_sync_active_release_recovers_via_previous_version_on_corruption(tmp_path):
    """sync_active_release recovers using previous_version when active release is corrupted."""
    target_dir = tmp_path / "target"
    static_dir = tmp_path / "static"

    corrupted_content = b"CORRUPTED_ACTIVE"
    good_previous_content = b"VALID_PREVIOUS_PARQUET"
    good_previous_sha = hashlib.sha256(good_previous_content).hexdigest()

    active_manifest = {
        "pipeline_run_id": "run-active-corrupted",
        "outputs": [
            {
                "name": "odis_communes.parquet",
                "sha256": "0" * 64,  # Mismatch
                "size_bytes": len(corrupted_content),
            }
        ],
    }
    previous_manifest = {
        "pipeline_run_id": "run-previous-healthy",
        "outputs": [
            {
                "name": "odis_communes.parquet",
                "sha256": good_previous_sha,
                "size_bytes": len(good_previous_content),
            }
        ],
    }

    pointer_dict = {
        "version": "run-active-corrupted",
        "previous_version": "run-previous-healthy",
        "manifest": {"name": "data_manifest.json"},
    }

    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_storage.bucket.return_value = mock_bucket

    def get_blob(blob_path: str):
        blob = MagicMock()
        blob.exists.return_value = True
        if blob_path.endswith("current.json"):
            blob.download_as_bytes.return_value = json.dumps(pointer_dict).encode(
                "utf-8"
            )
        elif "run-active-corrupted" in blob_path:
            if blob_path.endswith("data_manifest.json"):
                blob.download_as_bytes.return_value = json.dumps(
                    active_manifest
                ).encode("utf-8")
            else:
                blob.download_to_filename.side_effect = lambda path: Path(
                    path
                ).write_bytes(corrupted_content)
        elif "run-previous-healthy" in blob_path:
            if blob_path.endswith("data_manifest.json"):
                blob.download_as_bytes.return_value = json.dumps(
                    previous_manifest
                ).encode("utf-8")
            else:
                blob.download_to_filename.side_effect = lambda path: Path(
                    path
                ).write_bytes(good_previous_content)
        else:
            blob.exists.return_value = False
        return blob

    mock_bucket.blob.side_effect = get_blob

    summary = sync_active_release(
        bucket_name="test-bucket",
        target_dir=target_dir,
        static_dir=static_dir,
        storage_client=mock_storage,
        allow_fallback=True,
    )

    assert summary.get("fallback") is True
    assert summary["version"] == "run-previous-healthy"
    assert "odis_communes.parquet" in summary["downloaded_files"]
    assert (target_dir / "odis_communes.parquet").read_bytes() == good_previous_content


def test_ensure_datasets_available_skips_when_complete(tmp_path, monkeypatch):
    """ensure_datasets_available skips network calls if local datasets exist."""
    from utils.data_sync import RUNTIME_DATASET_FILENAMES, ensure_datasets_available

    target_dir = tmp_path / "datasets"
    target_dir.mkdir()
    (target_dir / "data_manifest.json").write_text("{}", encoding="utf-8")
    for fname in RUNTIME_DATASET_FILENAMES:
        (target_dir / fname).write_text("mock", encoding="utf-8")

    monkeypatch.setenv("GCS_DATASETS_BUCKET", "test-bucket")
    # Calling ensure_datasets_available should return None and not call GCS
    result = ensure_datasets_available(target_dir=target_dir)
    assert result is None


def test_atomic_staging_preserves_existing_files_on_mid_sync_failure(tmp_path):
    """If the second file in a release fails checksum, the first file in target_dir is not overwritten."""
    target_dir = tmp_path / "active"
    target_dir.mkdir(parents=True)
    static_dir = tmp_path / "static"

    original_communes = b"ORIGINAL_HEALTHY_COMMUNES"
    (target_dir / "odis_communes.parquet").write_bytes(original_communes)

    new_communes = b"NEW_COMMUNES_CONTENT"
    new_communes_sha = hashlib.sha256(new_communes).hexdigest()
    bad_pois = b"CORRUPTED_POIS_CONTENT"

    manifest_dict = {
        "pipeline_run_id": "run-partial-fail",
        "outputs": [
            {
                "name": "odis_communes.parquet",
                "sha256": new_communes_sha,
                "size_bytes": len(new_communes),
            },
            {
                "name": "odis_pois.parquet",
                "sha256": "f" * 64,  # Mismatch on second artifact
                "size_bytes": len(bad_pois),
            },
        ],
    }
    pointer_dict = {
        "version": "run-partial-fail",
        "manifest": {"name": "data_manifest.json"},
    }

    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_storage.bucket.return_value = mock_bucket

    def get_blob(blob_path: str):
        blob = MagicMock()
        blob.exists.return_value = True
        if blob_path.endswith("current.json"):
            blob.download_as_bytes.return_value = json.dumps(pointer_dict).encode(
                "utf-8"
            )
        elif blob_path.endswith("data_manifest.json"):
            blob.download_as_bytes.return_value = json.dumps(manifest_dict).encode(
                "utf-8"
            )
        elif blob_path.endswith("odis_communes.parquet"):
            blob.download_to_filename.side_effect = lambda p: Path(p).write_bytes(
                new_communes
            )
        elif blob_path.endswith("odis_pois.parquet"):
            blob.download_to_filename.side_effect = lambda p: Path(p).write_bytes(
                bad_pois
            )
        else:
            blob.exists.return_value = False
        return blob

    mock_bucket.blob.side_effect = get_blob

    with pytest.raises(RuntimeError, match="Checksum mismatch on odis_pois.parquet"):
        sync_active_release(
            bucket_name="test-bucket",
            target_dir=target_dir,
            static_dir=static_dir,
            force=True,
            storage_client=mock_storage,
        )

    # Existing file in target_dir must be untouched and staging dirs cleaned up
    assert (target_dir / "odis_communes.parquet").read_bytes() == original_communes
    assert not (target_dir / "odis_pois.parquet").exists()
    staging_leftovers = list(tmp_path.glob(".staging_*"))
    assert staging_leftovers == []


def test_default_dirs_anchored_on_app_dir(monkeypatch):
    """Default dataset and static directories are anchored on cfg.APP_DIR regardless of CWD."""
    import config as cfg
    from utils.data_sync import get_default_datasets_dir, get_default_static_dir

    for env_var in ("ODIS_DATASETS_DIR", "ODIS_CACHE_DIR", "ODIS_DATA_CACHE_DIR"):
        monkeypatch.delenv(env_var, raising=False)

    monkeypatch.chdir(cfg.APP_DIR)
    assert (
        get_default_datasets_dir() == Path(cfg.APP_DIR) / "data" / "datasets" / "active"
    )
    assert get_default_static_dir() == Path(cfg.APP_DIR) / "static" / "data"
