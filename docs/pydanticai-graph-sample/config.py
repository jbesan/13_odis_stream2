"""Application configuration via environment variables.

All settings use the ``SAC_`` prefix and can be overridden via
environment variables or a ``.env`` file.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class NodeConfig(BaseModel):
    """Configuration for a specific LLM node.

    Attributes:
        model: Pydantic AI model identifier. Defaults to gemini-3.1-flash-lite-preview.
        temperature: LLM temperature (0.0 to 1.0).
        max_tokens: Maximum output tokens.
        thinking: Optional thinking/reasoning effort level (Gemini 3+).
    """
    model: str = "google-gla:gemini-3.1-flash-lite-preview"
    temperature: float = 0.0
    max_tokens: int | None = None
    thinking: Literal["minimal", "low", "medium", "high"] | None = None


class Settings(BaseSettings):
    """Social Agent Core configuration.

    Attributes:
        orchestrator: Configuration for the Orchestrator node.
        expert: Default configuration for Expert workers.
        synthesizer: Configuration for the Synthesizer node.
        pm_discovery: Configuration for the PM Discovery node.
        pii_language: Language code for PII masking.
        pii_spacy_model: spaCy model for PII NER.
        environment: Deployment environment.
        log_level: Logging level.
        gemini_api_key: Optional API key for Google AI Studio models.
    """

    model_config = SettingsConfigDict(
        env_prefix="SAC_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Granular Node Configs - default to NodeConfig which inherits from env or class defaults
    orchestrator: NodeConfig = Field(default_factory=NodeConfig)
    expert: NodeConfig = Field(default_factory=NodeConfig)
    synthesizer: NodeConfig = Field(default_factory=NodeConfig)
    pm_discovery: NodeConfig = Field(default_factory=NodeConfig)

    pii_enabled: bool = False
    pii_language: str = "fr"
    pii_spacy_model: str = "fr_core_news_md"
    environment: str = "development"
    log_level: str = "INFO"
    gemini_api_key: str | None = None
    brave_search_api_key: str | None = None
    
    # Legacy Search Settings (Deprecated)
    google_search_cx_api_key: str | None = None
    google_search_cx: str | None = None
    google_maps_grounding_lite_api_key: str | None = None
    knowledge_catalog_dir: str = "catalog"
    embedding_model: str = "models/gemini-embedding-2"
    embedding_model_rna: str = "models/text-multilingual-embedding-002"
    gcp_project_id: str = "ts-buddy"
    bq_dataset_id: str = "social_agent_knowledge"
    emplois_inclusion_login: str | None = None
    emplois_inclusion_pwd: str | None = None
    france_travail_client_id: str | None = None
    france_travail_client_secret: str | None = None

    api_host: str = "0.0.0.0"
    api_port: int = 8000


settings = Settings()
