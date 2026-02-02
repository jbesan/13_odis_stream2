import os
from google import genai
from dotenv import load_dotenv
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider


# Ensure environment is loaded
# base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# env_path = os.path.join(base_dir, ".env")
# load_dotenv(env_path)


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


def get_p_model(agent_name: str, client: genai.Client) -> GoogleModel:
    
    mod_id = get_model(agent_name)
    if ":" in mod_id:
        _, model_name = mod_id.split(":", 1)
    else:
        model_name = mod_id
    
    api_key = os.getenv("GOOGLE_API_KEY")
    
    # Explicitly inject the fresh client (configured with retries)
    provider = GoogleProvider(client=client)
    
    # Increase max_tokens for complex outputs (Refiner/Synthesizer)
    return GoogleModel(
        model_name, 
        provider=provider
    )