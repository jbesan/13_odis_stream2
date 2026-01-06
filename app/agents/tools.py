import logging
import streamlit as st
from typing import List, Dict, Any, Optional, Union
from mcp_server import (
    _search_referentiels_logic, 
    _search_commune_logic,
    _compute_top_cities_logic,
    _search_places_logic,
    _compute_routes_logic
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
        return _compute_top_cities_logic(weights, filters_dict)
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
    if "agent" in st.session_state:
        st.session_state.agent.context.focus_city = city_name
    return f"SUCCÈS: Ville active définie sur {city_name}."
