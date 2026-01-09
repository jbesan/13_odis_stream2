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

    def _get_briefing_and_user_msg(self, message: str) -> tuple[str, str]:
        """Extracts briefing data and the actual user message from the input."""
        briefing_data = ""
        user_msg = message
        if "### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)" in message:
            parts = message.split("---")
            if len(parts) >= 3:
                briefing_data = parts[1].strip()
                user_msg = "---".join(parts[2:]).strip()
        return briefing_data, user_msg

    def _execute_tool_loop(self, prompt: str, message: str, tools: list, context: Optional[AgentContext] = None, include_google_search: bool = False, response_json_schema: Optional[Any] = None, response_mime_type: Optional[str] = None) -> str:
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
            response_json_schema=response_json_schema,
            response_mime_type=response_mime_type,
            safety_settings=[
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            ]
        )

        try:
            # Simple direct chat with tools
            chat = self.client.chats.create(
                model=self.model_id,
                config=config
            )
            
            response = chat.send_message(message)
            
            # Track Tokens
            if response.usage_metadata:
                in_tokens = response.usage_metadata.prompt_token_count or 0
                out_tokens = response.usage_metadata.candidates_token_count or 0
                if context:
                    context.total_tokens_sent += in_tokens
                    context.total_tokens_received += out_tokens
                    if "gemini-3" in self.model_id:
                        context.tokens_g3_input += in_tokens
                        context.tokens_g3_output += out_tokens
                    elif "gemini-2.5" in self.model_id:
                        context.tokens_g25_input += in_tokens
                        context.tokens_g25_output += out_tokens
                
                logger.debug(f"🏁 [BASE_AGENT] {self.model_id} Usage: +{in_tokens} in / +{out_tokens} out") 

            if response.text:
                return response.text.strip()
            
            # If no text returned, it's either tools called (but AFC should handle it) or safety/empty
            logger.warning(f"⚠️ [BASE_AGENT] No text in response. Finish reason: {response.candidates[0].finish_reason if response.candidates else 'UNKNOWN'}")
            return "Désolé, je n'ai pas pu générer de synthèse pour cette demande."

        except Exception as e:
            logger.error(f"❌ [BASE_AGENT] Error: {e}")
            if "must contain either" in str(e).lower():
                 return "Erreur technique (Réponse vide du modèle). Veuillez réessayer ou être plus précis."
            raise e



