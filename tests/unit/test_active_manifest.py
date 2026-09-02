"""Runtime verification of the active release provenance manifest."""

import hashlib
import json
from pathlib import Path
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from utils import data_loader
from utils.data_loader import load_active_data_manifest


@pytest.fixture(autouse=True)
def clear_active_release_payload_cache():
    data_loader._active_release_payload.clear()
    yield
    data_loader._active_release_payload.clear()


@patch("utils.data_loader.storage.Client")
def test_load_active_data_manifest_reads_the_pointer_release(mock_client, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    manifest = {
        "manifest_version": "v2-123",
        "pipeline_run_id": "run-123",
        "sources": [],
    }
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    pointer = {
        "version": "run-123",
        "manifest": {
            "name": "data_manifest.json",
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
    }
    pointer_blob = MagicMock()
    pointer_blob.exists.return_value = True
    pointer_blob.download_as_bytes.return_value = json.dumps(pointer).encode("utf-8")
    manifest_blob = MagicMock()
    manifest_blob.download_as_bytes.return_value = manifest_bytes
    bucket = MagicMock()
    bucket.blob.side_effect = lambda name: (
        pointer_blob if name.endswith("current.json") else manifest_blob
    )
    mock_client.return_value.bucket.return_value = bucket

    active_manifest = load_active_data_manifest()

    assert active_manifest["manifest_version"] == "v2-123"
    assert active_manifest["active_release_version"] == "run-123"


@patch("utils.data_loader.storage.Client")
def test_cloud_run_rejects_a_manifest_with_the_wrong_checksum(mock_client, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "odis-app")
    pointer_blob = MagicMock()
    pointer_blob.exists.return_value = True
    pointer_blob.download_as_bytes.return_value = json.dumps(
        {
            "version": "run-123",
            "manifest": {"name": "data_manifest.json", "sha256": "0" * 64},
        }
    ).encode("utf-8")
    manifest_blob = MagicMock()
    manifest_blob.download_as_bytes.return_value = b"{}"
    bucket = MagicMock()
    bucket.blob.side_effect = lambda name: (
        pointer_blob if name.endswith("current.json") else manifest_blob
    )
    mock_client.return_value.bucket.return_value = bucket

    with pytest.raises(RuntimeError, match="checksum"):
        load_active_data_manifest()


@patch("utils.data_loader.storage.Client")
def test_complete_release_is_resolved_once_and_downloaded_concurrently(
    mock_client, tmp_path, monkeypatch
):
    """A scoring load freezes one pointer/manifest before fetching all files."""
    monkeypatch.setattr(data_loader.tempfile, "gettempdir", lambda: str(tmp_path))
    release_version = "run-bundle"
    payloads = {
        filename: f"contents:{filename}".encode("utf-8")
        for filename in data_loader._RUNTIME_DATASET_FILENAMES
    }
    validation_payloads = {
        "odis_ft_jobs_coverage.parquet": b"pipeline-only",
        "odis_inclusion_jobs_coverage.parquet": b"pipeline-only",
    }
    release_payloads = {**payloads, **validation_payloads}
    manifest = {
        "pipeline_run_id": release_version,
        "outputs": [
            {
                "name": filename,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            for filename, payload in release_payloads.items()
        ],
    }
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    pointer = {
        "version": release_version,
        "files": list(release_payloads),
        "manifest": {
            "name": "data_manifest.json",
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
    }

    pointer_blob = MagicMock()
    pointer_blob.exists.return_value = True
    pointer_blob.download_as_bytes.return_value = json.dumps(pointer).encode("utf-8")
    manifest_blob = MagicMock()
    manifest_blob.download_as_bytes.return_value = manifest_bytes
    artifact_blobs = {}
    active_downloads = 0
    max_parallel_downloads = 0
    download_lock = threading.Lock()
    for filename, payload in release_payloads.items():
        blob = MagicMock()

        def download(target_path, content=payload):
            nonlocal active_downloads, max_parallel_downloads
            with download_lock:
                active_downloads += 1
                max_parallel_downloads = max(max_parallel_downloads, active_downloads)
            time.sleep(0.01)
            Path(target_path).write_bytes(content)
            with download_lock:
                active_downloads -= 1

        blob.download_to_filename.side_effect = download
        artifact_blobs[filename] = blob

    def get_blob(blob_path):
        if blob_path == "datasets/current.json":
            return pointer_blob
        if blob_path.endswith("data_manifest.json"):
            return manifest_blob
        return artifact_blobs[blob_path.rsplit("/", 1)[-1]]

    bucket = MagicMock()
    bucket.blob.side_effect = get_blob
    mock_client.return_value.bucket.return_value = bucket

    context = data_loader.get_active_release_context()
    data_loader._ensure_complete_release_cached(context)

    assert context.identity == f"gcs:{release_version}"
    assert pointer_blob.download_as_bytes.call_count == 1
    assert manifest_blob.download_as_bytes.call_count == 1
    assert all(
        artifact_blobs[filename].download_to_filename.call_count == 1
        for filename in payloads
    )
    assert all(
        artifact_blobs[filename].download_to_filename.call_count == 0
        for filename in validation_payloads
    )
    assert max_parallel_downloads > 1
