import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline import etl
from pipeline.run_context import PipelineRun, PipelineRunError


def _make_output_dir(tmp_path: Path) -> Path:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "data_manifest.json").write_text(
        json.dumps({"manifest_version": "v-test-1"}), encoding="utf-8"
    )
    for filename in etl.DATASET_FILES:
        (output_dir / filename).write_text(filename, encoding="utf-8")
    return output_dir


@patch("pipeline.etl.storage.Client")
def test_publish_datasets_uploads_files_before_pointer(mock_client_class, tmp_path):
    output_dir = _make_output_dir(tmp_path)
    events = []

    def get_blob(blob_path):
        blob = MagicMock()
        blob.upload_from_filename.side_effect = lambda source: events.append(
            ("file", blob_path, source)
        )
        blob.upload_from_string.side_effect = lambda value, **kwargs: events.append(
            ("pointer", blob_path, value, kwargs)
        )
        return blob

    bucket = MagicMock()
    bucket.blob.side_effect = get_blob
    mock_client_class.return_value.bucket.return_value = bucket

    version = etl._publish_datasets_to_gcs(output_dir, "odis-stream2-eu")

    assert version == "v-test-1"
    assert [event[1] for event in events[:-1]] == [
        f"datasets/releases/v-test-1/{filename}" for filename in etl.DATASET_FILES
    ]
    assert events[-1][0:2] == ("pointer", "datasets/current.json")
    pointer = json.loads(events[-1][2])
    assert pointer["version"] == "v-test-1"
    assert pointer["files"] == etl.DATASET_FILES


@patch("pipeline.etl.storage.Client")
def test_publish_datasets_rejects_incomplete_output(mock_client_class, tmp_path):
    output_dir = _make_output_dir(tmp_path)
    (output_dir / etl.DATASET_FILES[-1]).unlink()

    with pytest.raises(FileNotFoundError, match="incomplete dataset release"):
        etl._publish_datasets_to_gcs(output_dir, "odis-stream2-eu")

    mock_client_class.assert_not_called()


def test_deploy_rejects_a_candidate_manifest_for_another_run(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.run_context.RUNS_DIR", tmp_path / "runs")
    run = PipelineRun.create("run-a")
    run.update_state("PASSED", quality_gate={"status": "PASSED"})
    manifest_path = run.output_dir / "data_manifest.json"
    manifest_path.write_text(
        json.dumps({"manifest_version": "v-test-1", "pipeline_run_id": "run-b"}),
        encoding="utf-8",
    )

    with pytest.raises(PipelineRunError, match="does not belong"):
        etl._assert_deployable_candidate(run.output_dir, run)


def test_deploy_flag_is_only_valid_for_the_full_pipeline(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["etl.py", "--step", "build", "--deploy"])

    with pytest.raises(SystemExit) as exc_info:
        etl.main()

    assert exc_info.value.code == 2
