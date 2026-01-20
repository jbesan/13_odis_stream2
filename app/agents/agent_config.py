# Centralized Model Configuration for ODIS Agents
# Users can modify these values to switch models for specific agents.

# Defaults
DEFAULT_MODEL = "google-gla:gemini-2.5-flash-lite"

# Agent Specific assignments
MODELS = {
    "router": "google-gla:gemini-3-flash-preview",      # Reasoning + JSON
    "interviewer": "google-gla:gemini-3-flash-preview", # High Context / Conversational
    "scorer": "google-gla:gemini-2.5-flash-lite",      # Run ScoringEngine + Synthesis
    "scout": "google-gla:gemini-2.5-flash-lite",       # Fast lookup
    "web": "google-gla:gemini-2.5-flash-lite",         # Search grounding
    "job_hunter": "google-gla:gemini-2.5-flash-lite",  # API interaction
    "synthesizer": "google-gla:gemini-3-flash-preview", # Context fusion
    "refiner": "google-gla:gemini-2.5-flash-lite"      # Briefing summary
}

def get_model(agent_name: str) -> str:
    """Safely get model for an agent, fallback to DEFAULT."""
    return MODELS.get(agent_name, DEFAULT_MODEL)
