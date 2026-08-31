"""CLI utility to read [organizations] from secrets.toml and generate the ORGANIZATIONS_CONFIG_JSON payload for GCP Secret Manager.

Usage:
    python3 scripts/generate_orgs_json.py
    python3 scripts/generate_orgs_json.py --path app/.streamlit/secrets.toml
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from typing import Any, Dict

# Add app/ directory to sys.path to allow model imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.models import Org  # noqa: E402


def find_secrets_path(custom_path: str | None = None) -> str | None:
    """Find the active secrets.toml file."""
    if custom_path and os.path.exists(custom_path):
        return custom_path
    candidates = [
        os.path.join(APP_DIR, ".streamlit", "secrets.toml"),
        os.path.join(PROJECT_ROOT, ".streamlit", "secrets.toml"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def extract_organizations_from_toml(file_path: str) -> Dict[str, Dict[str, Any]]:
    """Parse secrets.toml and extract validated organization configurations."""
    with open(file_path, "rb") as f:
        data = tomllib.load(f)

    raw_orgs = data.get("organizations", {})
    if not isinstance(raw_orgs, dict) or not raw_orgs:
        raise ValueError(f"No [organizations] section found in {file_path}")

    validated_orgs: Dict[str, Dict[str, Any]] = {}
    for org_id, org_data in raw_orgs.items():
        if isinstance(org_data, dict):
            entry = dict(org_data)
            entry.setdefault("id", org_id)
            # Validate with Pydantic Org model to catch any schema errors
            org_obj = Org(**entry)
            # Convert back to clean dictionary representation
            validated_orgs[org_id] = org_obj.model_dump(exclude_unset=False, exclude_none=True)

    return validated_orgs


def main() -> None:
    """Read secrets.toml, validate, and print the Secret Manager JSON payload."""
    parser = argparse.ArgumentParser(
        description="Convert [organizations] from secrets.toml to GCP Secret Manager JSON."
    )
    parser.add_argument(
        "--path",
        "-p",
        help="Path to secrets.toml (defaults to app/.streamlit/secrets.toml)",
        default=None,
    )
    args = parser.parse_args()

    secrets_path = find_secrets_path(args.path)
    if not secrets_path:
        print(
            "❌ Error: Could not find .streamlit/secrets.toml.\n"
            "Please create it or specify with --path.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        orgs = extract_organizations_from_toml(secrets_path)
    except Exception as exc:
        print(f"❌ Error reading organizations from {secrets_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ Successfully loaded and validated {len(orgs)} organizations from {secrets_path}\n")

    payload = json.dumps(orgs, indent=2, ensure_ascii=False)
    compact_payload = json.dumps(orgs, ensure_ascii=False)

    print("=" * 80)
    print("ORGANIZATIONS_CONFIG_JSON (Formatted for review):")
    print("=" * 80)
    print(payload)
    print("=" * 80)
    print("ORGANIZATIONS_CONFIG_JSON (Compact for GCP Secret Manager):")
    print("=" * 80)
    print(compact_payload)
    print("=" * 80)


if __name__ == "__main__":
    main()
