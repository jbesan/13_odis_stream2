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


def test_fetch_gcp_billing_data():
    """Verify that fetch_gcp_billing_data constructs the query and returns a valid ServiceOutcome."""
    from services.analytics_data import fetch_gcp_billing_data

    mock_client = MagicMock()
    mock_client.project = "test-project"

    df_dummy = pd.DataFrame(
        [
            {
                "usage_date": "2026-08-30",
                "project_id": "odis-stream2-app",
                "service_name": "Vertex AI",
                "sku_description": "Generative AI",
                "currency": "EUR",
                "cost_gross": 2.50,
                "credits": 0.0,
                "cost_net": 2.50,
            }
        ]
    )

    mock_job = MagicMock()
    mock_job.to_dataframe.return_value = df_dummy
    mock_client.query.return_value = mock_job

    fn = getattr(fetch_gcp_billing_data, "__wrapped__", fetch_gcp_billing_data)
    outcome = fn(mock_client, 30)

    assert outcome.status == OutcomeStatus.SUCCESS
    assert outcome.value is not None
    assert len(outcome.value) == 1
    assert outcome.value.iloc[0]["service_name"] == "Vertex AI"

    # Verify query contains INTERVAL 30 DAY and projects filter
    query_arg = mock_client.query.call_args[0][0]
    assert "INTERVAL 30 DAY" in query_arg
    assert "'odis-stream2-app'" in query_arg


def test_fetch_gcp_billing_data_none_client():
    """Verify that fetch_gcp_billing_data handles None client gracefully."""
    from services.analytics_data import fetch_gcp_billing_data

    fn = getattr(fetch_gcp_billing_data, "__wrapped__", fetch_gcp_billing_data)
    outcome = fn(None, 30)

    assert outcome.status == OutcomeStatus.UNAVAILABLE
    assert outcome.value is None


def test_fetch_agent_costs_data():
    """Verify that fetch_agent_costs_data queries agent_state_logs correctly."""
    from services.analytics_data import fetch_agent_costs_data

    mock_client = MagicMock()
    mock_client.project = "test-project"

    df_dummy = pd.DataFrame(
        [
            {
                "usage_date": "2026-08-30",
                "run_count": 5,
                "total_estimated_cost_eur": 0.12,
            }
        ]
    )

    mock_job = MagicMock()
    mock_job.to_dataframe.return_value = df_dummy
    mock_client.query.return_value = mock_job

    fn = getattr(fetch_agent_costs_data, "__wrapped__", fetch_agent_costs_data)
    outcome = fn(mock_client, 30)

    assert outcome.status == OutcomeStatus.SUCCESS
    assert outcome.value is not None
    assert outcome.value.iloc[0]["run_count"] == 5
