import logging
import asyncio
from typing import List, Dict, Any, Union, Optional, Literal, Annotated
from pydantic import BaseModel, Field

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
from services.mcp_france_travail import (
    _search_job_offers_logic,
    _get_job_details_logic,
)
from core.models import SearchCriterias
import config as cfg  # noqa: F401

logger = logging.getLogger("agent_tools")


# ==============================================================================
# 1. Pydantic Models for Agent Tools
# ==============================================================================


class RnaSearchQuery(BaseModel):
    """Recherche d'associations au Répertoire National des Associations officiel sur le bassin de vie."""

    queries: List[str] = Field(
        min_length=1,
        max_length=5,
        description="Termes statutaires ou mots-clés concrets indispensables (2 à 4 mots, max 5 requêtes). Ne JAMAIS inclure le nom de la commune.",
    )
    codgeo: str = Field(
        pattern=r"^(?:\d{2}|2[ABab])\d{3}$",
        examples=["33063"],
        description="Code INSEE officiel de la commune.",
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Nombre maximum de résultats par terme (défaut: 10, max: 20).",
    )


class PlacesSearchQuery(BaseModel):
    """Recherche sur Google Maps pour la commune et ses alentours en mode batch."""

    queries: List[str] = Field(
        min_length=1,
        max_length=5,
        description="Requêtes ciblées indispensables (maximum 3 à 5 requêtes par appel batch). Ne cherche jamais ce qui figure déjà dans le dossier.",
    )
    location: str = Field(
        min_length=2,
        max_length=160,
        description="Commune cible et département ou région (ex: 'Bordeaux, Nouvelle-Aquitaine').",
    )


class RouteCalculationQuery(BaseModel):
    """Calcul d'itinéraires et temps de trajet."""

    origin: str = Field(description="Point de départ (adresse, commune ou lieu-dit).")
    destination: str = Field(
        description="Point d'arrivée (adresse, commune ou équipement)."
    )
    mode: Literal["transit", "driving", "walking", "bicycling"] = Field(
        default="transit",
        description="Mode de déplacement ('transit', 'driving', 'walking', 'bicycling').",
    )


class JobOfferSearchQuery(BaseModel):
    """Recherche d'offres d'emploi sur France Travail en mode batch."""

    location: Optional[str] = Field(
        None,
        description="Code INSEE de la commune (5 chiffres) ou nom de commune.",
    )
    rome: Optional[str] = Field(
        None,
        pattern=r"^[A-N][0-9]{4}$",
        description="Code métier ROME officiel de 5 caractères (ex: 'D1102').",
    )
    query: Optional[str] = Field(
        None,
        description="Mots-clés libres complémentaires (ex: 'Alternance', 'Boulangerie').",
    )
    distance: int = Field(
        default=10,
        ge=0,
        le=100,
        description="Rayon de recherche en km (défaut: 10, max: 100).",
    )


class InclusionJobSearchQuery(BaseModel):
    """Recherche d'offres d'insertion (SIAE) sur Les emplois de l'inclusion."""

    location: str = Field(
        pattern=r"^(?:\d{2}|2[ABab])\d{3}$",
        examples=["13018"],
        description=(
            "Code INSEE officiel de la commune (5 caractères, ex: '13018', '2A004'). "
            "Prendre STRICTEMENT le 'Code INSEE' présent dans la section 'Commune à analyser'. "
            "Ne JAMAIS mettre de code postal (ex: 13440 est interdit) ni de département seul."
        ),
    )
    rome: Optional[str] = Field(
        None,
        pattern=r"^[A-N][0-9]{4}$",
        examples=["A1203"],
        description="Code ROME officiel de 5 caractères.",
    )
    query: Optional[str] = Field(
        None,
        description="Mot-clé libre optionnel pour filtrer le titre du poste SIAE.",
    )


class ReferentielSearchQuery(BaseModel):
    """Normalisation et recherche dans les référentiels officiels."""

    query: str = Field(
        description="Terme à rechercher ou normaliser (ex: 'Boulanger', 'Bordeaux', 'Plomberie').",
    )
    domain: Literal[
        "rome_codes",
        "communes",
        "formation_codes",
        "inclusion_services",
        "waldec_codes",
        "regions",
        "departements",
        "housing_types",
    ] = Field(
        description="Domaine cible ('rome_codes', 'communes', 'formation_codes', 'inclusion_services', 'waldec_codes', 'regions', 'departements', 'housing_types').",
    )


# ==============================================================================
# 2. Referentials
# ==============================================================================


async def search_referentiels_batch(
    searches: List[ReferentielSearchQuery],
) -> Dict[str, List[Dict[str, Any]]]:
    """Effectue plusieurs recherches de référentiels en parallèle.

    Args:
        searches: Liste d'objets ReferentielSearchQuery {query, domain}

    Returns:
        Dictionnaire mappant chaque requête 'domain:query' à ses résultats.
    """
    logger.info(
        f"🚀 [TOOL] search_referentiels_batch parallel start: {len(searches)} queries"
    )

    async def _single_ref_search(s: ReferentielSearchQuery):
        q, d = s.query, s.domain
        try:
            res = await asyncio.to_thread(_search_referentiels_logic, q, d)
            return f"{d}:{q}", res
        except Exception as e:
            logger.error(f"❌ [TOOL] search_referentiels_batch failed for {d}:{q}: {e}")
            return f"{d}:{q}", []

    tasks = [_single_ref_search(s) for s in searches]
    completed_results = await asyncio.gather(*tasks)

    results = {key: res for key, res in completed_results if key}
    logger.info(
        f"✅ [TOOL] search_referentiels_batch finished: {len(results)} matches."
    )
    return results


async def search_referentiels_batch_tool(
    searches: Annotated[
        List[ReferentielSearchQuery],
        Field(
            min_length=1,
            max_length=10,
            description="Liste de 1 à 10 termes de référentiels à normaliser en batch.",
        ),
    ],
) -> Dict[str, List[Dict[str, Any]]]:
    """Normalisation et recherche en batch dans les référentiels officiels."""
    return await search_referentiels_batch(searches)


# ==============================================================================
# 3. Google Places & Routes
# ==============================================================================


async def search_places_batch(queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des lieux (POIs), commerces ou services dans une ville (Mode Batch Parallélisé)."""
    logger.info(f"🔍 [TOOL] search_places_batch async: {queries} in {location}")
    return await _search_places_logic(queries, location)


async def search_places_batch_tool(params: PlacesSearchQuery) -> Dict[str, Any]:
    """Recherche sur Google Maps pour la commune et ses alentours en mode batch."""
    return await search_places_batch(params.queries, params.location)


def compute_routes(
    origin: str, destination: str, mode: str = "transit"
) -> Dict[str, Any]:
    """Calcule des itinéraires et temps de trajet."""
    logger.info(f"🔍 [TOOL] compute_routes: {origin} to {destination} in {mode}")
    return _compute_routes_logic(origin, destination, mode)


def compute_routes_tool(params: RouteCalculationQuery) -> Dict[str, Any]:
    """Calcule des itinéraires et temps de trajet entre deux localisations."""
    return compute_routes(params.origin, params.destination, params.mode)


# ==============================================================================
# 4. Top Cities Computation
# ==============================================================================


def compute_top_cities(criteria: SearchCriterias) -> Dict[str, Any]:
    """Calcule le top des villes de réinstallation selon les critères complets de l'utilisateur."""
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

        raw_data = {k: _strip_labels(v) for k, v in criteria.model_dump().items()}
        raw_criteria = SearchCriterias(**raw_data)

        res = _compute_top_cities_logic(raw_criteria)
        return res
    except Exception as e:
        logger.error(f"❌ [TOOL] compute_top_cities failed: {e}", exc_info=True)
        return {"error": str(e)}


# ==============================================================================
# 5. France Travail Job Offers
# ==============================================================================


async def search_job_offers_batch(
    searches: List[JobOfferSearchQuery],
) -> Dict[str, Any]:
    """Effectue plusieurs recherches d'offres d'emploi en parallèle sur France Travail.

    Args:
        searches: Liste d'objets JobOfferSearchQuery {location, rome, query, distance}

    Returns:
        Dictionnaire mappant une clé unique ("rome|location|query") aux résultats.
    """
    logger.info(
        f"🚀 [TOOL] search_job_offers_batch parallel start: {len(searches)} queries"
    )

    async def _single_job_search(s: JobOfferSearchQuery):
        rome = s.rome
        loc = s.location
        q_text = s.query
        distance = s.distance
        key = f"{rome or ''}|{loc or ''}|{q_text or ''}"
        try:
            res = await asyncio.to_thread(
                _search_job_offers_logic,
                query=q_text,
                location=loc,
                rome=rome,
                distance=distance,
            )
            return key, res
        except Exception as e:
            logger.error(f"❌ [TOOL] search_job_offers_batch failed for {key}: {e}")
            return key, {"error": str(e), "offres": [], "total": 0}

    tasks = [_single_job_search(s) for s in searches]
    completed_results = await asyncio.gather(*tasks)

    results = {key: res for key, res in completed_results}
    logger.info(
        f"✅ [TOOL] search_job_offers_batch finished: {len(results)} search buckets."
    )
    return results


async def search_job_offers_batch_tool(
    searches: Annotated[
        List[JobOfferSearchQuery],
        Field(
            min_length=1,
            max_length=5,
            description="Liste de 1 à 5 critères de recherche d'offres d'emploi en batch.",
        ),
    ],
) -> Dict[str, Any]:
    """Recherche d'offres d'emploi sur France Travail en mode batch."""
    return await search_job_offers_batch(searches)


def get_job_details(job_id: str) -> Dict[str, Any]:
    """Récupère les détails complets d'une offre d'emploi spécifique."""
    return _get_job_details_logic(job_id)


def get_job_details_tool(job_id: str) -> Dict[str, Any]:
    """Recherche des détails d'une offre d'emploi ou structure d'insertion (SIAE)."""
    if len(job_id) < 10:
        return get_job_details(job_id)
    return get_inclusion_job_details(job_id)


# ==============================================================================
# 6. RNA & Associations
# ==============================================================================


def search_refugee_associations(codgeo: str) -> List[Dict[str, Any]]:
    """Recherche des associations spécialisées dans l'accueil des réfugiés (RNA).

    Identifie le Bassin de Vie et retourne TOUTES les associations de la zone.
    """
    return _search_refugee_associations_logic(codgeo)


def search_rna_rag(
    query: str, codgeo: str, top_k: int = 10
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Recherche sémantique d'associations dans une commune spécifique (RAG)."""
    logger.info(f"🔍 [TOOL] search_rna_rag: {query} in {codgeo}")
    return _search_rna_rag_logic(query, codgeo, top_k=top_k)


async def search_rna_rag_batch(
    queries: List[str], codgeo: str, top_k: int = 10
) -> List[Dict[str, Any]]:
    """Exécute plusieurs recherches BM25 distinctes en parallèle et consolide les résultats sans doublons.

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


async def search_rna_rag_batch_tool(params: RnaSearchQuery) -> List[Dict[str, Any]]:
    """Recherche BM25 sur le Répertoire National des Associations (RNA) officiel sur l'ensemble du bassin de vie de la commune."""
    return await search_rna_rag_batch(
        params.queries, params.codgeo, top_k=params.top_k
    )


# ==============================================================================
# 7. CCAS
# ==============================================================================


def search_ccas(codgeo: str) -> List[Dict[str, Any]]:
    """Recherche les informations du CCAS (Centre Communal d'Action Sociale) pour une commune."""
    return _search_ccas_logic(codgeo)


# ==============================================================================
# 8. Inclusion Jobs (SIAE)
# ==============================================================================


async def search_inclusion_jobs_batch(
    searches: List[InclusionJobSearchQuery],
) -> Dict[str, Any]:
    """Recherche d'offres SIAE (Insertion par l'Activité Économique) sur la plateforme officielle Emplois Inclusion.

    Args:
        searches: Liste d'objets InclusionJobSearchQuery {location, rome, query}

    Returns:
        Dictionnaire mappant une clé unique ("rome|location|query") aux résultats.
    """
    logger.info(
        f"🚀 [TOOL] search_inclusion_jobs_batch parallel start: {len(searches)} queries"
    )

    async def _single_inclusion_search(s: InclusionJobSearchQuery):
        loc = s.location
        rome = s.rome
        query_text = s.query
        key = f"{rome or ''}|{loc or ''}|{query_text or ''}"
        try:
            res = await asyncio.to_thread(
                _search_inclusion_jobs_logic, location=loc, rome=rome, query=query_text
            )
            return key, res
        except Exception as e:
            logger.error(f"❌ [TOOL] search_inclusion_jobs_batch failed for {key}: {e}")
            return key, {"error": str(e), "offres": [], "total": 0}

    tasks = [_single_inclusion_search(s) for s in searches]
    completed_results = await asyncio.gather(*tasks)

    results = {key: res for key, res in completed_results}
    logger.info(
        f"✅ [TOOL] search_inclusion_jobs_batch finished: {len(results)} search buckets."
    )
    return results


async def search_inclusion_jobs_batch_tool(
    searches: Annotated[
        List[InclusionJobSearchQuery],
        Field(
            min_length=1,
            max_length=5,
            description="Liste de 1 à 5 critères de recherche d'offres d'insertion (SIAE) en batch.",
        ),
    ],
) -> Dict[str, Any]:
    """Recherche d'offres d'insertion (SIAE) en mode batch sur Les emplois de l'inclusion."""
    return await search_inclusion_jobs_batch(searches)


def get_inclusion_job_details(siae_id: str) -> Dict[str, Any]:
    """Récupère les détails d'une structure SIAE et ses offres."""
    return _get_inclusion_job_details_logic(siae_id)


def get_inclusion_job_details_tool(siae_id: str) -> Dict[str, Any]:
    """Détails d'une structure SIAE et ses offres d'insertion."""
    return get_inclusion_job_details(siae_id)
