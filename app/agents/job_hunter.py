import logging
from .base import BaseAgent
from .state import AgentContext

logger = logging.getLogger("job_hunter_agent")

class JobHunterAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        # Placeholder response
        metiers = context.search_criteria.get('codes_metiers', [])
        return (
            "💼 **Assistant Emploi (Bêta)**\n\n"
            "Je suis l'expert emploi. Je vois que nous avons identifié les codes métiers suivants : "
            f"{metiers if metiers else 'Aucun pour le moment'}.\n\n"
            "Bientôt, je pourrai interroger les offres d'emploi en temps réel dans votre zone de recherche. "
            "Souhaitez-vous que nous continuions à affiner votre profil de réinstallation en attendant ?"
        )
