import pytest

from utils.oidc_policy import (
    OIDCAuthorizationPolicyError,
    load_runtime_oidc_authorization_policy,
    parse_oidc_authorization_policy,
)


def test_parse_oidc_authorization_policy_normalizes_identity_keys():
    policy = parse_oidc_authorization_policy(
        """
        {
          "allowed_domains": {" LAHSO.ORG. ": "emile_aura"},
          "allowed_emails": {" Partner@Example.ORG ": "agir33"}
        }
        """
    )

    assert policy.allowed_domains == {"lahso.org": "emile_aura"}
    assert policy.allowed_emails == {"partner@example.org": "agir33"}


@pytest.mark.parametrize(
    "raw_policy",
    [
        "not-json",
        "[]",
        '{"allowed_domains": {}}',
        '{"allowed_domains": {}, "allowed_emails": {}, "extra": {}}',
        '{"allowed_domains": {}, "allowed_emails": {}}',
        '{"allowed_domains": {"bad domain": "jaccueille"}, "allowed_emails": {}}',
        '{"allowed_domains": {}, "allowed_emails": {"no-at-sign": "jaccueille"}}',
        '{"allowed_domains": {"lahso.org": ""}, "allowed_emails": {}}',
    ],
)
def test_parse_oidc_authorization_policy_rejects_invalid_contract(raw_policy):
    with pytest.raises(OIDCAuthorizationPolicyError):
        parse_oidc_authorization_policy(raw_policy)


def test_runtime_policy_is_empty_only_outside_cloud_run():
    assert (
        load_runtime_oidc_authorization_policy(None, is_cloud_run=False).allowed_domains
        == {}
    )

    with pytest.raises(OIDCAuthorizationPolicyError, match="must be configured"):
        load_runtime_oidc_authorization_policy(None, is_cloud_run=True)
