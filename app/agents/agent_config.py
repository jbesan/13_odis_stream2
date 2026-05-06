import os
from typing import Literal
from google import genai
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_ai.models import ModelSettings
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider


# --- Configuration Models ---

class NodeConfig(BaseModel):
    """Configuration for a specific LLM agent node.

    Attributes:
        model: Pydantic AI model identifier.
        temperature: LLM temperature (0.0 to 1.0).
        max_tokens: Maximum output tokens.
        thinking: Optional thinking/reasoning effort level (Gemini 3+).
    """
    model: str = "google-gla:gemini-3.1-flash-lite-preview"
    temperature: float = 0.0
    max_tokens: int | None = None
    thinking: Literal["minimal", "low", "medium", "high"] | None = None

    @property
    def model_settings(self) -> ModelSettings:
        """Returns the Pydantic AI ModelSettings for this node."""
        return ModelSettings(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            thinking=self.thinking,
        )


class AgentSettings(BaseSettings):
    """Centralized Agent configuration with environment variable overrides.
    
    Settings can be overridden using the ODIS_AGENT_ prefix, e.g.:
    ODIS_AGENT_SYNTHESIZER__TEMPERATURE=0.7
    """
    model_config = SettingsConfigDict(
        env_prefix="ODIS_AGENT_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    router: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite-preview"))
    interviewer: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite-preview"))
    scorer: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-2.5-flash-lite"))
    scout: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite-preview"))
    web: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite-preview"))
    job_hunter: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite-preview"))
    synthesizer: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite-preview", max_tokens=8192))
    refiner: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-2.5-flash-lite"))

    def get_config(self, agent_name: str) -> NodeConfig:
        """Helper to get config by agent name, falling back to router if unknown."""
        return getattr(self, agent_name, self.router)


# Singleton instance
agent_settings = AgentSettings()


# --- Legacy Compatibility & Helpers ---

DEFAULT_MODEL = "google-gla:gemini-3.1-flash-lite-preview"


def get_model(agent_name: str) -> str:
    """Returns the model string for a given agent."""
    return agent_settings.get_config(agent_name).model


def get_model_settings(agent_name: str) -> ModelSettings:
    """Returns the ModelSettings for a given agent."""
    return agent_settings.get_config(agent_name).model_settings


def get_p_model(agent_name: str, client: genai.Client) -> GoogleModel:
    """Returns a configured GoogleModel instance for Pydantic AI."""
    config = agent_settings.get_config(agent_name)
    mod_id = config.model
    
    if ":" in mod_id:
        _, model_name = mod_id.split(":", 1)
    else:
        model_name = mod_id
    
    # Explicitly inject the fresh client
    provider = GoogleProvider(client=client)

    return GoogleModel(
        model_name, 
        provider=provider,
    )