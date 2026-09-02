"""BigQuery boundary and payload helpers used by the Analytics page."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st
from google.api_core import exceptions as google_exceptions
from google.cloud import bigquery

from services.service_outcomes import OutcomeStatus, ServiceOutcome


logger = logging.getLogger("services.analytics_data")
dataset_id = "odis_logs"


@dataclass(frozen=True)
class AnalyticsDataResult:
    """The independent outcomes of the two analytics queries.

    A failed query deliberately carries no dataframe. This prevents a caller
    from mistaking an unavailable table for a legitimate empty observation.
    """

    searches: ServiceOutcome[pd.DataFrame]
    usage: ServiceOutcome[pd.DataFrame]

    @property
    def status(self) -> OutcomeStatus:
        statuses = {self.searches.status, self.usage.status}
        if statuses <= {OutcomeStatus.SUCCESS, OutcomeStatus.SUCCESS_EMPTY}:
            return (
                OutcomeStatus.SUCCESS
                if OutcomeStatus.SUCCESS in statuses
                else OutcomeStatus.SUCCESS_EMPTY
            )
        if self.searches.is_success or self.usage.is_success:
            return OutcomeStatus.PARTIAL
        if OutcomeStatus.UNAUTHORIZED in statuses:
            return OutcomeStatus.UNAUTHORIZED
        return OutcomeStatus.UNAVAILABLE


@dataclass
class ParseStats:
    """Counts invalid persisted JSON rows without logging each raw payload."""

    invalid_rows: int = 0
    valid_rows: int = 0

    def record(self, value: Any) -> None:
        if value is None:
            self.invalid_rows += 1
        else:
            self.valid_rows += 1


@st.cache_resource(ttl=300)
def get_bq_client():
    """Return a BigQuery client, logging an operational failure at ERROR."""
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        logger.error(
            "Analytics BigQuery project is not configured",
            extra={"extra_data": {"error_code": "ANALYTICS-BQ-NOT-CONFIGURED"}},
        )
        return None
    try:
        return bigquery.Client()
    except Exception:
        logger.error(
            "Analytics BigQuery client initialization failed",
            extra={"extra_data": {"error_code": "ANALYTICS-BQ-UNAVAILABLE"}},
            exc_info=True,
        )
        return None


def _query_outcome(
    client: Any, query: str, table_name: str
) -> ServiceOutcome[pd.DataFrame]:
    """Query one table and retain the reason when no dataframe is available."""
    try:
        dataframe = client.query(query).to_dataframe(create_bqstorage_client=False)
    except (google_exceptions.Forbidden, google_exceptions.Unauthorized):
        logger.error(
            "Analytics BigQuery query unauthorized: table=%s",
            table_name,
            extra={
                "extra_data": {
                    "operation": "analytics_query",
                    "table": table_name,
                    "outcome": OutcomeStatus.UNAUTHORIZED.value,
                    "error_code": "ANALYTICS-BQ-UNAUTHORIZED",
                }
            },
            exc_info=True,
        )
        return ServiceOutcome(
            status=OutcomeStatus.UNAUTHORIZED,
            error_code="ANALYTICS-BQ-UNAUTHORIZED",
        )
    except google_exceptions.GoogleAPICallError:
        logger.error(
            "Analytics BigQuery query failed: table=%s",
            table_name,
            extra={
                "extra_data": {
                    "operation": "analytics_query",
                    "table": table_name,
                    "outcome": OutcomeStatus.UNAVAILABLE.value,
                    "error_code": "ANALYTICS-BQ-UNAVAILABLE",
                }
            },
            exc_info=True,
        )
        return ServiceOutcome(
            status=OutcomeStatus.UNAVAILABLE,
            error_code="ANALYTICS-BQ-UNAVAILABLE",
        )
    except Exception:
        # An outer boundary is intentionally broad: unexpected provider/client
        # errors are still operational incidents and must page with a traceback.
        logger.error(
            "Analytics BigQuery query failed unexpectedly: table=%s",
            table_name,
            extra={
                "extra_data": {
                    "operation": "analytics_query",
                    "table": table_name,
                    "outcome": OutcomeStatus.UNAVAILABLE.value,
                    "error_code": "ANALYTICS-BQ-UNAVAILABLE",
                }
            },
            exc_info=True,
        )
        return ServiceOutcome(
            status=OutcomeStatus.UNAVAILABLE,
            error_code="ANALYTICS-BQ-UNAVAILABLE",
        )

    if dataframe.empty:
        return ServiceOutcome(status=OutcomeStatus.SUCCESS_EMPTY, value=dataframe)
    return ServiceOutcome(status=OutcomeStatus.SUCCESS, value=dataframe)


@st.cache_data
def fetch_analytics_data(_client: Any, days: int) -> AnalyticsDataResult:
    """Fetch analytics while keeping empty, failed and partial results distinct."""
    if _client is None:
        unavailable = ServiceOutcome[pd.DataFrame](
            status=OutcomeStatus.UNAVAILABLE,
            error_code="ANALYTICS-BQ-UNAVAILABLE",
        )
        return AnalyticsDataResult(searches=unavailable, usage=unavailable)

    query_searches = f"""
        SELECT
            interaction_id,
            timestamp,
            username,
            IFNULL(org_id, 'défaut') AS org_id,
            IFNULL(search_hash, '') AS search_hash,
            source_flow,
            search_criteria,
            weights,
            top_results,
            detailed_breakdown
        FROM `{_client.project}.{dataset_id}.search_events`
        WHERE TIMESTAMP(timestamp) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        ORDER BY timestamp DESC
    """
    query_usage = f"""
        SELECT
            interaction_id,
            login_session_id,
            timestamp,
            username,
            IFNULL(org_id, 'défaut') AS org_id,
            event_name,
            payload
        FROM `{_client.project}.{dataset_id}.usage_events`
        WHERE TIMESTAMP(timestamp) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        ORDER BY timestamp DESC
    """
    return AnalyticsDataResult(
        searches=_query_outcome(_client, query_searches, "search_events"),
        usage=_query_outcome(_client, query_usage, "usage_events"),
    )


BILLING_EXPORT_DEFAULT_TABLE = (
    "odis-stream2-app.odis_logs.gcp_billing_export_v1_011680_B35255_2DA84B"
)


@st.cache_data
def fetch_gcp_billing_data(
    _client: Any,
    days: int,
    projects: tuple[str, ...] = ("odis-stream2-app",),
) -> ServiceOutcome[pd.DataFrame]:
    """Fetch GCP billing export records aggregated by day, project, service and SKU."""
    if _client is None:
        return ServiceOutcome[pd.DataFrame](
            status=OutcomeStatus.UNAVAILABLE,
            error_code="ANALYTICS-BQ-UNAVAILABLE",
        )

    billing_table = os.getenv("ODIS_BILLING_EXPORT_TABLE", BILLING_EXPORT_DEFAULT_TABLE)
    project_list_sql = ", ".join(f"'{p}'" for p in projects)

    query_billing = f"""
        SELECT
            DATE(usage_start_time) AS usage_date,
            project.id AS project_id,
            IFNULL(project.name, project.id) AS project_name,
            service.description AS service_name,
            sku.description AS sku_description,
            currency,
            SUM(cost) AS cost_gross,
            SUM((SELECT IFNULL(SUM(c.amount), 0) FROM UNNEST(credits) c)) AS credits,
            SUM(cost + (SELECT IFNULL(SUM(c.amount), 0) FROM UNNEST(credits) c)) AS cost_net
        FROM `{billing_table}`
        WHERE usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
          AND project.id IN ({project_list_sql})
        GROUP BY usage_date, project_id, project_name, service_name, sku_description, currency
        ORDER BY usage_date DESC, cost_net DESC
    """
    return _query_outcome(_client, query_billing, "gcp_billing_export")


@st.cache_data
def fetch_agent_costs_data(_client: Any, days: int) -> ServiceOutcome[pd.DataFrame]:
    """Fetch AI agent execution estimated costs aggregated by day."""
    if _client is None:
        return ServiceOutcome[pd.DataFrame](
            status=OutcomeStatus.UNAVAILABLE,
            error_code="ANALYTICS-BQ-UNAVAILABLE",
        )

    query_agent_costs = f"""
        SELECT
            DATE(timestamp) AS usage_date,
            COUNT(*) AS run_count,
            SUM(cost_eur) AS total_estimated_cost_eur
        FROM `{_client.project}.{dataset_id}.agent_state_logs`
        WHERE TIMESTAMP(timestamp) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        GROUP BY usage_date
        ORDER BY usage_date DESC
    """
    return _query_outcome(_client, query_agent_costs, "agent_state_logs")


def clear_analytics_cache() -> None:
    """Invalidate only the query cache owned by this page."""
    fetch_analytics_data.clear()
    fetch_gcp_billing_data.clear()
    fetch_agent_costs_data.clear()


def parse_json_payload(
    raw_value: Any,
    stats: ParseStats,
    *,
    expected_type: type | tuple[type, ...] | None = None,
) -> Any | None:
    """Parse persisted JSON and count invalid non-empty rows safely."""
    if raw_value is None or raw_value == "":
        return None
    if not isinstance(raw_value, str):
        value = raw_value
    else:
        try:
            value = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            stats.record(None)
            return None
    if expected_type is not None and not isinstance(value, expected_type):
        stats.record(None)
        return None
    stats.record(value)
    return value


def log_invalid_payload_summary(widget: str, stats: ParseStats) -> None:
    """Emit one structured summary instead of swallowing malformed rows."""
    if not stats.invalid_rows:
        return
    logger.warning(
        "Analytics payload rows discarded: widget=%s invalid_rows=%d valid_rows=%d",
        widget,
        stats.invalid_rows,
        stats.valid_rows,
        extra={
            "extra_data": {
                "operation": "analytics_payload_parse",
                "widget": widget,
                "invalid_rows": stats.invalid_rows,
                "valid_rows": stats.valid_rows,
                "outcome": OutcomeStatus.INVALID_PAYLOAD.value,
            }
        },
    )
    if stats.valid_rows == 0:
        logger.error(
            "Analytics widget has no valid payload rows: widget=%s",
            widget,
            extra={
                "extra_data": {
                    "operation": "analytics_payload_parse",
                    "widget": widget,
                    "invalid_rows": stats.invalid_rows,
                    "error_code": "ANALYTICS-PAYLOAD-INVALID",
                }
            },
        )
