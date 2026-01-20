import logging
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
    _search_job_offers_logic,
    _get_job_details_logic
)
import config as cfg
from core.models import SearchCriterias

logger = logging.getLogger("agent_tools")

def search_referentiels(query: str, domain: str) -> List[Dict[str, Any]]:
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

def compute_top_cities(criteria: SearchCriterias) -> Dict[str, Any]:
    """
    Calcule le top des villes de réinstallation selon les critères complets de l'utilisateur.
    """
    try:
        res = _compute_top_cities_logic(criteria)
        return res
    except Exception as e:
        logger.error(f"❌ [TOOL] compute_top_cities failed: {e}", exc_info=True)
        return {"error": str(e)}

def update_search_criteria(criteria_to_update: Dict[str, Any]) -> str:
    """Met à jour les critères de recherche (ex: {'nb_adultes': 2}). Appelle cet outil dès que tu as validé une info."""
    return "SUCCESS: Critères mis à jour." 

def set_focus_city(city_name: str) -> str:
    """
    Définit la ville 'active' ou 'focus' pour la conversation de terrain.
    À utiliser dès que l'utilisateur s'intéresse à une ville spécifique (ex: 'Parle moi de Bordeaux').
    """
    # Simply return the city name; the Agent/Graph will handle the state update.
    return f"SUCCÈS: Ville active définie sur {city_name}."

def search_job_offers(
    query: Optional[str] = None, 
    location: Optional[str] = None, 
    rome: Optional[str] = None, 
    appellation_codes: Optional[List[str]] = None,
    distance: int = 10,
    rome_code: Optional[str] = None,
    rome_codes: Optional[str] = None
) -> Dict[str, Any]:
    """
    Recherche des offres d'emploi réelles sur France Travail.
    Utilise cet outil pour trouver des opportunités concrètes.
    
    Args:
        query: Mots clés supplémentaires (ex: 'Alternance').
        location: Code INSEE de la commune (ex: '33063').
        rome: Code ROME (Métier) de 5 caractères (ex: 'M1805').
        # appellation_codes: Liste de codes métiers précis (ROME Appellations).
        distance: Rayon de recherche en km autour de la commune.
    """
    try:
        res = _search_job_offers_logic(
            query=query, 
            location=location, 
            rome=rome, 
            distance=distance,
            rome_code=rome_code,
            rome_codes=rome_codes
        )
        return res
    except Exception as e:
        logger.error(f"❌ [TOOL] search_job_offers failed: {e}", exc_info=True)
        return {"offres": [], "total": 0, "error": str(e)}
        
def get_job_details(job_id: str) -> Dict[str, Any]:
    """
    Récupère les détails complets d'une offre d’emploi spécifique.

    Args:
        job_id: ID de l'offre d'emploi (ex: '048KLTP').
    """
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
    return _search_refugee_associations_logic(codgeo)

def search_odis_associations(codgeo: str) -> List[Dict[str, Any]]:
    """
    Recherche les associations locales (Sports, Culture, Loisirs, Social) dans l'annuaire ODIS.
    Retourne les associations de la commune ou du Bassin de Vie.
    
    Args:
        codgeo: Code INSEE de la commune (ex: '33063').
    """
    return _search_odis_associations_logic(codgeo)
