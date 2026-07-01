import os
import pytest
from unittest.mock import patch, MagicMock
from services.mcp_inclusion import _get_access_token, TOKEN_CACHE


@pytest.fixture(autouse=True)
def reset_token_cache():
    """Reset token cache before each test."""
    TOKEN_CACHE["token"] = None
    TOKEN_CACHE["last_refresh"] = 0.0
    yield


def test_get_access_token_prioritizes_static_token():
    """Verify that _get_access_token uses EMPLOIS_INCLUSION_TOKEN if present."""
    with patch.dict(os.environ, {"EMPLOIS_INCLUSION_TOKEN": "mock_static_token_123"}):
        with patch("requests.post") as mock_post:
            token = _get_access_token()
            assert token == "mock_static_token_123"
            # Ensure no HTTP auth request was made
            mock_post.assert_not_called()


def test_get_access_token_fallback_to_login_pwd():
    """Verify that _get_access_token falls back to login/pwd if no static token is present."""
    mock_env = {
        "EMPLOIS_INCLUSION_LOGIN": "test_user",
        "EMPLOIS_INCLUSION_PWD": "test_password",
    }
    # Ensure EMPLOIS_INCLUSION_TOKEN is not in env
    if "EMPLOIS_INCLUSION_TOKEN" in os.environ:
        del os.environ["EMPLOIS_INCLUSION_TOKEN"]

    with patch.dict(os.environ, mock_env, clear=False):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"token": "dynamic_token_abc"}

        with patch("requests.post", return_value=mock_response) as mock_post:
            token = _get_access_token()
            assert token == "dynamic_token_abc"
            mock_post.assert_called_once()
