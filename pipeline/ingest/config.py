"""Configuration loader and validator for pipeline domain contracts."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Dict
import yaml

from pipeline.ingest.schemas import DomainContracts

DEFAULT_DOMAIN_CONTRACTS_PATH = Path("pipeline/domain_contracts.yaml")


def load_domain_contracts_yaml(
    path: Path | str = DEFAULT_DOMAIN_CONTRACTS_PATH,
) -> Dict[str, Any]:
    """Load raw domain contracts dictionary from YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        Dictionary loaded from YAML.

    Raises:
        FileNotFoundError: If the contract file does not exist.
        yaml.YAMLError: If YAML syntax is invalid.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Domain contracts file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"Domain contracts YAML root must be a mapping, got {type(data)}"
        )
    return data


@functools.lru_cache(maxsize=1)
def get_domain_contracts(
    path: Path | str = DEFAULT_DOMAIN_CONTRACTS_PATH,
) -> DomainContracts:
    """Load and validate the domain contracts using Pydantic.

    Results are cached in memory for zero-overhead repeated access.

    Args:
        path: Path to the domain contracts YAML file.

    Returns:
        Validated DomainContracts model.
    """
    raw_data = load_domain_contracts_yaml(path)
    return DomainContracts.model_validate(raw_data)


def clear_domain_contracts_cache() -> None:
    """Clear the cached domain contracts (useful in tests)."""
    get_domain_contracts.cache_clear()
