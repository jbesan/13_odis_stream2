from typing import Any, Dict
from pydantic_ai import Agent

# Humoristic messages for the ODIS agents
AGENT_TOASTS = {
    "interviewer": {
        "emoji": "💬",
        "messages": [
            "Interrogatoire poli en cours.",
            "Je prépare mes meilleures questions pièges.",
            "Discussion mondaine avec l'IA.",
            "À l'écoute de chaque pixel de votre demande.",
            "Le détective ODIS mène l'enquête."
        ]
    },
    "scorer": {
        "emoji": "📈",
        "messages": [
            "Sortez les calculatrices, ça va chauffer !",
            "Tri sélectif des meilleures opportunités.",
            "Le jury a délibéré... Calcul des scores.",
            "Je cherche la perle rare sur la carte.",
            "Alchimie urbaine : transformer les données en pépites."
        ]
    },
    "scout": {
        "emoji": "🏘️",
        "messages": [
            "Exploration du quartier en baskets virtuelles.",
            "Je vérifie si la boulangerie est ouverte.",
            "Repérage terrain. GPS activé.",
            "Je fouille les recoins de chaque commune.",
            "Mission de reconnaissance lancée !"
        ]
    },
    "web": {
        "emoji": "🌐",
        "messages": [
            "Plongeon dans les abysses d'Internet.",
            "Google est mon meilleur ami (pour le moment).",
            "Surf sur la vague de l'information.",
            "Je rapporte des nouvelles fraîches du Web.",
            "Connexion au grand cerveau mondial."
        ]
    },
    "job_hunter": {
        "emoji": "💼",
        "messages": [
            "Chasseur de jobs : Mode furtif activé.",
            "Je déniche des offres avant qu'elles ne refroidissent.",
            "Pêche au gros dans le bassin de l'emploi.",
            "Tri des CV et des annonces... C'est du sérieux.",
            "Le recruteur de choc est sur le coup !"
        ]
    },
    "synthesizer": {
        "emoji": "🧩",
        "messages": [
            "Assemblage des pièces du puzzle.",
            "La cerise sur le gâteau ODIS.",
            "Grand mélange final... Agitez bien.",
            "Dernière vérification avant le décollage.",
            "Je mets de l'ordre dans tout ce bazar."
        ]
    }
}
