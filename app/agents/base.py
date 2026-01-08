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

    def _execute_tool_loop(self, prompt: str, message: str, tools: list, context: Optional[AgentContext] = None, include_google_search: bool = False) -> str:
        """
        Exécute l'agent en mode 'Stateless Single-Turn'.
        On n'envoie JAMAIS d'historique au SDK pour éviter les erreurs de validation.
        Tout le contexte est dans 'prompt'.
        """
        merged_tools = []
        if tools:
            # On passe les fonctions directement pour que le SDK les gère
            merged_tools.extend(tools)
        
        if include_google_search:
            merged_tools.append(types.Tool(google_search=types.GoogleSearch()))

        config = types.GenerateContentConfig(
            system_instruction=prompt + "\n\nIMPORTANT: Tu DOIS répondre en FRANÇAIS. Tu DOIS toujours produire du texte explicatif à la fin.",
            tools=merged_tools,
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
            # logger.info(f"🤖 [BASE_AGENT] Calling LLM ({self.model_id})...")
            # APPEL UNIQUE - Pas de variable 'contents' avec historique
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=message, # Juste le dernier message
                config=config
            )
            
            # Track Tokens
            if response.usage_metadata:
                in_tokens = response.usage_metadata.prompt_token_count or 0
                out_tokens = response.usage_metadata.candidates_token_count or 0
                
                # Global totals
                context.total_tokens_sent += in_tokens
                context.total_tokens_received += out_tokens
                
                # Model-specific tracking
                if "gemini-3" in self.model_id:
                    context.tokens_g3_input += in_tokens
                    context.tokens_g3_output += out_tokens
                elif "gemini-2.5" in self.model_id:
                    context.tokens_g25_input += in_tokens
                    context.tokens_g25_output += out_tokens
                
                logger.info(f"📊 [BASE_AGENT] {self.model_id} Usage: +{in_tokens} in / +{out_tokens} out")

            if response.text:
                # logger.info(f"💾 [BASE_AGENT] LLM Answer: {response.text[:100]}...")
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



