
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
import config as cfg
from .state import ODISGraphState, ODISDeps
from core.models import SearchCriterias
from .agent_config import get_model
# Import the pure tools
from .tools import search_referentiels, search_commune

logger = logging.getLogger("interviewer_agent_v2")

# --- Structured Output ---
# Though Interviewer mainly talks, defining a structure helps if we want to extract final data.
class InterviewerResult(BaseModel):
    response: str
    intermediate_criteria_update: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(populate_by_name=True)

# --- Prompt ---
# We keep the core prompt but adapt it for PydanticAI (system_prompt)
INTERVIEWER_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Interviewer ODIS. Ta mission est de collecter auprès d'un travailleur social (l'utilisateur) les besoins d'un réfugié (et éventuellement sa famille) pour sa mobilité en France.
**Ton** : Empathique, professionnel, direct. Utilise le tutoiement.

**Outils disponibles** :
- `search_commune` : Cherche le code INSEE d'une commune.
- `search_referentiels` : Cherche les codes ROME (métier), Formation, Services d'inclusion ou catégorie associative (WALDEC) correspondants.
- `update_search_criteria` : Enregistre les données validées dans le dossier.

**DIRECTIVE DE COLLECTE (CRITIQUE)** :
- **GROUPEMENT DES APPELS** : Ne fais JAMAIS plusieurs appels à `update_search_criteria` à la suite. Collecte TOUTES les informations d'un message utilisateur (ex: nom, métier, ville) et fais UN SEUL appel à l'outil à la fin.
- **PARCIMONIE DES OUTILS** : N'utilise `search_referentiels` ou `search_commune` que si l'information n'est pas déjà claire dans le **Briefing**.
- **PAS DE DISCOURS INUTILE** : Ne commente pas tes appels d'outils ("Je vais chercher le code..."). Fais l'appel, et ne demande confirmation que s'il y a ambiguité ou pas de correspondance trouvée.

**Instructions de Collecte (Ordre Prioritaire)** :
1. **Commune Actuelle** : Cherche le code INSEE (codgeo) avec `search_commune`.
2. **Périmètre de Recherche** : Identifie la zone cible. Par défaut c'est le département de la commune actuelle. Si l'utilisateur veut une autre zone (ex: "Je veux aller en Gironde"), identifie le département ou la région cible avec `search_referentiels` et enregistre le code dans `loc_search_code` en précisant le type dans `loc_search_area` ('departement', 'region' ou 'france').
3. **Composition Familiale** : Demande le nombre d'adultes et d'enfants.
4. **Projet Pro & Formations** : Cherche IMMEDIATEMENT via `search_referentiels` les codes ROME (`rome_codes`) ou Formation (`formation_codes`) correspondant.
5. **Logement & Hébergement** : Choisi dans {HEBERGEMENT_OPTIONS} et {LOGEMENT_OPTIONS}.
6. **Éducation des Enfants** : Choisi dans {CLASSES_SCOLAIRES}.
7. **Santé Spécifique** : Choisi dans {SANTE_OPTIONS}.
8. **Notes Qualitatives** : Passions, origine, besoins spécifiques (Religion, Velo, Halal, etc).
9. **Inclusions & Assos** : Cherche IMMEDIATEMENT via `search_referentiels` (inclusion_services ou waldec_codes).

**Profil de Pondération** : Suggère un profil parmi : {WEIGHT_PROFILES}.

**DIRECTIVE DE TRANSITION** :
Tant que tu n'as pas : **Commune Actuelle**, **Nb Adultes**, **Profil** et **Périmètre**, reste en collecte.
Une fois acquis, dis : "J'ai bien noté vos critères (Profil: {weight_profile}, Zone: {loc_search_area}). Voulez-vous que je lance le calcul pour trouver vos meilleures villes ?"
Demande la confirmation avant de terminer.
**DIRECTIVE DE SORTIE** : Réponds toujours de manière structurée selon le schéma InterviewerResult fourni. Ta réponse textuelle à l'utilisateur doit se trouver dans le champ 'response'.
"""

# --- Agent Definition ---
# We redefine the agent slightly to use dynamic instructions fully
interviewer_agent = Agent(
    get_model("interviewer"), 
    deps_type=ODISDeps,
    output_type=InterviewerResult
)

@interviewer_agent.system_prompt
async def main_instructions(ctx: RunContext[ODISDeps]) -> str:
    # Prepare dynamic values
    filtered_areas_values = [v for k, v in cfg.LOC_SEARCH_AREA_OPTIONS.items() if k != 'custom']
    wp = ctx.deps.state.search_criteria.weight_profile or 'Non défini'
    lsa = ctx.deps.state.search_criteria.loc_search_area or 'Non définie'
    
    prompt = INTERVIEWER_SYSTEM_PROMPT.replace("{BRIEFING}", ctx.deps.state.briefing or "(Pas de briefing)")
    prompt = prompt.replace("{CLASSES_SCOLAIRES}", str(cfg.CLASSES_SCOLAIRES))
    prompt = prompt.replace("{HEBERGEMENT_OPTIONS}", str(cfg.HEBERGEMENT_OPTIONS))
    prompt = prompt.replace("{LOGEMENT_OPTIONS}", str(cfg.LOGEMENT_OPTIONS))
    prompt = prompt.replace("{SANTE_OPTIONS}", str(cfg.SANTE_OPTIONS))
    prompt = prompt.replace("{WEIGHT_PROFILES}", str(list(cfg.WEIGHT_PROFILES.keys())))
    prompt = prompt.replace("{LOC_SEARCH_AREAS}", ", ".join(filtered_areas_values))
    prompt = prompt.replace("{weight_profile}", wp)
    prompt = prompt.replace("{loc_search_area}", lsa)
    
    return prompt

# --- Tools ---

# We wrap the pure tools to make them PydanticAI compatible (inject context if needed)
# search_commune and search_referentiels are pure lookups, so we can just register them directly?
# PydanticAI tools receive `ctx: RunContext[Deps]` as first arg if typed.

@interviewer_agent.tool
def search_commune_tool(ctx: RunContext[ODISDeps], query: str) -> List[Dict[str, Any]]:
    """Recherche une ville française pour obtenir son code INSEE.
    
    Args:
        query (str): Nom de la commune à rechercher.
    
    Returns:
        List[Dict[str, Any]]: Liste des codes INSEE correspondants.
    """
    return search_commune(query)

@interviewer_agent.tool
def search_referentiels_tool(ctx: RunContext[ODISDeps], query: str, domain: str) -> List[Dict[str, Any]]:
    """Recherche des codes officiels (Formations, ROME, Services d'inclusion, WALDEC, etc.) dans les référentiels.
    
    Args:
        query (str): Recherche à effectuer.
        domain (str): Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements'].
    
    Returns:
        List[Dict[str, Any]]: Liste des codes officiels correspondants.
    """
    return search_referentiels(query, domain)

@interviewer_agent.tool
def update_search_criteria_tool(
    ctx: RunContext[ODISDeps],
    commune_actuelle: Optional[str] = None,
    nb_adultes: Optional[int] = None,
    nb_enfants: Optional[int] = None,
    weight_profile: Optional[str] = None,
    loc_search_area: Optional[str] = None,
    loc_search_code: Optional[str] = None,
    codes_metiers: Optional[List[List[str]]] = None,
    codes_formations: Optional[List[List[str]]] = None,
    classe_enfants: Optional[List[str]] = None,
    inc_services_add_selection: Optional[List[str]] = None,
    inc_asso_add_selection: Optional[List[str]] = None,
    hebergement: Optional[str] = None,
    logement: Optional[str] = None,
    sante: Optional[str] = None,
    notes_qualitatives: Optional[List[str]] = None
) -> str:
    """
    Enregistre les données validées dans le dossier du bénéficiaire.

    Args:
        commune_actuelle (Optional[str]): Code INSEE de la commune actuelle.
        nb_adultes (Optional[int]): Nombre d'adultes.
        nb_enfants (Optional[int]): Nombre d'enfants.
        weight_profile (Optional[str]): Profil de pondération.
        loc_search_area (Optional[str]): Type de zone de recherche (departement, region, france).
        loc_search_code (Optional[str]): Code INSEE du département ou de la région cible si différent de la commune actuelle.
        codes_metiers (Optional[List[List[str]]]): Codes métiers.
        codes_formations (Optional[List[List[str]]]): Codes formations.
        classe_enfants (Optional[List[str]]): Classe d'enfants.
        inc_services_add_selection (Optional[List[str]]): Services d'inclusion.
        inc_asso_add_selection (Optional[List[str]]): Associations d'inclusion.
        hebergement (Optional[str]): Hebergement.
        logement (Optional[str]): Logement.
        sante (Optional[str]): Santé.
        notes_qualitatives (Optional[List[str]]): Notes qualitatives.
    
    Returns:
        str: Message de confirmation.
    """
    # This acts as a Side-Effect on the Graph State (Dependency)
    # In LangGraph + PydanticAI pattern, we might want to return the diff 
    # BUT for now direct mutation of the `deps` object (ODISGraphState) is the way to propagate 
    # changes if we pass the same object reference around.
    
    updates: Dict[str, Any] = {}
    if commune_actuelle: updates['commune_actuelle'] = commune_actuelle
    if nb_adultes is not None: updates['nb_adultes'] = nb_adultes
    if nb_enfants is not None: updates['nb_enfants'] = nb_enfants
    if weight_profile: updates['weight_profile'] = weight_profile
    if loc_search_area: updates['loc_search_area'] = loc_search_area
    if loc_search_code: updates['loc_search_code'] = loc_search_code
    if codes_metiers: updates['codes_metiers'] = codes_metiers
    if codes_formations: updates['codes_formations'] = codes_formations
    if classe_enfants: updates['classe_enfants'] = classe_enfants
    if inc_services_add_selection: updates['inc_services_add_selection'] = inc_services_add_selection
    if inc_asso_add_selection: updates['inc_asso_add_selection'] = inc_asso_add_selection
    if hebergement: updates['hebergement'] = hebergement
    if logement: updates['logement'] = logement
    if sante: updates['sante'] = sante
    if notes_qualitatives: updates['notes_qualitatives'] = notes_qualitatives
    
    if updates:
        # Update the SearchCriterias model in the state
        # We use model_copy with update to safely update fields
        ctx.deps.state.search_criteria = ctx.deps.state.search_criteria.model_copy(update=updates)
        return f"SUCCESS: Données enregistrées: {list(updates.keys())}"
    return "Aucune mise à jour détectée."