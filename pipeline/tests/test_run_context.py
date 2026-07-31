import json

import pytest

from pipeline.common import PipelineLogger
from pipeline.run_context import PipelineRun, PipelineRunError, bind_run_paths


def test_run_record_is_isolated_and_requires_a_passed_quality_gate(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("pipeline.run_context.RUNS_DIR", tmp_path / "runs")
    run = PipelineRun.create("run-candidate")

    logger = PipelineLogger(run.status_file)
    logger.log_step("ingest_all", "COMPLETED")

    state = json.loads(run.status_file.read_text(encoding="utf-8"))
    assert state["run_id"] == "run-candidate"
    assert state["steps"]["ingest_all"]["status"] == "COMPLETED"
    with pytest.raises(PipelineRunError, match="not deployable"):
        run.assert_deployable()

    run.update_state("PASSED", quality_gate={"status": "PASSED"})
    assert run.assert_deployable()["state"] == "PASSED"


def test_bind_run_paths_isolates_clean_and_output_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.run_context.RUNS_DIR", tmp_path / "runs")
    run = PipelineRun.create("run-candidate")

    from pipeline import build, common, ingest, manifest

    # bind_run_paths assigns module globals. Register their prior values with
    # monkeypatch so the test cannot leak a candidate directory to later tests.
    for module in (build, common, ingest, manifest):
        for attr in ("CLEAN_DIR", "OUTPUT_DIR", "STATUS_FILE"):
            monkeypatch.setattr(module, attr, getattr(module, attr))
    monkeypatch.setattr(
        manifest, "DEFAULT_MANIFEST_PATH", manifest.DEFAULT_MANIFEST_PATH
    )

    bind_run_paths(run)

    assert common.CLEAN_DIR == run.clean_dir
    assert ingest.CLEAN_DIR == run.clean_dir
    assert build.CLEAN_DIR == run.clean_dir
    assert manifest.CLEAN_DIR == run.clean_dir
    assert run.clean_dir.exists()
