import logging
import streamlit as st
from typing import List, Dict, Any, Optional, Union
from mcp_server import (
    _search_referentiels_logic, 
    _search_commune_logic,
    _compute_top_cities_logic,
    _search_places_logic,
    _compute_routes_logic,
    _get_labels_for_codes_logic,
    _get_rome_for_fap_logic
)
from mcp_france_travail import (
    search_job_offers_logic as _search_job_offers_logic,
    _get_job_details_logic,
    _search_rome_appellations_logic
)
from config import WEIGHT_PROFILES
from models import SearchCriterias

logger = logging.getLogger("agent_tools")

def search_referentiels(query: str, domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """Recherche des codes officiels (FAP, ROME, etc.) dans les référentiels."""
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
        logger.info(f"🚀 [TOOL] compute_top_cities called (extracted profile: {weight_profile})")

        weights = WEIGHT_PROFILES.get(weight_profile, WEIGHT_PROFILES["Équilibré"])
        res = _compute_top_cities_logic(weights, filters_dict)
        
        # Save results to context if possible
        if "cities" in res and "agent" in st.session_state:
            st.session_state.agent.context.top_cities = res["cities"]
            logger.info(f"✅ [TOOL] Saved {len(res['cities'])} cities to AgentContext.")
            
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
    logger.info(f"📍 [TOOL] set_focus_city: '{city_name}'")
    if "agent" in st.session_state:
        st.session_state.agent.context.focus_city = city_name
        logger.info(f"✅ [TOOL] context.focus_city updated to: {city_name}")
    else:
        logger.warning("⚠️ [TOOL] set_focus_city: st.session_state.agent not found!")
    return f"SUCCÈS: Ville active définie sur {city_name}."

def search_job_offers(
    query: Optional[str] = None, 
    location: Optional[str] = None, 
    fap_code: Optional[str] = None, 
    appellation_codes: Optional[List[str]] = None,
    distance: int = 10
) -> Dict[str, Any]:
    """
    Recherche des offres d'emploi réelles sur France Travail.
    Utilise cet outil pour trouver des opportunités concrètes.
    
    Args:
        query: Mots clés supplémentaires (ex: 'Alternance').
        location: Code INSEE de la commune (ex: '33063').
        fap_code: Code FAP (Famille Professionnelle) de métier.
        appellation_codes: Liste de codes métiers précis (ROME Appellations).
        distance: Rayon de recherche en km autour de la commune.
    """
    logger.info(f"🚀 [TOOL] search_job_offers: query={query}, location={location}, fap={fap_code}, apps={appellation_codes}")
    res = _search_job_offers_logic(
        query=query, 
        location=location, 
        fap_code=fap_code, 
        appellation_codes=appellation_codes,
        distance=distance
    )
    
    # Save results to context if possible
    if "offres" in res and "agent" in st.session_state:
        st.session_state.agent.context.found_jobs = res["offres"]
        logger.info(f"✅ [TOOL] Saved {len(res['offres'])} job offers to AgentContext.")
        
    return res

def search_rome_appellations(query: str) -> List[Dict[str, str]]:
    """
    Recherche des intitulés de métiers précis (appellations ROME) à partir d'un mot-clé.
    Utile pour traduire un code FAP (ex: 'Boulanger') en codes précis pour la recherche d'offres.
    """
    logger.info(f"🚀 [TOOL] search_rome_appellations: '{query}'")
    return _search_rome_appellations_logic(query)

def get_job_details(job_id: str) -> Dict[str, Any]:
    """
    Récupère les détails complets d'une offre d’emploi spécifique.

    Args:
        job_id: ID de l'offre d'emploi (ex: '048KLTP').
    """
    logger.info(f"🚀 [TOOL] get_job_details: {job_id}")
    return _get_job_details_logic(job_id)

def get_labels_for_codes(codes: List[str]) -> Dict[str, str]:
    """
    Récupère les libellés en français pour une liste de codes (FAP, INSEE, etc.).
    Utile pour savoir à quoi correspond un code avant de l'utiliser.
    """
    return _get_labels_for_codes_logic(codes)

def get_rome_for_fap(fap_codes: List[str]) -> Dict[str, List[str]]:
    """
    Traduit des codes FAP (Ex: 'A0X41') en codes ROME correspondants.
    C'est la méthode la plus fiable pour trouver des offres d'emploi pour un profil ODIS.
    """
    return _get_rome_for_fap_logic(fap_codes)
