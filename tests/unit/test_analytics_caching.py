from unittest.mock import MagicMock
import pandas as pd
from services.analytics_data import (
    ParseStats,
    fetch_analytics_data,
    parse_json_payload,
)
from services.service_outcomes import OutcomeStatus


def test_fetch_analytics_data_caching():
    """Verify that fetch_analytics_data queries BigQuery correctly and uses unhashed client."""
    mock_client = MagicMock()
    mock_client.project = "test-project"

    df_dummy_searches = pd.DataFrame([{"interaction_id": "1", "org_id": "test_org"}])
    df_dummy_usage = pd.DataFrame([{"interaction_id": "1", "event_name": "test_event"}])

    mock_query_job_1 = MagicMock()
    mock_query_job_1.to_dataframe.return_value = df_dummy_searches

    mock_query_job_2 = MagicMock()
    mock_query_job_2.to_dataframe.return_value = df_dummy_usage

    mock_client.query.side_effect = [mock_query_job_1, mock_query_job_2]

    fn = getattr(fetch_analytics_data, "__wrapped__", fetch_analytics_data)
    result = fn(mock_client, 30)

    assert result.status == OutcomeStatus.SUCCESS
    assert result.searches.status == OutcomeStatus.SUCCESS
    assert result.usage.status == OutcomeStatus.SUCCESS
    assert result.searches.value is not None and not result.searches.value.empty
    assert result.usage.value is not None and not result.usage.value.empty
    assert mock_client.query.call_count == 2

    searches_query_arg = mock_client.query.call_args_list[0][0][0]
    assert "INTERVAL 30 DAY" in searches_query_arg


def test_fetch_analytics_data_none_client():
    """Verify that fetch_analytics_data returns empty DataFrames gracefully when client is None."""
    fn = getattr(fetch_analytics_data, "__wrapped__", fetch_analytics_data)
    result = fn(None, 30)
    assert result.status == OutcomeStatus.UNAVAILABLE
    assert result.searches.value is None
    assert result.usage.value is None


def test_fetch_analytics_data_is_partial_when_one_query_fails():
    mock_client = MagicMock()
    mock_client.project = "test-project"
    search_job = MagicMock()
    search_job.to_dataframe.return_value = pd.DataFrame([{"interaction_id": "1"}])
    mock_client.query.side_effect = [search_job, RuntimeError("BQ unavailable")]

    fn = getattr(fetch_analytics_data, "__wrapped__", fetch_analytics_data)
    result = fn(mock_client, 30)

    assert result.status == OutcomeStatus.PARTIAL
    assert result.searches.status == OutcomeStatus.SUCCESS
    assert result.usage.status == OutcomeStatus.UNAVAILABLE
    assert result.usage.value is None


def test_parse_json_payload_counts_invalid_rows():
    stats = ParseStats()

    assert parse_json_payload('{"valid": true}', stats, expected_type=dict) == {
        "valid": True
    }
    assert parse_json_payload("not-json", stats, expected_type=dict) is None
    assert parse_json_payload("[]", stats, expected_type=dict) is None

    assert stats.valid_rows == 1
    assert stats.invalid_rows == 2
