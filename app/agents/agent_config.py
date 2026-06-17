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

    router: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.1, thinking="low"))
    interviewer: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.5, thinking="medium"))
    ts_agent: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.1, thinking="low"))
    housing_expert: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.3, thinking="low"))
    mobility_expert: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.3, thinking="low"))
    healthcare_expert: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.3, thinking="low"))
    education_expert: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.3, thinking="low"))
    social_integration_expert: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.3, thinking="low"))
    job_hunter: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.3, thinking="low"))
    synthesizer: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.1, thinking="medium"))#, max_tokens=8192))
    refiner: NodeConfig = Field(default_factory=lambda: NodeConfig(model="google-gla:gemini-3.1-flash-lite", temperature=0.1, thinking="minimal", max_tokens=4096))

    def get_config(self, agent_name: str) -> NodeConfig:
        """Helper to get config by agent name, falling back to router if unknown."""
        return getattr(self, agent_name, self.router)


# Singleton instance
agent_settings = AgentSettings()


# --- Legacy Compatibility & Helpers ---

DEFAULT_MODEL = "google-gla:gemini-3.1-flash-lite"


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


def get_swarm_boilerplate(agent_type: Literal["expert", "coordinator", "synthesizer"]) -> str:
    """Returns the standardized swarm collaboration boilerplate context prompt."""
    if agent_type == "expert":
        return (
            "**Contexte de collaboration (Swarm d'agents IA)** :\n"
            "- Tu es un expert thématique faisant partie d'un swarm d'agents IA. La demande provient de l'agent coordinateur.\n"
            "- L'utilisateur final est un **Travailleur Social humain** qui accompagne un bénéficiaire (généralement une personne réfugiée et sa famille) dans sa relocalisation.\n"
            "- Formule tes analyses et réponses à partir du dossier JSON pour l'aider dans son accompagnement.\n"
            "- Soit hyper factuel et ajoute toujours une section sur les éléments spécifiques que tu n'as pas pu trouver ou vérifier.\n"
        )
    elif agent_type == "coordinator":
        return (
            "**Contexte de collaboration (Swarm d'agents IA)** :\n"
            "- Tu es le coordinateur d'un swarm d'agents IA thématiques.\n"
            "- L'utilisateur final est un **Travailleur Social humain** qui accompagne un bénéficiaire (généralement une personne réfugiée et sa famille) dans sa relocalisation.\n"
            "- Planifie le travail ou réponds directement à partir du dossier JSON pour l'aider dans son accompagnement.\n"
            
        )
    elif agent_type == "synthesizer":
        return (
            "**Contexte de collaboration (Swarm d'agents IA)** :\n"
            "- Tu es le synthétiseur final d'un swarm d'agents IA thématiques.\n"
            "- L'utilisateur final est un **Travailleur Social humain** qui accompagne un bénéficiaire (généralement une personne réfugiée et sa famille) dans sa relocalisation.\n"
            "- Synthétise les retours des experts et du dossier JSON pour l'aider dans son accompagnement.\n"
            "- Soit hyper factuel et ajoute toujours une section sur les éléments spécifiques que tu n'as pas pu trouver ou vérifier.\n"
        )
    return ""