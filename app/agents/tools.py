import logging
import asyncio
from typing import List, Dict, Any, Union
from services.mcp_server import (
    _search_referentiels_logic,
    _compute_top_cities_logic,
    _search_places_logic,
    _compute_routes_logic,
    _search_refugee_associations_logic,
    _search_rna_rag_logic,
    _search_ccas_logic,
    _search_inclusion_jobs_logic,
    _get_inclusion_job_details_logic,
)
from services.mcp_france_travail import _search_job_offers_logic, _get_job_details_logic
from core.models import SearchCriterias
import config as cfg  # noqa: F401

logger = logging.getLogger("agent_tools")


async def search_referentiels_batch(
    queries: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en parallèle.
    Args:
        queries: Liste de dictionnaires {'query': '...', 'domain': '...'}
    Returns:
        Dictionnaire mappant chaque requête 'query' à ses résultats.
    """
    logger.info(
        f"🚀 [TOOL] search_referentiels_batch parallel start: {len(queries)} queries"
    )

    async def _single_ref_search(item: Dict[str, Any]):
        q, d = item.get("query"), item.get("domain")
        if not (q and d):
            return None, []
        try:
            res = await asyncio.to_thread(_search_referentiels_logic, q, d)
            return f"{d}:{q}", res
        except Exception as e:
            logger.error(f"❌ [TOOL] search_referentiels_batch failed for {d}:{q}: {e}")
            return f"{d}:{q}", []

    tasks = [_single_ref_search(q) for q in queries]
    completed_results = await asyncio.gather(*tasks)

    results = {key: res for key, res in completed_results if key}
    logger.info(
        f"✅ [TOOL] search_referentiels_batch finished: {len(results)} matches."
    )
    return results


async def search_places_batch(queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des lieux (POIs), commerces ou services dans une ville (Mode Batch Parallélisé)."""
    logger.info(f"🔍 [TOOL] search_places_batch async: {queries} in {location}")
    return await _search_places_logic(queries, location)


def compute_routes(
    origin: str, destination: str, mode: str = "transit"
) -> Dict[str, Any]:
    """Calcule des itinéraires et temps de trajet."""
    logger.info(f"🔍 [TOOL] compute_routes: {origin} to {destination} in {mode}")
    return _compute_routes_logic(origin, destination, mode)


def compute_top_cities(criteria: SearchCriterias) -> Dict[str, Any]:
    """
    Calcule le top des villes de réinstallation selon les critères complets de l'utilisateur.
    """
    try:
        from core.models import CriteriaItem

        def _strip_labels(obj: Any) -> Any:
            """Recursively extract 'code' from CriteriaItem or dict-equivalent."""
            if isinstance(obj, CriteriaItem):
                return obj.code
            if isinstance(obj, dict) and "code" in obj:
                return obj["code"]
            if isinstance(obj, list):
                return [_strip_labels(i) for i in obj]
            return obj

        # Create a raw version of criteria for the scoring engine
        # We reuse the same model class but populate it with strings
        raw_data = {k: _strip_labels(v) for k, v in criteria.model_dump().items()}
        raw_criteria = SearchCriterias(**raw_data)

        res = _compute_top_cities_logic(raw_criteria)
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
    logger.info(f"🔍 [TOOL] set_focus_city: {city_name}")
    return f"SUCCÈS: Ville active définie sur {city_name}."


async def search_job_offers_batch(queries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Version optimisée pour effectuer plusieurs recherches d'offres d'emploi en un seul appel (Parallelize).
    Args:
        queries: Liste de dictionnaires contenant les paramètres de recherche (rome, location)
    Returns:
        Dictionnaire mappant une clé unique (ex: "rome:location") aux résultats.
    """
    logger.info(
        f"🚀 [TOOL] search_job_offers_batch parallel start: {len(queries)} queries"
    )

    async def _single_job_search(q_params: Dict[str, Any]):
        rome = (
            q_params.get("rome")
            or q_params.get("rome_code")
            or q_params.get("rome_codes")
        )
        loc = q_params.get("location")
        q_text = q_params.get("query")
        key = f"{rome or ''}|{loc or ''}|{q_text or ''}"
        try:
            # logic is blocking I/O (REST calls) -> wrap in to_thread
            res = await asyncio.to_thread(_search_job_offers_logic, **q_params)
            return key, res
        except Exception as e:
            logger.error(f"❌ [TOOL] search_job_offers_batch failed for {key}: {e}")
            return key, {"error": str(e), "offres": [], "total": 0}

    tasks = [_single_job_search(q) for q in queries]
    completed_results = await asyncio.gather(*tasks)

    # Reassemble as dict
    results = {key: res for key, res in completed_results}
    logger.info(
        f"✅ [TOOL] search_job_offers_batch finished: {len(results)} search buckets."
    )
    return results


def get_job_details(job_id: str) -> Dict[str, Any]:
    """
    Récupère les détails complets d'une offre d’emploi spécifique.

    Args:
        job_id: ID de l'offre d'emploi (ex: '048KLTP').
    """
    return _get_job_details_logic(job_id)


def search_refugee_associations(codgeo: str) -> List[Dict[str, Any]]:
    """
    Recherche des associations spécialisées dans l'accueil des réfugiés (RNA).
    Identifie le Bassin de Vie et retourne TOUTES les associations de la zone.

    Args:
        codgeo: Code INSEE de la commune (ex: '33063').
    """
    return _search_refugee_associations_logic(codgeo)


def search_rna_rag(
    query: str, codgeo: str, top_k: int = 10
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Recherche sémantique d'associations dans une commune spécifique (RAG).
    Retourne les associations les plus pertinentes (score > 0.8) triées par pertinence.

    Args:
        query: Terme de recherche (ex: 'football', 'hébergement d'urgence').
        codgeo: Code INSEE de la commune (5 chiffres).
        top_k: Nombre maximum de résultats à retourner.
    """
    logger.info(f"🔍 [TOOL] search_rna_rag: {query} in {codgeo}")
    return _search_rna_rag_logic(query, codgeo, top_k=top_k)


async def search_rna_rag_batch(
    queries: List[str], codgeo: str, top_k: int = 10
) -> List[Dict[str, Any]]:
    """
    Exécute plusieurs recherches sémantiques distinctes en parallèle et consolide les résultats sans doublons.

    Args:
        queries: Liste de termes de recherche.
        codgeo: Code INSEE de la commune (5 chiffres).
        top_k: Nombre maximum de résultats par terme.

    Returns:
        List[Dict[str, Any]]: Liste unique d'associations dédoublées par ID.
    """
    logger.info(f"🚀 [TOOL] search_rna_rag_batch parallel start: {queries} in {codgeo}")

    async def _single_rna_search(q: str):
        try:
            # Logic involves BigQuery and Vertex API embedding -> wrap in to_thread
            return await asyncio.to_thread(
                _search_rna_rag_logic, q, codgeo, top_k=top_k
            )
        except Exception as e:
            logger.error(f"❌ [TOOL] search_rna_rag_batch loop failed for {q}: {e}")
            return []

    tasks = [_single_rna_search(q) for q in queries]
    batch_results = await asyncio.gather(*tasks)

    all_results = []
    seen_ids = set()

    for res in batch_results:
        # _search_rna_rag_logic returns List[Dict] or Dict with error
        if isinstance(res, list):
            for assoc in res:
                assoc_id = assoc.get("id")
                if assoc_id and assoc_id not in seen_ids:
                    all_results.append(assoc)
                    seen_ids.add(assoc_id)
        elif isinstance(res, dict) and "error" in res:
            logger.warning(f"  ⚠️ Research step failed: {res['error']}")

    logger.info(
        f"✅ [TOOL] search_rna_rag_batch finished: {len(all_results)} unique results."
    )
    return all_results


def search_ccas(codgeo: str) -> List[Dict[str, Any]]:
    """
    Recherche les informations du CCAS (Centre Communal d'Action Sociale) pour une commune.
    Si aucun CCAS n'est trouvé dans la commune, l'outil retourne les CCAS du Bassin de Vie.

    Args:
        codgeo: Code INSEE de la commune (ex: '33063').
    """
    return _search_ccas_logic(codgeo)


async def search_inclusion_jobs_batch(queries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Recherche d'offres SIAE (Insertion par l'Activité Économique) en mode Batch Parallélisé.

    Args:
        queries: Liste de dictionnaires {'location': '...', 'rome': '...', 'query': '...'}
    """
    logger.info(
        f"🚀 [TOOL] search_inclusion_jobs_batch parallel start: {len(queries)} queries"
    )

    async def _single_inclusion_search(q: Dict[str, Any]):
        loc = q.get("location")
        rome = q.get("rome")
        query_text = q.get("query")
        key = f"{rome or ''}|{loc or ''}|{query_text or ''}"
        try:
            # Logic involves external API call -> wrap in to_thread
            res = await asyncio.to_thread(
                _search_inclusion_jobs_logic, location=loc, rome=rome, query=query_text
            )
            return key, res
        except Exception as e:
            logger.error(f"❌ [TOOL] search_inclusion_jobs_batch failed for {key}: {e}")
            return key, {"error": str(e), "offres": [], "total": 0}

    tasks = [_single_inclusion_search(q) for q in queries]
    completed_results = await asyncio.gather(*tasks)

    results = {key: res for key, res in completed_results}
    logger.info(
        f"✅ [TOOL] search_inclusion_jobs_batch finished: {len(results)} search buckets."
    )
    return results


def get_inclusion_job_details(siae_id: str) -> Dict[str, Any]:
    """
    Récupère les détails d'une structure SIAE et ses offres.

    Args:
        siae_id: L'identifiant (SIRET ou ID interne) de la structure.
    """
    return _get_inclusion_job_details_logic(siae_id)
