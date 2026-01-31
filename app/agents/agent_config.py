# Defaults
DEFAULT_MODEL = "google-gla:gemini-2.5-flash-lite"

MODELS = {
    "router": "google-gla:gemini-3-flash-preview",
    "interviewer": "google-gla:gemini-3-flash-preview",
    "scorer": "google-gla:gemini-2.5-flash-lite",
    "scout": "google-gla:gemini-2.5-flash-lite",
    "web": "google-gla:gemini-2.5-flash-lite",
    "job_hunter": "google-gla:gemini-2.5-flash-lite",
    "synthesizer": "google-gla:gemini-3-flash-preview",
    "refiner": "google-gla:gemini-2.5-flash-lite"
}


def get_model(agent_name: str) -> str:
    return MODELS.get(
        agent_name,
        DEFAULT_MODEL
        )
