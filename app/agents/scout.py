import logging
from typing import List, Dict, Any, Optional
from .base import BaseAgent
from .state import AgentContext
from google.genai import types
from .tools import search_places, compute_routes

logger = logging.getLogger("scout_agent")

SCOUT_PROMPT = """
**Rôle** : Tu es le Scout ODIS. Expert en terrain.
**Critères** : {CRITERIA}

**Instructions** :
1. Utilise `search_places` et `compute_routes`.
2. Réponds précisément aux questions sur la vie quotidienne.
3. Tu DOIS toujours répondre avec du texte.
"""

class ScoutAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        # Prepare Prompt-based Memory
        history_summary = ""
        if context.history:
            for turn in context.history[-10:]:
                role = "Utilisateur" if turn.get("role") == "user" else "Assistant"
                parts = turn.get("parts", [])
                text_parts = [p.get("text") for p in parts if isinstance(p, dict) and p.get("text")]
                text = " ".join(text_parts) if text_parts else ""
                history_summary += f"- {role}: {text}\n"

        prompt = SCOUT_PROMPT.replace("{CRITERIA}", str(context.search_criteria))
        if "{HISTORY_SUMMARY}" in prompt:
             prompt = prompt.replace("{HISTORY_SUMMARY}", history_summary)
        else:
             prompt += f"\n\n**Historique Recent** :\n{history_summary}"

        try:
            return self._execute_tool_loop(prompt, message, [search_places, compute_routes])
        except Exception as e:
            logger.error(f"Scout error: {e}")
            return "Une erreur est survenue lors de la vérification terrain."
