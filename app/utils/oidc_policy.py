"""Validation helpers for the runtime OIDC authorization policy.

The policy is security configuration, not an authentication mechanism. Cloud
Run injects its JSON representation from Secret Manager before
``generate_secrets.py`` writes the Streamlit authentication configuration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


class OIDCAuthorizationPolicyError(ValueError):
    """Raised when the OIDC authorization policy is missing or malformed."""


@dataclass(frozen=True)
class OIDCAuthorizationPolicy:
    """Normalized mappings of authorized OIDC identities to organization IDs."""

    allowed_domains: dict[str, str]
    allowed_emails: dict[str, str]


def normalize_email(value: Any) -> str | None:
    """Return a canonical email address, or ``None`` for an invalid value."""
    if not isinstance(value, str):
        return None
    email = value.strip().casefold()
    if email.count("@") != 1:
        return None
    local_part, domain = email.split("@")
    if not local_part or not normalize_domain(domain):
        return None
    return email


def normalize_domain(value: Any) -> str | None:
    """Return a canonical bare domain, or ``None`` for an invalid value."""
    if not isinstance(value, str):
        return None
    domain = value.strip().casefold().rstrip(".")
    if (
        not domain
        or "@" in domain
        or "." not in domain
        or any(char.isspace() for char in domain)
    ):
        return None
    return domain


def _normalize_mapping(
    value: Any, *, key_normalizer: Any, field_name: str
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise OIDCAuthorizationPolicyError(f"{field_name} must be a JSON object")

    normalized: dict[str, str] = {}
    for raw_key, raw_org_id in value.items():
        key = key_normalizer(raw_key)
        org_id = raw_org_id.strip() if isinstance(raw_org_id, str) else ""
        if not key or not org_id:
            raise OIDCAuthorizationPolicyError(
                f"{field_name} must contain non-empty identity and organization values"
            )
        if key in normalized:
            raise OIDCAuthorizationPolicyError(
                f"{field_name} contains duplicate identities after normalization"
            )
        normalized[key] = org_id
    return normalized


def parse_oidc_authorization_policy(raw_policy: str) -> OIDCAuthorizationPolicy:
    """Parse the strict Secret Manager JSON contract used by Cloud Run.

    Expected shape::

        {
          "allowed_domains": {"example.org": "organization_id"},
          "allowed_emails": {"partner@example.net": "organization_id"}
        }

    A policy must authorize at least one domain or individual email. Whether an
    organization ID exists is deliberately checked by the application, where
    ``config.ORGANIZATION_PROFILES`` is authoritative.
    """
    try:
        parsed = json.loads(raw_policy)
    except (TypeError, json.JSONDecodeError) as exc:
        raise OIDCAuthorizationPolicyError(
            "OIDC authorization policy must be valid JSON"
        ) from exc

    if not isinstance(parsed, Mapping):
        raise OIDCAuthorizationPolicyError(
            "OIDC authorization policy must be a JSON object"
        )

    expected_fields = {"allowed_domains", "allowed_emails"}
    if set(parsed) != expected_fields:
        raise OIDCAuthorizationPolicyError(
            "OIDC authorization policy must contain only allowed_domains and allowed_emails"
        )

    allowed_domains = _normalize_mapping(
        parsed["allowed_domains"],
        key_normalizer=normalize_domain,
        field_name="allowed_domains",
    )
    allowed_emails = _normalize_mapping(
        parsed["allowed_emails"],
        key_normalizer=normalize_email,
        field_name="allowed_emails",
    )
    if not allowed_domains and not allowed_emails:
        raise OIDCAuthorizationPolicyError(
            "OIDC authorization policy must authorize at least one identity"
        )
    return OIDCAuthorizationPolicy(
        allowed_domains=allowed_domains,
        allowed_emails=allowed_emails,
    )


def load_runtime_oidc_authorization_policy(
    raw_policy: str | None, *, is_cloud_run: bool
) -> OIDCAuthorizationPolicy:
    """Load the runtime policy, failing closed for a Cloud Run revision."""
    if raw_policy:
        return parse_oidc_authorization_policy(raw_policy)
    if is_cloud_run:
        raise OIDCAuthorizationPolicyError(
            "OIDC_AUTHORIZATION_POLICY_JSON must be configured for Cloud Run"
        )
    return OIDCAuthorizationPolicy(allowed_domains={}, allowed_emails={})
