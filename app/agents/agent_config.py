import logging
import os
from typing import Literal, Any
from pydantic_ai import Agent
from google import genai
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_ai.models import ModelSettings
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google_cloud import GoogleCloudProvider

from agents.google_model import GroundingGoogleModel

logger = logging.getLogger(__name__)


# --- Configuration Models ---


class NodeConfig(BaseModel):
    """Configuration for a specific LLM agent node.

    Attributes:
        model: Optional Pydantic AI model identifier override. If None, uses AgentSettings.default_model.
        temperature: LLM temperature (0.0 to 1.0).
        max_tokens: Maximum output tokens.
        thinking: Optional thinking/reasoning effort level (Gemini 3+) or False to disable.
        timeout: Request timeout in seconds.
    """

    model: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    thinking: Literal["minimal", "low", "medium", "high"] | bool | None = False
    timeout: float | None = 30.0

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
    ODIS_AGENT_DEFAULT_MODEL=google:gemini-3.5-flash-lite
    ODIS_AGENT_SYNTHESIZER__TEMPERATURE=0.7
    """

    model_config = SettingsConfigDict(
        env_prefix="ODIS_AGENT_",
        env_file=(".env", "app/.env"),
        env_nested_delimiter="__",
        extra="ignore",
    )

    default_model: str = Field(default="google:gemini-3.1-flash-lite")

    router: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.0, thinking=False)
    )
    interviewer: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.7, thinking="medium")
    )
    ts_agent: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.0, thinking=False)
    )
    housing_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.0, thinking=False)
    )
    mobility_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.0, thinking=False)
    )
    healthcare_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.0, thinking=False)
    )
    education_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.0, thinking=False)
    )
    social_integration_expert: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.0, thinking=False)
    )
    job_hunter: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.0, thinking=False)
    )
    job_curator: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.0, thinking=False)
    )
    synthesizer: NodeConfig = Field(
        default_factory=lambda: NodeConfig(temperature=0.7, thinking="low")
    )
    refiner: NodeConfig = Field(
        default_factory=lambda: NodeConfig(
            temperature=0.0,
            thinking=False,
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

DEFAULT_MODEL = agent_settings.default_model


def get_gcp_project() -> str:
    """Resolve the Vertex project without retaining a source-project fallback."""
    project = (
        agent_settings.gcp_project
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("ODIS_DATA_PROJECT")
    )
    if not project:
        raise RuntimeError(
            "ODIS_AGENT_GCP_PROJECT, GOOGLE_CLOUD_PROJECT or ODIS_DATA_PROJECT "
            "must be configured for Vertex AI"
        )
    return project


def get_model(agent_name: str) -> str:
    """Returns the model string for a given agent, falling back to default_model."""
    cfg = agent_settings.get_config(agent_name)
    return cfg.model or agent_settings.default_model


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

    project = get_gcp_project()
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
    except Exception as exc:
        logger.debug("Could not resolve model profile for '%s': %s", model_name, exc)

    return GroundingGoogleModel(
        model_name,
        provider=provider,
        profile=profile,
    )


def get_gemini_client(attempts: int = 3, location: str | None = None) -> genai.Client:
    """Returns a configured Google GenAI client based on settings.

    Uses Vertex AI on the configured location unconditionally.
    """
    from google import genai
    from google.genai import types

    project = get_gcp_project()
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
            "- Formule tes analyses à partir de tes recherches qualitatives en plus des données quantitatives du dossier joint.\n"
            "- Sois hyper factuel. Si des éléments essentiels sont manquants ou non vérifiables, formalise-les explicitement sous une section titrée '#### ⚠️ Éléments non vérifiés / manquants' (et non comme une simple note de bas de page).\n"
            "**Instructions opérationnelles**:\n"
            "- Ne recherche jamais une deuxième fois des éléments déjà à ta disposition.\n"
            "- Priorisation des outils : N'utilise `search_web_batch_tool` que lorsque les autres outils n'ont rien donné ou ne sont pas pertinents sur un point essentiel.\n"
            "- `search_places_batch_tool` (recherche de lieux et équipements locaux Google Places) doit être utilisé avec grande parcimonie : limite-toi à un seul appel batch par mission regroupant au maximum 3 à 5 requêtes ciblées indispensables (ex: 2 ou 3 structures clés). Ne cherche jamais via Places ce qui figure déjà dans le dossier ou les référentiels.\n"
            "- Pour un outil donné, regroupe toutes les recherches indépendantes dans un seul appel batch.\n"
            "- Si plusieurs outils sont indépendants, appelle-les dans la même réponse, sans attendre le premier résultat.\n"
            "- `search_web_batch_tool` est limité à un seul appel par mission : donne-lui une liste de besoins indépendants (termes clés, question et lieu si nécessaire), jamais une recherche à la fois.\n"
            "- Budget limité : tu disposes d'au plus 5 requêtes au modèle pour cette mission, appels de suivi compris. Ce budget concerne les tours du modèle, pas le nombre de recherches regroupées dans un batch : planifie dès le premier tour et garde un tour pour la réponse finale.\n"
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
            "- Synthétise les retours des experts pour éclairer la décision du travailleur social.\n"
            "- Sois hyper factuel et identifie clairement les points d'arbitrage et les vigilances.\n"
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
    kwargs.setdefault("name", agent_name)  # Ensures proper Logfire trace names
    kwargs.setdefault(
        "defer_model_check", True
    )  # Bypasses Cloud Run startup API key errors

    return Agent(model=model, model_settings=model_settings, **kwargs)
