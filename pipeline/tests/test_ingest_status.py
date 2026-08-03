from pipeline import ingest


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
