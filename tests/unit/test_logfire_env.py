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
    # Test that setup_logfire disables remote sending in production (GDPR compliance)
    mock_logfire = MagicMock()
    mock_logfire.DEFAULT_LOGFIRE_INSTANCE.config.environment = None

    env_mock = os.environ.copy()
    env_mock["ODIS_DEPLOYMENT_ENV"] = "production"
    env_mock["K_SERVICE"] = "odis-app"
    env_mock["LOGFIRE_TOKEN"] = "some-token"

    with patch("utils.logger.logfire", mock_logfire), patch("os.environ", env_mock):
        setup_logfire()

        mock_logfire.configure.assert_called_once_with(
            service_name="odis-stream2",
            environment="production",
            send_to_logfire=False,
            console=False,
        )
        mock_logfire.instrument_pydantic_ai.assert_called_once()
        mock_logfire.instrument_httpx.assert_called_once()


def test_setup_logfire_staging():
    # Test that setup_logfire enables remote sending in staging even when K_SERVICE is set
    mock_logfire = MagicMock()
    mock_logfire.DEFAULT_LOGFIRE_INSTANCE.config.environment = None

    env_mock = os.environ.copy()
    env_mock["ODIS_DEPLOYMENT_ENV"] = "staging"
    env_mock["K_SERVICE"] = "odis-app-staging"
    env_mock["LOGFIRE_TOKEN"] = "some-token"

    with patch("utils.logger.logfire", mock_logfire), patch("os.environ", env_mock):
        setup_logfire()

        mock_logfire.configure.assert_called_once_with(
            service_name="odis-stream2",
            environment="staging",
            send_to_logfire="if-token-present",
            console=False,
        )
        mock_logfire.instrument_pydantic_ai.assert_called_once()
        mock_logfire.instrument_httpx.assert_called_once()


def test_config_logfire_helpers(monkeypatch):
    import config as cfg

    monkeypatch.delenv("ODIS_DEPLOYMENT_ENV", raising=False)
    assert cfg.get_deployment_env() == "local"
    assert cfg.is_logfire_enabled() is True

    monkeypatch.setenv("ODIS_DEPLOYMENT_ENV", "staging")
    assert cfg.get_deployment_env() == "staging"
    assert cfg.is_logfire_enabled() is True

    for prod_env in ("production", "prod", "unknown"):
        monkeypatch.setenv("ODIS_DEPLOYMENT_ENV", prod_env)
        assert cfg.is_logfire_enabled() is False


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
            console=False,
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


def test_heavy_logfire_instrument_decorators_disable_extract_args():
    """Verify graph steps and scoring decorators do not serialize large context/DataFrame args."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    targets = {
        root / "app" / "agents" / "graph.py": {
            "triage_step",
            "expert_worker_step",
            "synthesizer_step",
        },
        root / "app" / "core" / "scoring.py": {"_compute_weighted_score"},
    }

    for path, fn_names in targets.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in fn_names:
                found.add(node.name)
                inst_calls = [
                    dec
                    for dec in node.decorator_list
                    if isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "instrument"
                ]
                assert inst_calls, f"Missing @logfire.instrument on {path.name}:{node.name}"
                for call in inst_calls:
                    extract_kw = [kw for kw in call.keywords if kw.arg == "extract_args"]
                    assert extract_kw and isinstance(extract_kw[0].value, ast.Constant) and extract_kw[0].value.value is False, (
                        f"{path.name}:{node.name} must pass extract_args=False to @logfire.instrument"
                    )
        assert found == fn_names
