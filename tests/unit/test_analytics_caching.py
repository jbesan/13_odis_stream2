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
    assert "AND env = 'production'" in searches_query_arg

    usage_query_arg = mock_client.query.call_args_list[1][0][0]
    assert "INTERVAL 30 DAY" in usage_query_arg
    assert "AND env = 'production'" in usage_query_arg


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
                "usage_amount": 1000.0,
                "usage_unit": "count",
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
    assert outcome.value.iloc[0]["usage_amount"] == 1000.0
    assert outcome.value.iloc[0]["usage_unit"] == "count"

    # Verify query contains INTERVAL 30 DAY and projects filter
    query_arg = mock_client.query.call_args[0][0]
    assert "INTERVAL 30 DAY" in query_arg
    assert "'odis-stream2-app'" in query_arg
    assert "usage_amount" in query_arg
    assert "usage_unit" in query_arg


def test_format_billing_usage():
    """Verify format_billing_usage formats quantities and units properly."""
    from services.analytics_data import format_billing_usage

    assert format_billing_usage(None, "second") == "-"
    assert format_billing_usage(0.0, "second") == "-"
    assert format_billing_usage(125742.9, "second") == "125 742.9 s"
    assert format_billing_usage(1046884.0, "count") == "1 046 884 tokens/req"
    assert format_billing_usage(15.18, "month") == "15.18 mois"
    assert format_billing_usage(1.34, "gibibyte month") == "1.34 GiB·mois"
    assert format_billing_usage(0.03, "gibibyte") == "0.0300 GiB"



def test_fetch_gcp_billing_data_none_client():
    """Verify that fetch_gcp_billing_data handles None client gracefully."""
    from services.analytics_data import fetch_gcp_billing_data

    fn = getattr(fetch_gcp_billing_data, "__wrapped__", fetch_gcp_billing_data)
    outcome = fn(None, 30)

    assert outcome.status == OutcomeStatus.UNAVAILABLE
    assert outcome.value is None


def test_fetch_agent_costs_data():
    """Verify that fetch_agent_costs_data queries agent_state_logs correctly with cost_details."""
    from services.analytics_data import fetch_agent_costs_data

    mock_client = MagicMock()
    mock_client.project = "test-project"

    df_dummy = pd.DataFrame(
        [
            {
                "usage_date": "2026-08-30",
                "run_count": 5,
                "total_estimated_cost_eur": 0.12,
                "input_tokens_new": 50000,
                "input_tokens_cached": 15000,
                "output_tokens": 6000,
                "grounding_queries": 5,
                "places_requests": 8,
                "token_cost_eur": 0.10,
                "grounding_cost_eur": 0.01,
                "places_cost_eur": 0.01,
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
    assert outcome.value.iloc[0]["input_tokens_new"] == 50000
    query_executed = mock_client.query.call_args[0][0]
    assert "env = 'production'" in query_executed
    assert "cost_details" in query_executed
    assert "input_tokens_new" in query_executed


def test_resolve_analytics_project_and_env_filter():
    """Verify project resolution and SQL filter construction for different environments."""
    from services.analytics_data import resolve_analytics_project, build_env_filter

    mock_client = MagicMock()
    mock_client.project = "odis-stream2"

    assert resolve_analytics_project(mock_client, "production") == "odis-stream2-app"
    assert resolve_analytics_project(mock_client, "staging") == "odis-stream2-app"
    assert resolve_analytics_project(mock_client, "local") == "odis-stream2"

    assert build_env_filter("production") == "AND env = 'production'"
    assert build_env_filter("staging") == "AND env = 'staging'"
    assert build_env_filter("local") == "AND (env = 'local' OR env IS NULL)"
    assert build_env_filter(None) == ""
    assert build_env_filter("all") == ""
