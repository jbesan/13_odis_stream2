import os
from unittest.mock import patch
from app.agents.agent_config import (
    AgentSettings,
    NodeConfig,
    get_model,
    get_model_settings,
)


def test_agent_settings_default_model():
    """Verify that all nodes default to AgentSettings.default_model when model is None."""
    settings = AgentSettings(default_model="google:gemini-3.1-flash-lite")

    agent_names = [
        "router",
        "interviewer",
        "ts_agent",
        "housing_expert",
        "mobility_expert",
        "healthcare_expert",
        "education_expert",
        "social_integration_expert",
        "job_hunter",
        "job_curator",
        "synthesizer",
        "refiner",
    ]
    for name in agent_names:
        cfg = settings.get_config(name)
        assert cfg.model is None
        # Effective model should resolve to default_model
        resolved_model = cfg.model or settings.default_model
        assert resolved_model == "google:gemini-3.1-flash-lite"


def test_toggle_default_model_to_3_5():
    """Verify that changing default_model affects all agents without custom model overrides."""
    settings = AgentSettings(default_model="google:gemini-3.5-flash-lite")

    assert settings.default_model == "google:gemini-3.5-flash-lite"
    for name in ["router", "ts_agent", "synthesizer", "refiner"]:
        cfg = settings.get_config(name)
        resolved_model = cfg.model or settings.default_model
        assert resolved_model == "google:gemini-3.5-flash-lite"


def test_per_agent_model_override():
    """Verify that specific agent model override takes precedence over default_model."""
    settings = AgentSettings(
        default_model="google:gemini-3.5-flash-lite",
        synthesizer=NodeConfig(model="google:gemini-2.5-pro", thinking="high"),
    )

    # Synthesizer has custom model
    synth_cfg = settings.get_config("synthesizer")
    assert synth_cfg.model == "google:gemini-2.5-pro"
    assert (synth_cfg.model or settings.default_model) == "google:gemini-2.5-pro"
    assert synth_cfg.thinking == "high"

    # Other agents still use default_model
    ts_cfg = settings.get_config("ts_agent")
    assert ts_cfg.model is None
    assert (ts_cfg.model or settings.default_model) == "google:gemini-3.5-flash-lite"


def test_thinking_levels_preserved():
    """Verify that each agent retains its distinct thinking level and temperature."""
    settings = AgentSettings()

    assert settings.router.thinking is False
    assert settings.router.temperature == 0.0

    assert settings.interviewer.thinking == "medium"
    assert settings.interviewer.temperature == 0.7

    assert settings.ts_agent.thinking is False
    assert settings.ts_agent.temperature == 0.0

    assert settings.synthesizer.thinking == "low"
    assert settings.synthesizer.temperature == 0.7

    assert settings.refiner.thinking is False
    assert settings.refiner.temperature == 0.0
    assert settings.refiner.max_tokens == 4096


def test_get_model_and_settings_helper():
    """Verify get_model and get_model_settings module helper functions."""
    with patch(
        "app.agents.agent_config.agent_settings",
        AgentSettings(default_model="google:gemini-3.5-flash-lite"),
    ):
        assert get_model("router") == "google:gemini-3.5-flash-lite"
        assert get_model("ts_agent") == "google:gemini-3.5-flash-lite"

        ms = get_model_settings("synthesizer")
        assert ms.get("thinking") == "low"
        assert ms.get("temperature") == 0.7


def test_env_var_default_model_override():
    """Verify ODIS_AGENT_DEFAULT_MODEL environment variable override."""
    with patch.dict(
        os.environ, {"ODIS_AGENT_DEFAULT_MODEL": "google:gemini-3.5-flash-lite"}
    ):
        env_settings = AgentSettings()
        assert env_settings.default_model == "google:gemini-3.5-flash-lite"
        assert (
            env_settings.router.model or env_settings.default_model
        ) == "google:gemini-3.5-flash-lite"
