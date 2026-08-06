import os
import json
import logging
from unittest.mock import MagicMock, patch

from utils.logger import JsonFormatter, setup_logfire


def test_cloud_run_json_log_is_single_line(monkeypatch):
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="provider unavailable", args=(), exc_info=None,
    )
    record.extra_data = {"error_code": "P3-07-CHECK"}

    monkeypatch.setenv("K_SERVICE", "odis-app")
    payload = JsonFormatter().format(record)

    assert "\n" not in payload
    assert json.loads(payload)["severity"] == "ERROR"


def test_setup_logfire_prod():
    # Test that setup_logfire configures environment as 'prod' and disables remote sending on GCP (GDPR compliance)
    mock_logfire = MagicMock()
    mock_logfire.DEFAULT_LOGFIRE_INSTANCE.config.environment = None

    with (
        patch("utils.logger.logfire", mock_logfire),
        patch.dict(
            os.environ,
            {"K_SERVICE": "my-cloud-run-service", "LOGFIRE_TOKEN": "some-token"},
        ),
    ):
        setup_logfire()

        mock_logfire.configure.assert_called_once_with(
            service_name="odis-stream2",
            environment="prod",
            send_to_logfire=False,
        )
        mock_logfire.instrument_pydantic_ai.assert_called_once()
        mock_logfire.instrument_httpx.assert_called_once()


def test_setup_logfire_local():
    # Test that setup_logfire configures environment as 'local' when K_SERVICE is not set
    mock_logfire = MagicMock()
    mock_logfire.DEFAULT_LOGFIRE_INSTANCE.config.environment = None

    # Force pop K_SERVICE from mock environment dictionary
    env_mock = os.environ.copy()
    env_mock.pop("K_SERVICE", None)
    env_mock["LOGFIRE_TOKEN"] = "some-token"

    with patch("utils.logger.logfire", mock_logfire), patch("os.environ", env_mock):
        setup_logfire()

        mock_logfire.configure.assert_called_once_with(
            service_name="odis-stream2",
            environment="local",
            send_to_logfire="if-token-present",
        )
        mock_logfire.instrument_pydantic_ai.assert_called_once()
        mock_logfire.instrument_httpx.assert_called_once()


def test_setup_logfire_skips_when_test():
    # Test that setup_logfire returns early and does not call configure if environment is already 'test'
    mock_logfire = MagicMock()
    mock_logfire.DEFAULT_LOGFIRE_INSTANCE.config.environment = "test"

    with (
        patch("utils.logger.logfire", mock_logfire),
        patch.dict(os.environ, {"LOGFIRE_TOKEN": "some-token"}),
    ):
        setup_logfire()

        mock_logfire.configure.assert_not_called()
        mock_logfire.instrument_pydantic_ai.assert_not_called()
        mock_logfire.instrument_httpx.assert_not_called()
