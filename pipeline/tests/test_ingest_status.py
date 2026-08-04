import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

from pipeline import ingest
from pipeline.common import PipelineLogger


def test_live_jobs_cache_without_coverage_requires_refresh(tmp_path, monkeypatch):
    aggregate_path = tmp_path / "odis_ft_jobs_agg.parquet"
    coverage_path = tmp_path / "odis_ft_jobs_coverage.parquet"
    aggregate_path.write_bytes(b"legacy aggregate")

    monkeypatch.setattr(ingest, "OUTPUT_DIR", tmp_path / "candidate")
    monkeypatch.setattr(ingest, "FT_SHARED_OUTPUT_PATH", aggregate_path)
    monkeypatch.setattr(ingest, "FT_COVERAGE_SHARED_OUTPUT_PATH", coverage_path)

    status = ingest.get_live_jobs_status()

    assert status["exists"] is True
    assert status["coverage_exists"] is False
    assert status["within_ttl"] is False

    coverage_path.write_bytes(b"coverage evidence")
    refreshed_status = ingest.get_live_jobs_status()
    assert refreshed_status["coverage_exists"] is True
    assert refreshed_status["within_ttl"] is True


def test_live_jobs_cache_records_provenance_for_the_manifest(tmp_path, monkeypatch):
    source_path = tmp_path / "odis_ft_jobs_agg.parquet"
    coverage_path = tmp_path / "odis_ft_jobs_coverage.parquet"
    source_path.write_bytes(b"aggregate")
    coverage_path.write_bytes(b"coverage")
    candidate_output = tmp_path / "candidate"

    monkeypatch.setattr(ingest, "OUTPUT_DIR", candidate_output)
    monkeypatch.setattr(ingest, "FT_SHARED_OUTPUT_PATH", source_path)
    monkeypatch.setattr(ingest, "FT_COVERAGE_SHARED_OUTPUT_PATH", coverage_path)
    logger = MagicMock()

    ingest.clean_live_jobs({}, logger)

    logger.log_source.assert_called_once()
    source_key, status, artifact = logger.log_source.call_args.args
    assert (source_key, status) == ("france_travail_live", "CACHED")
    assert artifact == candidate_output / source_path.name
    assert logger.log_source.call_args.kwargs["observed_at"].endswith("+00:00")


def test_inclusion_jobs_cache_records_provenance_for_the_manifest(tmp_path, monkeypatch):
    source_path = tmp_path / "odis_inclusion_jobs.parquet"
    coverage_path = tmp_path / "odis_inclusion_jobs_coverage.parquet"
    source_path.write_bytes(b"jobs")
    coverage_path.write_bytes(b"coverage")
    candidate_output = tmp_path / "candidate"

    monkeypatch.setattr(ingest, "OUTPUT_DIR", candidate_output)
    monkeypatch.setattr(ingest, "INCLUSION_OUTPUT_PATH", source_path)
    monkeypatch.setattr(ingest, "INCLUSION_SHARED_OUTPUT_PATH", source_path)
    monkeypatch.setattr(ingest, "INCLUSION_COVERAGE_SHARED_OUTPUT_PATH", coverage_path)
    logger = MagicMock()

    ingest.clean_inclusion_jobs({}, logger)

    logger.log_source.assert_called_once()
    source_key, status, artifact = logger.log_source.call_args.args
    assert (source_key, status) == ("inclusion_jobs", "CACHED")
    assert artifact == candidate_output / source_path.name
    assert logger.log_source.call_args.kwargs["observed_at"].endswith("+00:00")


def test_pipeline_logger_uses_utc_timestamps(tmp_path):
    status_path = tmp_path / "run.json"
    cache_path = tmp_path / "cache.parquet"
    cache_path.write_bytes(b"cache")
    cache_mtime = 1_700_000_000
    os.utime(cache_path, (cache_mtime, cache_mtime))
    logger = PipelineLogger(status_path)

    logger.log_source("source", "REFRESHED")
    logger.log_source("cached_source", "CACHED", cache_path)
    logger.log_step("step", "COMPLETED")

    status = json.loads(status_path.read_text())
    assert status["sources"]["source"]["timestamp"].endswith("+00:00")
    assert status["sources"]["cached_source"]["timestamp"] == datetime.fromtimestamp(
        cache_mtime, tz=timezone.utc
    ).isoformat()
    assert status["steps"]["step"]["timestamp"].endswith("+00:00")


def test_muted_logger_forwards_source_freshness_metadata():
    real_logger = MagicMock()
    muted_logger = ingest.MutedPipelineLogger(real_logger)

    muted_logger.log_source(
        "france_travail_live",
        "CACHED",
        "/tmp/jobs.parquet",
        observed_at="2026-08-04T15:00:00+00:00",
    )

    real_logger.log_source.assert_called_once_with(
        "france_travail_live",
        "CACHED",
        "/tmp/jobs.parquet",
        observed_at="2026-08-04T15:00:00+00:00",
    )
