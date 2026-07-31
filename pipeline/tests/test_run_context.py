import json

import pytest

from pipeline.common import PipelineLogger
from pipeline.run_context import PipelineRun, PipelineRunError


def test_run_record_is_isolated_and_requires_a_passed_quality_gate(tmp_path, monkeypatch):
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
