import logging
from typing import List, Dict, Any, Optional
from .base import BaseAgent
from .state import AgentContext
from google.genai import types
from models import SearchCriterias
from .tools import compute_top_cities

logger = logging.getLogger("scorer_agent")

SCORER_PROMPT = """
**Rôle** : Tu es le Scorer ODIS. Ton job est de calculer et expliquer le Top Villes.
**PROFILE** : {PROFILE}
**CRITÈRES VALIDÉS** : 
```json
{CRITERIA_JSON}
```

**DIRECTIVE CRITIQUE** :
Tu DOIS utiliser l'outil `compute_top_cities`.
Pour l'argument `filters`, tu DOIS COPIER EXACTEMENT le JSON ci-dessus.

INTERDIT d'inventer des valeurs ou de modifier le JSON. Si un champ est null, laisse-le null.

**Instructions** :
1. Lance `compute_top_cities` avec les arguments stricts.
2. Une fois les résultats reçus, présente le Top 3 des meilleures communes.
3. Pour chaque ville du Top 3:
    - Donne son nom et son score global.
    - Cite 1 ou 2 points forts pertinents par rapport au profil (Famille, Emploi, etc.).
4. Termine TOUJOURS en suggérant à l'utilisateur de lancer une recherche approfondie sur l'une des 3 communes.
"""

class ScorerAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        # 1. Validation des critères via Pydantic
        try:
            # On essaie de construire le modèle pour valider et nettoyer
            criteria_model = SearchCriterias(**context.search_criteria)
            # On utilise le dump json pour le prompt pour être propre
            criteria_json = criteria_model.model_dump_json(indent=2)
            profile = criteria_model.weight_profile or "Équilibré"
        except Exception as e:
            logger.error(f"Scorer validation error: {e}")
            return f"Je ne peux pas encore lancer le calcul, il manque des informations dans vos critères ({e})."

        # 2. Construction du prompt
        prompt = SCORER_PROMPT.replace("{CRITERIA_JSON}", criteria_json)
        prompt = prompt.replace("{PROFILE}", profile)
        
        # Prepare Prompt-based Memory (Short)
        history_summary = ""
        if context.history:
            for turn in context.history[-3:]:
                role = "Utilisateur" if turn.get("role") == "user" else "Assistant"
                parts = turn.get("parts", [])
                text_parts = [p.get("text") for p in parts if isinstance(p, dict) and p.get("text")]
                text = " ".join(text_parts) if text_parts else ""
                history_summary += f"- {role}: {text}\n"

        if "{HISTORY_SUMMARY}" in prompt:
             prompt = prompt.replace("{HISTORY_SUMMARY}", history_summary)
        else:
             prompt += f"\n\n**Historique Recent** :\n{history_summary}"

        try:
            return self._execute_tool_loop(prompt, message, [compute_top_cities])
        except Exception as e:
            logger.error(f"Scorer error: {e}")
            return "Une erreur est survenue lors du calcul."
