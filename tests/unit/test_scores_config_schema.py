import pytest
import yaml
from pathlib import Path
from pydantic import ValidationError

from app.core.models import ScoresConfigFileSchema


def test_scores_config_yaml_is_valid():
    """Verifies that the project's app/scores_config.yaml conforms strictly to ScoresConfigFileSchema."""
    config_path = Path(__file__).parent.parent.parent / "app" / "scores_config.yaml"
    assert config_path.exists(), f"Config file not found at {config_path}"

    with open(config_path, "r", encoding="utf-8") as f:
        cfg_data = yaml.safe_load(f)

    validated = ScoresConfigFileSchema.model_validate(cfg_data)
    assert len(validated.scores) > 0
    assert len(ScoresConfigFileSchema.get_valid_ids()) == len(validated.scores)


def test_scores_config_schema_rejects_invalid_computation():
    """Verifies that invalid computation mode like 'precomputedde' raises ValidationError."""
    invalid_cfg = {
        "scores": [
            {
                "id": "test_invalid_score",
                "category": "emploi",
                "computation": "precomputedde",
            }
        ]
    }
    with pytest.raises(ValidationError) as exc_info:
        ScoresConfigFileSchema.model_validate(invalid_cfg)
    assert "computation" in str(exc_info.value)


def test_scores_config_schema_rejects_invalid_category():
    """Verifies that invalid category raises ValidationError."""
    invalid_cfg = {
        "scores": [
            {
                "id": "test_invalid_score",
                "category": "unknown_cat",
                "computation": "precomputed",
            }
        ]
    }
    with pytest.raises(ValidationError) as exc_info:
        ScoresConfigFileSchema.model_validate(invalid_cfg)
    assert "category" in str(exc_info.value)


def test_scores_config_schema_rejects_extra_fields():
    """Verifies that unknown extra fields are forbidden."""
    invalid_cfg = {
        "scores": [
            {
                "id": "test_invalid_score",
                "category": "emploi",
                "computation": "precomputed",
                "unknown_extra_field": 123,
            }
        ]
    }
    with pytest.raises(ValidationError) as exc_info:
        ScoresConfigFileSchema.model_validate(invalid_cfg)
    assert "unknown_extra_field" in str(exc_info.value)
