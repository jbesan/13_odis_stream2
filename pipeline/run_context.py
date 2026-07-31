"""Run-scoped state and paths for a publishable pipeline candidate.

A candidate run never writes its output artefacts or status into the shared
``pipeline/cache/output`` / ``pipeline/status.json`` locations.  The active
runtime release is consequently unaffected until deploy explicitly activates a
run that has completed all required steps and its quality gate.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNS_DIR = Path("pipeline/cache/runs")
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class PipelineRunError(RuntimeError):
    """A run is invalid, incomplete, or cannot be published."""


@dataclass(frozen=True)
class PipelineRun:
    """Filesystem contract for one isolated pipeline candidate."""

    run_id: str
    directory: Path

    @property
    def output_dir(self) -> Path:
        return self.directory / "output"

    @property
    def clean_dir(self) -> Path:
        """Run-scoped cleaned intermediates used to build this candidate."""
        return self.directory / "clean"

    @property
    def status_file(self) -> Path:
        return self.directory / "run.json"

    @classmethod
    def create(cls, run_id: str | None = None) -> "PipelineRun":
        if run_id is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_id = f"run-{stamp}-{uuid.uuid4().hex[:8]}"
        run = cls.from_id(run_id)
        if run.directory.exists():
            raise PipelineRunError(f"Run already exists: {run.run_id}")
        run.output_dir.mkdir(parents=True)
        run.clean_dir.mkdir(parents=True)
        run._write_state(
            {
                "schema_version": 1,
                "run_id": run.run_id,
                "state": "RUNNING",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "steps": {},
                "sources": {},
            }
        )
        return run

    @classmethod
    def from_id(cls, run_id: str) -> "PipelineRun":
        if not _RUN_ID_RE.fullmatch(run_id):
            raise PipelineRunError(f"Unsafe run_id: {run_id!r}")
        return cls(run_id=run_id, directory=RUNS_DIR / run_id)

    def read_state(self) -> dict[str, Any]:
        if not self.status_file.exists():
            raise PipelineRunError(f"Run record is missing: {self.status_file}")
        try:
            return json.loads(self.status_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PipelineRunError(
                f"Run record is invalid JSON: {self.status_file}"
            ) from exc

    def update_state(self, state: str, **details: Any) -> None:
        payload = self.read_state()
        payload["state"] = state
        payload["finished_at"] = datetime.now(timezone.utc).isoformat()
        payload.update(details)
        self._write_state(payload)

    def assert_deployable(self) -> dict[str, Any]:
        payload = self.read_state()
        if payload.get("run_id") != self.run_id:
            raise PipelineRunError("Run record does not match requested run_id")
        if payload.get("state") != "PASSED":
            raise PipelineRunError(
                f"Run {self.run_id} is not deployable (state={payload.get('state')!r})"
            )
        quality_gate = payload.get("quality_gate", {})
        if quality_gate.get("status") != "PASSED":
            raise PipelineRunError("Run does not have a passed quality gate")
        return payload

    def _write_state(self, payload: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary_path = self.status_file.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        temporary_path.replace(self.status_file)


def bind_run_paths(run: PipelineRun) -> None:
    """Bind output/status module globals to one run before invoking pipeline steps."""
    # Existing functions import these paths as module globals.  Centralizing the
    # binding here preserves their public interfaces while preventing candidate
    # outputs/statuses from sharing the active cache locations.
    from pipeline import build, common, ingest, manifest, prescoring

    common.STATUS_FILE = run.status_file
    common.CLEAN_DIR = run.clean_dir
    common.OUTPUT_DIR = run.output_dir
    ingest.STATUS_FILE = run.status_file
    ingest.CLEAN_DIR = run.clean_dir
    ingest.OUTPUT_DIR = run.output_dir
    build.STATUS_FILE = run.status_file
    build.CLEAN_DIR = run.clean_dir
    build.OUTPUT_DIR = run.output_dir
    prescoring.STATUS_FILE = run.status_file
    prescoring.OUTPUT_DIR = run.output_dir
    manifest.STATUS_FILE = run.status_file
    manifest.CLEAN_DIR = run.clean_dir
    manifest.OUTPUT_DIR = run.output_dir
    manifest.DEFAULT_MANIFEST_PATH = run.output_dir / "data_manifest.json"
