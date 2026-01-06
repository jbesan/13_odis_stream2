import abc
import logging
from typing import List, Any, Dict, Optional
from .state import AgentContext
from google import genai
from google.genai import types

logger = logging.getLogger("base_agent")

class BaseAgent(abc.ABC):
    def __init__(self, model_id: str, client: genai.Client):
        self.model_id = model_id
        self.client = client
    
    @abc.abstractmethod
    def run(self, message: str, context: AgentContext) -> str:
        """Process a message within the given context and return agent's response."""
        pass

    def _execute_tool_loop(self, prompt: str, message: str, tools: list) -> str:
        """
        Exécute l'agent en mode 'Stateless Single-Turn'.
        On n'envoie JAMAIS d'historique au SDK pour éviter les erreurs de validation.
        Tout le contexte est dans 'prompt'.
        """
        config = types.GenerateContentConfig(
            system_instruction=prompt + "\n\nIMPORTANT: Tu DOIS répondre en FRANÇAIS. Tu DOIS toujours produire du texte explicatif à la fin.",
            tools=tools,
            # On utilise le mode automatique car sans historique, il est stable
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=False),
            temperature=0.3,
            safety_settings=[
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
            ]
        )

        try:
            logger.info(f"🤖 [BASE_AGENT] Calling LLM ({self.model_id})...")
            # APPEL UNIQUE - Pas de variable 'contents' avec historique
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=message, # Juste le dernier message
                config=config
            )
            
            if response.text:
                logger.info(f"💾 [BASE_AGENT] LLM Answer: {response.text[:100]}...")
                return response.text.strip()
            
            # Si le texte est vide, on vérifie si des outils ont été appelés 
            # (normalement géré par le SDK en mode auto)
            logger.info("💾 [BASE_AGENT] LLM returned no text (likely tool call auto-executed).")
            return "J'ai bien reçu votre message. Je continue mes recherches pour vous."

        except Exception as e:
            logger.error(f"❌ [SDK_BYPASS] Error: {e}")
            if "must contain either" in str(e).lower():
                return "Je suis prêt. Pose-moi une question sur une ville ou un métier."
            raise e



