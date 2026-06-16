import logging
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings

logger = logging.getLogger("synthesizer_agent")

# --- Synthesis Logic ---

SYNTH_SYSTEM_PROMPT_ANALYSIS = """
**Contexte** : Un Travailleur Social cherche la ville la plus adéquate pour une personne réfugiée (et sa famille). A partir de critères de recherches, l'outil identifie un Top 5 que l'on analyse et compare à la commune actuelle de la personne accompagnée.
**Rôle** : Tu es le Synthétiseur ODIS. Ta mission est de fusionner les retours du refiner et des experts pour donner une réponse au travailleur social qui accompagne la ou les personnes réfugiées.

# Données de contexte :
```json
{DATA_CONTEXT}
```

Tu trouveras dans ces données de contexte, sous la clé `"Analyses experts"`, les contributions détaillées des experts thématiques :
- `housing_expert` (Logement, loyers, hébergement temporaire)
- `mobility_expert` (Transports locaux, abonnements, trajets)
- `healthcare_expert` (Besoins de santé, PMI, hôpitaux, médecins)
- `education_expert` (Crèches, scolarité, inscription école)
- `social_integration_expert` (Accompagnement social, CCAS, associations d'aide aux réfugiés)
- `job_hunter` (Emplois correspondants, SIAE)

# Instructions :
1. Fais une synthèse structurée, argumentée et détaillée pour le Travailleur Social qui soit factuelle, actionnable et ultra-convaincante en FRANÇAIS.
    - Utilise les **DONNÉES CHIFFRÉES** (scores, points forts ODIS). Exprime les scores en pourcentage (80% plutôt que 0.8)
    - N'utilise JAMAIS les codes normatifs sans les intitulés.
    - N'invente rien, utilise uniquement les éléments du brief et des experts. Mets en gras les éléments importants.
    - Sois le plus factuel possible et cite toujours le nom des entités identifiées (associations, entreprises, lieux etc.)
    - Longueur synthèse: minimum 750 mots, idéal 1000 mots. Utilise des listes à puces ou tableaux dès que pertinents.
2. Structure ta réponse au format Markdown avec les sections suivantes :
    - ## 🏙️ Aperçu de {FOCUS_CITY} : 3 à 5 phrases de description.
    - ## ⚖️ Mini analyse comparative entre {FOCUS_CITY} et {CURRENT_CITY_NAME} :
        - Identifie et résume dans un tableau Markdown les **3 à 5 points chiffrés les plus déterminants** en faveur de {FOCUS_CITY} vs {CURRENT_CITY_NAME} (ex: loyer plus bas, meilleures écoles, plus d'opportunités d'emploi spécifiques).
    - ## 🧭 Synthèse thématique :
        - **Vie Quotidienne** : Synthèse (Logement, mobilité sur place, éducation, santé, affinités culturelles, sports, loisirs etc.)
        - **Inclusion** : Synthèse (associations, solidarité, insertion)
        - **Opportunités Emploi** : Synthèse (marché du travail, secteurs porteurs, emploi)
        - **Actualités** : Synthèse des actualités pertinentes des dernières années.
    - ## ✅ Forces & ⚠️ Vigilances : Tableau Markdown.
    - ## 💡 Contact du CCAS de {FOCUS_CITY}
    - ## ❓ Et ensuite ? : Propose d'analyser une autre ville du Top 5 ou d'approfondir un point sur la ville en cours.
"""

SYNTH_SYSTEM_PROMPT_SPECIFIC = """
**Contexte** : Un Travailleur Social cherche la ville la plus adéquate pour une personne réfugiée (et sa famille). A partir de critères de recherches, l'outil identifie un Top 5 que l'on analyse et compare à la commune actuelle de la personne accompagnée.
**Rôle** : Ta mission est de répondre UNIQUEMENT à une question spécifique du Travailleur Social en utilisant les données des experts.

# Données de contexte :
```json
{DATA_CONTEXT}
```

Tu trouveras dans ces données de contexte, sous la clé `"Analyses experts"`, les contributions détaillées des experts thématiques :
- `housing_expert` (Logement, loyers, hébergement temporaire)
- `mobility_expert` (Transports locaux, abonnements, trajets)
- `healthcare_expert` (Besoins de santé, PMI, hôpitaux, médecins)
- `education_expert` (Crèches, scolarité, inscription école)
- `social_integration_expert` (Accompagnement social, CCAS, associations d'aide aux réfugiés)
- `job_hunter` (Emplois correspondants, SIAE)

# Instructions :
- Réponds UNIQUEMENT et de manière détaillée à la question de l'utilisateur : "{LAST_MESSAGE}"
- Sois factuel et précis et mentionne les codes & identifiants des éléments trouvés. Si pertinent, utilise un tableau Markdown.
- N'invente rien, utilise uniquement les éléments des Experts. Mets en gras les éléments importants. Si les données des experts sont insuffisantes, mentionne-le clairement.
"""

# No SynthesizerResult needed, using raw str for performance/stability on large outputs
synthesizer_agent = Agent(
    get_model("synthesizer"),
    model_settings=get_model_settings("synthesizer"),
    deps_type=ODISDeps,
    output_type=str
)

@synthesizer_agent.system_prompt
async def synth_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds the Synthesizer prompt using ODISContextBuilder."""
    state = ctx.deps.state
    focus_name = state.focus_city.name if state.focus_city else "Non définie"
    current_city_name = "la commune actuelle"
    if state.search_results and state.search_results.current_geo:
        current_city_name = state.search_results.current_geo.name

    data_context = ODISContextBuilder.agent_context(state, "synthesizer")
    last_message = state.messages[-1].get("content", "Non disponible") if state.messages else "Non disponible"

    mode = state.execution_mode
    prompt_template = SYNTH_SYSTEM_PROMPT_SPECIFIC if mode == "specific_ask" else SYNTH_SYSTEM_PROMPT_ANALYSIS

    prompt = prompt_template.format(
        DATA_CONTEXT=data_context,
        FOCUS_CITY=focus_name,
        CURRENT_CITY_NAME=current_city_name,
        LAST_MESSAGE=last_message,
    )

    logger.info(f"💎 [SYNTHESIZER-PROMPT] Mode: {mode}. Template: {'SPECIFIC' if mode == 'specific_ask' else 'ANALYSIS'}")
    logger.info(f"💎 [SYNTHESIZER-CONTEXT-SIZE] {len(prompt)} chars")
    logger.debug(f"💎 [SYNTHESIZER-FULL-PROMPT-DUMP]\n{prompt}\n--- END DUMP ---")

    return prompt
