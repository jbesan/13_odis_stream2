"""Generates .streamlit/secrets.toml from runtime environment variables.

Runs at container boot on Cloud Run before Streamlit starts. Translates Secret
Manager environment variables into Streamlit-compatible TOML configuration,
enforces restrictive file permissions (0600), and validates structural integrity
using tomllib.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tomllib
from typing import Any, Mapping

from utils.oidc_policy import (
    OIDCAuthorizationPolicy,
    OIDCAuthorizationPolicyError,
    load_runtime_oidc_authorization_policy,
)

logger = logging.getLogger(__name__)

DEFAULT_SECRETS_PATH = "/app/.streamlit/secrets.toml"


def load_authorization_policy(
    policy_json: str | None = None,
    *,
    is_cloud_run: bool | None = None,
) -> OIDCAuthorizationPolicy:
    """Load the Secret Manager policy and fail closed on Cloud Run.

    Args:
        policy_json: Raw JSON policy string. Defaults to env var OIDC_AUTHORIZATION_POLICY_JSON.
        is_cloud_run: Whether running on Cloud Run. Defaults to bool(os.getenv("K_SERVICE")).

    Returns:
        OIDCAuthorizationPolicy: Parsed and validated policy object.

    Raises:
        RuntimeError: If policy parsing fails.
    """
    if policy_json is None:
        policy_json = os.getenv("OIDC_AUTHORIZATION_POLICY_JSON")
    if is_cloud_run is None:
        is_cloud_run = bool(os.getenv("K_SERVICE"))

    try:
        return load_runtime_oidc_authorization_policy(
            policy_json,
            is_cloud_run=is_cloud_run,
        )
    except OIDCAuthorizationPolicyError as exc:
        raise RuntimeError("OIDC authorization policy is invalid") from exc


def serialize_secrets_toml(
    policy: OIDCAuthorizationPolicy,
    *,
    client_id: str,
    client_secret: str,
    cookie_secret: str,
    redirect_uri: str,
    admin_users: list[str] | None = None,
) -> str:
    """Serialize authorization and OIDC settings into standard TOML format.

    Args:
        policy: Normalized OIDCAuthorizationPolicy.
        client_id: OIDC Google client ID.
        client_secret: OIDC Google client secret.
        cookie_secret: Streamlit cookie secret.
        redirect_uri: OIDC redirect URI.
        admin_users: List of admin user emails.

    Returns:
        str: Serialized TOML content.
    """
    admin_list = admin_users or []
    allowed_domains = list(policy.allowed_domains.keys())
    allowed_emails = list(policy.allowed_emails.keys())

    lines: list[str] = [
        "[auth]",
        f"redirect_uri = {json.dumps(redirect_uri)}",
        f"cookie_secret = {json.dumps(cookie_secret)}",
        f"allowed_domains = {json.dumps(allowed_domains)}",
        f"allowed_emails = {json.dumps(allowed_emails)}",
        f"admin_users = {json.dumps(admin_list)}",
        "",
        "[auth.google]",
        f"client_id = {json.dumps(client_id)}",
        f"client_secret = {json.dumps(client_secret)}",
        'server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"',
        "",
        "[auth.domain_org_mapping]",
    ]

    for domain, org_id in policy.allowed_domains.items():
        lines.append(f"{json.dumps(domain)} = {json.dumps(org_id)}")

    lines.append("")
    lines.append("[auth.email_org_mapping]")
    for email, org_id in policy.allowed_emails.items():
        lines.append(f"{json.dumps(email)} = {json.dumps(org_id)}")

    lines.append("")
    return "\n".join(lines)


def validate_secrets_toml(toml_content: str) -> dict[str, Any]:
    """Validate that the TOML content parses correctly and contains required structure.

    Args:
        toml_content: Serialized TOML string.

    Returns:
        dict[str, Any]: Parsed TOML dictionary.

    Raises:
        ValueError: If TOML syntax is invalid or required keys are missing.
    """
    try:
        data = tomllib.loads(toml_content)
    except Exception as exc:
        raise ValueError(f"Generated TOML syntax is invalid: {exc}") from exc

    if not isinstance(data.get("auth"), Mapping):
        raise ValueError("Missing or invalid [auth] section in generated secrets TOML")

    auth = data["auth"]
    for key in ("redirect_uri", "cookie_secret", "allowed_domains", "allowed_emails", "admin_users"):
        if key not in auth:
            raise ValueError(f"Missing '{key}' in [auth] section")

    if not isinstance(auth.get("google"), Mapping):
        raise ValueError("Missing or invalid [auth.google] section in generated secrets TOML")

    google = auth["google"]
    for key in ("client_id", "client_secret", "server_metadata_url"):
        if key not in google:
            raise ValueError(f"Missing '{key}' in [auth.google] section")

    if not isinstance(auth.get("domain_org_mapping"), Mapping):
        raise ValueError("Missing [auth.domain_org_mapping] section in generated secrets TOML")

    if not isinstance(auth.get("email_org_mapping"), Mapping):
        raise ValueError("Missing [auth.email_org_mapping] section in generated secrets TOML")

    return data


def generate_secrets_file(
    target_path: str | None = None,
    *,
    policy_json: str | None = None,
    is_cloud_run: bool | None = None,
) -> str:
    """Generate, write with mode 0600, and validate the Streamlit secrets.toml file.

    Args:
        target_path: Destination path for secrets.toml. Defaults to /app/.streamlit/secrets.toml
            if /app exists, or .streamlit/secrets.toml locally.
        policy_json: Optional override for OIDC_AUTHORIZATION_POLICY_JSON.
        is_cloud_run: Optional override for Cloud Run detection.

    Returns:
        str: Absolute path to the created secrets file.

    Raises:
        RuntimeError: If policy is invalid or file validation fails.
    """
    if target_path is None:
        if os.path.isdir("/app"):
            target_path = DEFAULT_SECRETS_PATH
        else:
            target_path = os.path.join(".streamlit", "secrets.toml")

    target_dir = os.path.dirname(target_path)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    policy = load_authorization_policy(policy_json=policy_json, is_cloud_run=is_cloud_run)
    client_id = os.getenv("OIDC_CLIENT_ID", "")
    client_secret = os.getenv("OIDC_CLIENT_SECRET", "")
    cookie_secret = os.getenv("OIDC_COOKIE_SECRET", "")
    redirect_uri = os.getenv("OIDC_REDIRECT_URI", "")

    try:
        admin_users: list[str] = json.loads(os.getenv("ADMIN_USERS_JSON", "[]"))
    except json.JSONDecodeError:
        admin_users = []

    toml_content = serialize_secrets_toml(
        policy=policy,
        client_id=client_id,
        client_secret=client_secret,
        cookie_secret=cookie_secret,
        redirect_uri=redirect_uri,
        admin_users=admin_users,
    )

    # Validate before writing
    try:
        validate_secrets_toml(toml_content)
    except ValueError as exc:
        raise RuntimeError(f"Pre-write secrets validation failed: {exc}") from exc

    # Write file securely with mode 0600 (owner read/write only)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = stat.S_IRUSR | stat.S_IWUSR  # 0o600
    fd = os.open(target_path, flags, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(toml_content)
    except Exception:
        raise

    # Ensure permissions are strictly 0600 regardless of umask
    try:
        os.chmod(target_path, 0o600)
    except OSError as exc:
        logger.warning("Could not set strict 0600 permissions on %s: %s", target_path, exc)

    # Verify on-disk file can be read and parsed by tomllib
    with open(target_path, "rb") as f:
        disk_content = f.read().decode("utf-8")
    validate_secrets_toml(disk_content)

    print(f"Dynamically generated and validated {target_path} (mode 0600) from environment variables.")
    return target_path


if __name__ == "__main__":
    generate_secrets_file()
