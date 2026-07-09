import os
from typing import Literal, Any
from pydantic_ai import Agent
from google import genai
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_ai.models import ModelSettings
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google_cloud import GoogleCloudProvider


# --- Configuration Models ---


class NodeConfig(BaseModel):
    """Configuration for a specific LLM agent node.

    Attributes:
        model: Pydantic AI model identifier.
        temperature: LLM temperature (0.0 to 1.0).
        max_tokens: Maximum output tokens.
        thinking: Optional thinking/reasoning effort level (Gemini 3+).
    """

    model: str = "google:gemini-3.1-flash-lite-preview"
    temperature: float = 0.0
    max_tokens: int | None = None
    thinking: Literal["minimal", "low", "medium", "high"] | None = None
    timeout: float | None = 60.0

    @property
    def model_settings(self) -> ModelSettings:
        """Returns the Pydantic AI ModelSettings for this node."""
        return ModelSettings(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            thinking=self.thinking,
            timeout=self.timeout,
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

    router: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.1, thinking="low"
        )
    )
    interviewer: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.5, thinking="medium"
        )
    )
    ts_agent: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.1, thinking="low"
        )
    )
    housing_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.3, thinking="low"
        )
    )
    mobility_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.3, thinking="low"
        )
    )
    healthcare_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.3, thinking="low"
        )
    )
    education_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.3, thinking="low"
        )
    )
    social_integration_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.3, thinking="low"
        )
    )
    job_hunter: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.3, thinking="low"
        )
    )
    job_curator: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.1, thinking="low"
        )
    )
    synthesizer: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite", temperature=0.1, thinking="medium"
        )
    )  # , max_tokens=8192))
    refiner: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            model="google:gemini-3.1-flash-lite",
            temperature=0.1,
            thinking="minimal",
            max_tokens=4096,
        )
    )

    # Vertex AI configuration
    gcp_project: str | None = Field(default=None)
    gcp_location: str = Field(default="eu")

    def get_config(self, agent_name: str) -> NodeConfig:
        """Helper to get config by agent name, falling back to router if unknown."""
        return getattr(self, agent_name, self.router)


# Singleton instance
agent_settings = AgentSettings()


# --- Legacy Compatibility & Helpers ---

DEFAULT_MODEL = "google:gemini-3.1-flash-lite"


def get_model(agent_name: str) -> str:
    """Returns the model string for a given agent."""
    return agent_settings.get_config(agent_name).model


def get_model_settings(agent_name: str) -> ModelSettings:
    """Returns the ModelSettings for a given agent."""
    return agent_settings.get_config(agent_name).model_settings


def get_p_model(agent_name: str, client: genai.Client | None = None) -> GoogleModel:
    """Returns a configured GoogleModel instance for Pydantic AI."""
    mod_id = get_model(agent_name)

    if ":" in mod_id:
        _, model_name = mod_id.split(":", 1)
    else:
        model_name = mod_id

    project = (
        agent_settings.gcp_project
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or "odis-stream2"
    )
    location = agent_settings.gcp_location

    if client is not None:
        provider = GoogleCloudProvider(client=client)
    else:
        base_url = None
        if location == "eu":
            base_url = "https://aiplatform.eu.rep.googleapis.com"
        provider = GoogleCloudProvider(
            project=project, location=location, base_url=base_url
        )

    profile = None
    try:
        default_profile = provider.model_profile(model_name)
        if default_profile:
            profile = {
                **default_profile,
                "google_supports_server_side_tool_invocations": False,
            }
    except Exception:
        pass

    return GoogleModel(
        model_name,
        provider=provider,
        profile=profile,
    )


def get_gemini_client(attempts: int = 3, location: str | None = None) -> genai.Client:
    """Returns a configured Google GenAI client based on settings.

    Uses Vertex AI on the configured location unconditionally.
    """
    import os
    from google import genai
    from google.genai import types

    project = (
        agent_settings.gcp_project
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or "odis-stream2"
    )
    loc = location or agent_settings.gcp_location or "eu"

    # For Vertex AI in 'eu' multi-region, we must specify the correct endpoint URL
    base_url = None
    if loc == "eu":
        base_url = "https://aiplatform.eu.rep.googleapis.com"

    # Retry and HTTP options
    retry_opts = types.HttpRetryOptions(
        attempts=attempts,
        initial_delay=1.0,
        max_delay=10.0,
        http_status_codes=[429, 503],
    )
    http_opts = types.HttpOptions(retry_options=retry_opts, base_url=base_url)

    return genai.Client(
        vertexai=True, project=project, location=loc, http_options=http_opts
    )


def get_swarm_boilerplate(
    agent_type: Literal["expert", "coordinator", "synthesizer", "job_curator"],
) -> str:
    """Returns the standardized swarm collaboration boilerplate context prompt."""
    if agent_type == "expert":
        return (
            "**Contexte de collaboration (Swarm d'agents IA)** :\n"
            "- Tu es un expert thématique faisant partie d'un swarm d'agents IA. La demande provient de l'agent coordinateur.\n"
            "- L'utilisateur final est un **Travailleur Social humain** qui accompagne un bénéficiaire (généralement une personne réfugiée et sa famille) dans sa relocalisation.\n"
            "- Formule tes analyses à partir de tes recherches qualitatives en plus des données quantitatives du dossier JSON joint.\n"
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
    elif agent_type == "job_curator":
        return (
            "**Contexte de collaboration (CIP / Insertion)** :\n"
            "- Tu es un CIP (Conseiller en Insertion Professionnelle) expert en insertion professionnelle de réfugiés.\n"
            "- Sélectionne de manière factuelle et rigoureuse les 5 meilleures offres d'emploi pour aider le Travailleur Social humain.\n"
        )
    return ""


def create_agent(agent_name: str, **kwargs: Any) -> Agent[Any, Any]:
    """Centralized factory to instantiate an Agent with configured models,
    settings, trace naming, and deferred model checks for production.
    """
    model = get_model(agent_name)
    model_settings = get_model_settings(agent_name)

    # Enforce standard defaults
    kwargs.setdefault("name", agent_name)            # Ensures proper Logfire trace names
    kwargs.setdefault("defer_model_check", True)      # Bypasses Cloud Run startup API key errors

    return Agent(
        model=model,
        model_settings=model_settings,
        **kwargs
    )

