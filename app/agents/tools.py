import logging
import streamlit as st
from typing import List, Dict, Any, Optional, Union
from services.mcp_server import (
    _search_referentiels_logic, 
    _search_commune_logic,
    _compute_top_cities_logic,
    _search_places_logic,
    _compute_routes_logic,
    _get_labels_for_codes_logic,
    _search_refugee_associations_logic,
    _search_odis_associations_logic
)
from services.mcp_france_travail import (
    _get_job_details_logic
)
import config as cfg
from core.models import SearchCriterias

logger = logging.getLogger("agent_tools")

def search_referentiels(query: str, domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """Recherche des codes officiels (Formations, ROME, Services d'inclusion, WALDEC, etc.) dans les référentiels."""
    return _search_referentiels_logic(query, domain)

def search_commune(query: str) -> List[Dict[str, Any]]:
    """Recherche une ville française pour obtenir son code INSEE."""
    return _search_commune_logic(query)

def search_places(queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des lieux (POIs), commerces ou services dans une ville."""
    return _search_places_logic(queries, location)

def compute_routes(origin: str, destination: str, mode: str = "transit") -> Dict[str, Any]:
    """Calcule des itinéraires et temps de trajet."""
    return _compute_routes_logic(origin, destination, mode)

def compute_top_cities(filters: SearchCriterias) -> Dict[str, Any]:
    """
    Calcule le top des villes de réinstallation selon les critères complets de l'utilisateur.
    """
    try:
        # Convert Pydantic object to dict for the engine
        filters_dict = filters.model_dump() if hasattr(filters, 'model_dump') else filters
        
        # Extract profile from the filters (it's the source of truth)
        weight_profile = filters.weight_profile or "Équilibré"
        logger.info(f"⚒️ [TOOL] compute_top_cities called (extracted profile: {weight_profile})")

        weights = cfg.WEIGHT_PROFILES.get(weight_profile, cfg.WEIGHT_PROFILES["Équilibré"])
        res = _compute_top_cities_logic(weights, filters_dict)
        
        # Save results to context if possible
        if "cities" in res and "agent" in st.session_state:
            st.session_state.agent.context.top_cities = res["cities"]
            logger.info(f"⚒️ [TOOL] Saved {len(res['cities'])} cities to AgentContext.")
            
        return res
    except Exception as e:
        logger.error(f"❌ [TOOL] compute_top_cities_logic failed: {e}", exc_info=True)
        return {"error": str(e)}

def update_search_criteria(criteria_to_update: Dict[str, Any]) -> str:
    """Met à jour les critères de recherche (ex: {'nb_adultes': 2}). Appelle cet outil dès que tu as validé une info."""
    return "SUCCESS: Critères mis à jour." # The actual logic is handled in the agent run loop

def set_focus_city(city_name: str) -> str:
    """
    Définit la ville 'active' ou 'focus' pour la conversation de terrain.
    À utiliser dès que l'utilisateur s'intéresse à une ville spécifique (ex: 'Parle moi de Bordeaux').
    """
    # logger.info(f"⚒️ [TOOL] set_focus_city: '{city_name}'")
    if "agent" in st.session_state:
        st.session_state.agent.context.focus_city = city_name
        logger.info(f"⚒️ [TOOL] context.focus_city updated to: {city_name}")
    else:
        logger.warning("⚠️ [TOOL] set_focus_city: st.session_state.agent not found!")
    return f"SUCCÈS: Ville active définie sur {city_name}."

def search_job_offers(
    query: Optional[str] = None, 
    location: Optional[str] = None, 
    rome_code: Optional[str] = None, 
    appellation_codes: Optional[List[str]] = None,
    distance: int = 10
) -> Dict[str, Any]:
    """
    Recherche des offres d'emploi réelles sur France Travail.
    Utilise cet outil pour trouver des opportunités concrètes.
    
    Args:
        query: Mots clés supplémentaires (ex: 'Alternance').
        location: Code INSEE de la commune (ex: '33063').
        rome_code: Code ROME (Métier) de 5 caractères (ex: 'M1805').
        # appellation_codes: Liste de codes métiers précis (ROME Appellations).
        distance: Rayon de recherche en km autour de la commune.
    """
    logger.info(f"⚒️ [TOOL] search_job_offers: query={query}, location={location}, rome={rome_code}, apps={appellation_codes}")
    res = _search_job_offers_logic(
        query=query, 
        location=location, 
        rome_code=rome_code, 
        appellation_codes=appellation_codes,
        distance=distance
    )
    
    # Save results to context if possible
    if "offres" in res and "agent" in st.session_state:
        st.session_state.agent.context.found_jobs = res["offres"]
        logger.info(f"⚒️ [TOOL] Saved {len(res['offres'])} job offers to AgentContext.") 
        
    return res


def get_job_details(job_id: str) -> Dict[str, Any]:
    """
    Récupère les détails complets d'une offre d’emploi spécifique.

    Args:
        job_id: ID de l'offre d'emploi (ex: '048KLTP').
    """
    logger.info(f"⚒️ [TOOL] get_job_details: {job_id}")
    return _get_job_details_logic(job_id)

def get_labels_for_codes(codes: List[str]) -> Dict[str, str]:
    """
    Récupère les libellés en français pour une liste de codes (ROME, INSEE, etc.).
    Utile pour savoir à quoi correspond un code avant de l'utiliser.
    """
    return _get_labels_for_codes_logic(codes)


def search_refugee_associations(codgeo: str) -> List[Dict[str, Any]]:
    """
    Recherche des associations spécialisées dans l'accueil des réfugiés (RNA).
    Identifie le Bassin de Vie et retourne TOUTES les associations de la zone.
    
    Args:
        codgeo: Code INSEE de la commune (ex: '33063').
    """
    logger.info(f"⚒️ [TOOL] search_refugee_associations called with codgeo='{codgeo}'")
    import random
    st.toast(random.choice([
        "Consultation du registre des mains tendues...",
        "Décollage pour le QG des associations solidaires...",
        "Infiltration de la base secrète des bénévoles...",
        "Scan des initiatives du cœur dans la zone...",
        "Extraction de la liste des anges gardiens locaux..."
    ]), icon="🤝")
    return _search_refugee_associations_logic(codgeo)

def search_odis_associations(codgeo: str) -> List[Dict[str, Any]]:
    """
    Recherche les associations locales (Sports, Culture, Loisirs, Social) dans l'annuaire ODIS.
    Retourne les associations de la commune ou du Bassin de Vie.
    
    Args:
        codgeo: Code INSEE de la commune (ex: '33063').
    """
    logger.info(f"⚒️ [TOOL] search_odis_associations called with codgeo='{codgeo}'")
    return _search_odis_associations_logic(codgeo)
