import os
import stat
import tomllib
import pytest

from generate_secrets import (
    generate_secrets_file,
    load_authorization_policy,
    serialize_secrets_toml,
    validate_secrets_toml,
)
from utils.oidc_policy import OIDCAuthorizationPolicy


VALID_POLICY_JSON = """{
  "allowed_domains": {"jaccueille.fr": "jaccueille"},
  "allowed_emails": {"admin@partner.org": "agir33"}
}"""


def test_load_authorization_policy_success():
    """Verify loading valid authorization policy."""
    policy = load_authorization_policy(VALID_POLICY_JSON, is_cloud_run=True)
    assert policy.allowed_domains == {"jaccueille.fr": "jaccueille"}
    assert policy.allowed_emails == {"admin@partner.org": "agir33"}


def test_serialize_and_validate_secrets_toml():
    """Ensure TOML serialization generates valid structure matching policy."""
    policy = OIDCAuthorizationPolicy(
        allowed_domains={"jaccueille.fr": "jaccueille"},
        allowed_emails={"user@partner.org": "agir33"},
    )
    content = serialize_secrets_toml(
        policy=policy,
        client_id="test-client-id.apps.googleusercontent.com",
        client_secret="test-client-secret",
        cookie_secret="test-cookie-secret-32-chars-long",
        redirect_uri="https://test.app/oauth2callback",
        admin_users=["admin@jaccueille.fr"],
    )

    parsed = validate_secrets_toml(content)
    assert parsed["auth"]["redirect_uri"] == "https://test.app/oauth2callback"
    assert parsed["auth"]["cookie_secret"] == "test-cookie-secret-32-chars-long"
    assert parsed["auth"]["allowed_domains"] == ["jaccueille.fr"]
    assert parsed["auth"]["allowed_emails"] == ["user@partner.org"]
    assert parsed["auth"]["admin_users"] == ["admin@jaccueille.fr"]
    assert parsed["auth"]["google"]["client_id"] == "test-client-id.apps.googleusercontent.com"
    assert parsed["auth"]["domain_org_mapping"] == {"jaccueille.fr": "jaccueille"}
    assert parsed["auth"]["email_org_mapping"] == {"user@partner.org": "agir33"}


def test_generate_secrets_file_creates_mode_0600(tmp_path, monkeypatch):
    """Verify secrets.toml is written with strict 0600 permissions."""
    target_path = str(tmp_path / ".streamlit" / "secrets.toml")

    monkeypatch.setenv("OIDC_AUTHORIZATION_POLICY_JSON", VALID_POLICY_JSON)
    monkeypatch.setenv("OIDC_CLIENT_ID", "mock-client-id")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "mock-client-secret")
    monkeypatch.setenv("OIDC_COOKIE_SECRET", "mock-cookie-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://mock.app/oauth2callback")
    monkeypatch.setenv("ADMIN_USERS_JSON", '["admin@mock.app"]')

    created_path = generate_secrets_file(target_path, is_cloud_run=False)
    assert created_path == target_path
    assert os.path.exists(target_path)

    # Check permissions are 0600 (owner read/write only)
    file_mode = stat.S_IMODE(os.stat(target_path).st_mode)
    assert file_mode == 0o600

    # Check contents are valid TOML
    with open(target_path, "rb") as f:
        data = tomllib.load(f)
    assert data["auth"]["google"]["client_id"] == "mock-client-id"
    assert data["auth"]["domain_org_mapping"]["jaccueille.fr"] == "jaccueille"


def test_generate_secrets_fails_closed_on_cloud_run_without_policy(tmp_path, monkeypatch):
    """Verify Cloud Run runtime boot fails if authorization policy is missing."""
    target_path = str(tmp_path / "secrets.toml")
    monkeypatch.delenv("OIDC_AUTHORIZATION_POLICY_JSON", raising=False)

    with pytest.raises(RuntimeError, match="OIDC authorization policy is invalid"):
        generate_secrets_file(target_path, is_cloud_run=True)


def test_validate_secrets_toml_rejects_missing_sections():
    """Verify validation detects missing sections or mandatory keys."""
    invalid_toml = '[auth]\nredirect_uri = "https://example.com"\n'
    with pytest.raises(ValueError, match="Missing 'cookie_secret'"):
        validate_secrets_toml(invalid_toml)

    missing_auth = 'key = "value"\n'
    with pytest.raises(ValueError, match="Missing or invalid \\[auth\\] section"):
        validate_secrets_toml(missing_auth)
