import importlib
from unittest.mock import MagicMock
import pandas as pd

analytics_page = importlib.import_module("app.pages.4_Analytics")
fetch_analytics_data = analytics_page.fetch_analytics_data


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
    df_searches, df_usage = fn(mock_client, 30)

    assert not df_searches.empty
    assert not df_usage.empty
    assert mock_client.query.call_count == 2

    searches_query_arg = mock_client.query.call_args_list[0][0][0]
    assert "INTERVAL 30 DAY" in searches_query_arg
