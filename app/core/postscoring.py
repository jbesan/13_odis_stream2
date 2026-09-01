import string
from typing import Any, Dict, List, Optional
import pandas as pd
import streamlit as st
import threading
import logging
import logfire

import config as cfg
from core.enrichment_status import EnrichmentStatus, enrichment_result
from core.models import CommuneResult
from agents.utils import (
    get_odis_bg_store,
    launch_background_city_analysis,
    rehydrate_graph_state,
    sanitize_llm_markdown,
)

logger = logging.getLogger(__name__)
ENRICHMENT_DEADLINE_SECONDS = 30


def _schedule_enrichment_deadline(
    results_store: dict, hash_val: str, status_key: str, codgeos: List[str]
) -> None:
    """Ensure a stalled best-effort task reaches an honest terminal state."""

    def mark_timeout() -> None:
        current = results_store.get(hash_val, {})
        if not isinstance(current, dict):
            return
        statuses = current.get(status_key, {})
        if not isinstance(statuses, dict):
            return
        changed = False
        for codgeo in codgeos:
            item = statuses.get(str(codgeo), {})
            if not item or item.get("status") == EnrichmentStatus.PENDING.value:
                statuses[str(codgeo)] = enrichment_result(
                    EnrichmentStatus.TIMEOUT,
                    error_code="deadline_exceeded",
                    retryable=True,
                )
                changed = True
        if changed:
            current[status_key] = statuses
            results_store[hash_val] = current

    timer = threading.Timer(ENRICHMENT_DEADLINE_SECONDS, mark_timeout)
    timer.daemon = True
    timer.start()


def launch_background_refining(
    search_criterias: Any,
    results_dict_ignored: dict,
    hash_val: str,
    top_cities: Optional[list] = None,
    current_geo: Optional[dict] = None,
    commune_pressentie: Optional[dict] = None,
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
):
    """
    Launches a background thread to generate the REFINER AI briefing and pitches.
    Stores the result in the cached global store.
    """
    # Get the store here (main thread) to ensure it's initialized in the cache
    store = get_odis_bg_store()

    # Capture Logfire context to propagate it to the background thread
    context = logfire.get_context()

    @logfire.instrument("Background Refiner: {hash_val}")
    def bg_refiner_task(results_store: dict, hash_val: str):
        # Attach the context from the main thread
        logfire.attach_context(context)

        logfire.info(
            "Refiner background task started for hash: {search_hash}",
            search_hash=hash_val,
        )
        import asyncio
        from agents.state import ODISDeps
        from agents.refiner import refiner_agent
        from agents.agent_config import get_p_model

        try:
            logging.debug(f"🚀 [BG] Starting background refiner for hash {hash_val}")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            from agents.agent_config import get_gemini_client

            client = get_gemini_client(attempts=2)

            # 3. Unified State Rehydration
            input_data = {
                "search_criteria": search_criterias,
                "search_results": {
                    "search_hash": hash_val,
                    "results": top_cities,
                    "current_geo": current_geo
                    or (top_cities[0] if top_cities else None),
                    "commune_pressentie": commune_pressentie,
                }
                if top_cities
                else None,
                "execution_mode": "full_analysis",
                "interaction_id": interaction_id or "unknown",
                "username": username or "unknown",
            }

            state = rehydrate_graph_state(input_data)
            logging.debug(
                f"🔍 [REFINER-DEBUG] commune_pressentie in input_data: {commune_pressentie is not None}"
            )
            logging.debug(
                f"🔍 [REFINER-DEBUG] commune_pressentie in rehydrated state: {state.search_results.commune_pressentie is not None if state.search_results else False}"
            )
            deps = ODISDeps(state=state, client=client)
            model = get_p_model("refiner", client=client)

            async def run_agent():
                logging.debug(f"🚀 [BG] Calling refiner_agent.run for hash {hash_val}")
                return await refiner_agent.run(
                    "Génère le briefing du dossier et les explications des résultats.",
                    deps=deps,
                    model=model,
                )

            try:
                result_run = loop.run_until_complete(run_agent())
                response_obj = result_run.output
                logging.debug(
                    f"🚀 [BG] Refiner Agent call successful for hash {hash_val}"
                )

                pitches_dict = {
                    "global": sanitize_llm_markdown(response_obj.global_pitch),
                    "pitches": {
                        str(p.codgeo).strip(): sanitize_llm_markdown(p.pitch)
                        for p in response_obj.pitches_per_city
                    },
                }

                # Harmonized storage: merge with existing results if any
                current_val = results_store.get(hash_val, {})
                if not isinstance(current_val, dict):
                    current_val = {}
                current_val["pitches"] = pitches_dict
                current_val["odis_brief"] = sanitize_llm_markdown(
                    response_obj.odis_brief
                )
                current_val["status_refiner"] = "done"
                results_store[hash_val] = current_val

                logging.debug(
                    f"✅ [BG] Background Refiner fully finished for hash {hash_val}"
                )
            except Exception as e:
                logging.error(
                    f"❌ [BG] Background Refiner Error for hash {hash_val}: {e}"
                )
                current_val = results_store.get(hash_val, {})
                if not isinstance(current_val, dict):
                    current_val = {}
                current_val["pitches_error"] = f"⚠️ L'analyse IA a échoué: {e}"
                current_val["status_refiner"] = "error"
                results_store[hash_val] = current_val
        except Exception as global_e:
            logging.error(
                f"❌ [BG] Background Refiner Setup Error for hash {hash_val}: {global_e}"
            )
            current_val = results_store.get(hash_val, {})
            if not isinstance(current_val, dict):
                current_val = {}
            current_val["pitches_error"] = (
                f"⚠️ L'analyse IA a échoué (Setup): {global_e}"
            )
            current_val["status_refiner"] = "error"
            results_store[hash_val] = current_val
        finally:
            if "loop" in locals():
                loop.close()

    # 4. Threading (Non-blocking)
    import threading

    thread = threading.Thread(target=bg_refiner_task, args=(store, hash_val))
    thread.daemon = True  # Ensure it doesn't block exit
    thread.start()


def prefetch_associations(engine: Any, codgeos: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetches association details for multiple communes.
    Updates engine._associations_cache.
    """
    if not engine.rna_rag_service or not codgeos:
        raise RuntimeError("association_service_not_configured")

    try:
        logger.info(f"📊 [PREFETCH] Fetching associations for {len(codgeos)} communes")
        all_assos = engine.rna_rag_service.get_associations_by_codgeo(codgeos)

        temp_results: Dict[str, Dict[str, Any]] = {
            cg: {"refugee": [], "inclusion": {}} for cg in codgeos
        }

        for asso in all_assos:
            codgeo = asso.get("codgeo")
            if not codgeo or codgeo not in temp_results:
                continue

            raw_code = str(asso.get("code_waldec", "")).strip()
            desc = str(asso.get("description", "")).strip()
            if desc.lower() in ["nan", "none"]:
                desc = ""
            if len(desc) > 250:
                desc = desc[:250] + "..."

            name = string.capwords(str(asso.get("name", "Inconnu")).lower())

            asso_data = {
                "id": asso.get("id", ""),
                "name": name,
                "description": desc,
                "waldec_code": raw_code,
                "waldec_label": asso.get("categorie", "Action Sociale"),
                "categorie_odis": asso.get("primary_category", ""),
                "codgeo": codgeo,
                "is_refugee_focused": bool(asso.get("is_refugee_focused", False)),
            }

            if asso_data["is_refugee_focused"]:
                temp_results[codgeo]["refugee"].append(asso_data)
            else:
                cat = asso_data["categorie_odis"] or "Inclusion"
                if cat not in temp_results[codgeo]["inclusion"]:
                    temp_results[codgeo]["inclusion"][cat] = []

                if len(temp_results[codgeo]["inclusion"][cat]) < 20:
                    temp_results[codgeo]["inclusion"][cat].append(asso_data)

        engine._associations_cache.update(temp_results)
        return temp_results

    except Exception as e:
        logger.error(f"❌ [PREFETCH] Failed associations fetch: {e}")
        raise RuntimeError("association_fetch_failed") from e


def launch_background_association_enrichment(
    engine: Any, codgeos: List[str], hash_val: str
):
    """
    Launches a background thread to fetch detailed associations for the search results.
    """
    store = get_odis_bg_store()
    current = store.get(hash_val, {})
    if not isinstance(current, dict):
        current = {}
    current["association_enrichment_status"] = {
        str(codgeo): enrichment_result(EnrichmentStatus.PENDING, attempts=0)
        for codgeo in codgeos
    }
    store[hash_val] = current

    def bg_enrichment_task(results_store: dict):
        try:
            logging.info(
                f"🚀 [ENRICH] Starting background enrichment for {len(codgeos)} communes (hash: {hash_val})"
            )

            # Use the provided engine to prefetch
            # Note: engine is likely a ScoringEngine instance
            enrichment_data = prefetch_associations(engine, codgeos)

            # Merge into harmonized storage
            current_val = results_store.get(hash_val, {})
            if not isinstance(current_val, dict):
                current_val = {}
            current_val["enrichment"] = enrichment_data
            current_val["association_enrichment_status"] = {
                str(codgeo): enrichment_result(
                    EnrichmentStatus.SUCCESS_NONEMPTY
                    if enrichment_data.get(str(codgeo), {}).get("refugee")
                    or enrichment_data.get(str(codgeo), {}).get("inclusion")
                    else EnrichmentStatus.SUCCESS_EMPTY,
                    data=enrichment_data.get(str(codgeo), {}),
                )
                for codgeo in codgeos
            }
            results_store[hash_val] = current_val

            logging.info(
                f"✅ [ENRICH] Background enrichment finished for hash {hash_val}"
            )
        except Exception as e:
            logging.error(
                f"❌ [ENRICH] Background enrichment error for {hash_val}: {e}"
            )
            current_val = results_store.get(hash_val, {})
            if not isinstance(current_val, dict):
                current_val = {}
            status = (
                EnrichmentStatus.NOT_CONFIGURED
                if str(e) == "association_service_not_configured"
                else EnrichmentStatus.ERROR
            )
            current_val["association_enrichment_status"] = {
                str(codgeo): enrichment_result(
                    status,
                    error_code=str(e),
                    retryable=status == EnrichmentStatus.ERROR,
                )
                for codgeo in codgeos
            }
            results_store[hash_val] = current_val

    thread = threading.Thread(target=bg_enrichment_task, args=(store,))
    thread.daemon = True
    thread.start()
    _schedule_enrichment_deadline(
        store, hash_val, "association_enrichment_status", codgeos
    )


def launch_background_inclusion_enrichment(
    engine: Any,
    codgeos: List[str],
    hash_val: str,
    thematique_slugs: Optional[List[str]] = None,
) -> None:
    """
    Launches a background thread to fetch detailed inclusion services for the search results from the Data Inclusion API.

    Args:
        engine: ScoringEngine instance with inclusion_services_index.
        codgeos: List of INSEE commune codes to fetch services for.
        hash_val: Search hash used as background store key.
        thematique_slugs: Optional list of Data Inclusion thematique slugs to filter the API
            response. If None or empty, fetches all services for each commune.
    """
    store = get_odis_bg_store()
    current = store.get(hash_val, {})
    if not isinstance(current, dict):
        current = {}
    current["inclusion_services_status"] = {
        str(codgeo): enrichment_result(EnrichmentStatus.PENDING, attempts=0)
        for codgeo in codgeos
    }
    store[hash_val] = current

    def bg_inclusion_enrichment_task(results_store: dict):
        import requests
        import os

        api_key = os.getenv("DATA_INCLUSION_API_KEY")
        if not api_key:
            logging.warning("⚠️ [INCLUSION-ENRICH] DATA_INCLUSION_API_KEY is not set.")
            current_val = results_store.get(hash_val, {})
            if not isinstance(current_val, dict):
                current_val = {}
            current_val["inclusion_services_status"] = {
                str(codgeo): enrichment_result(
                    EnrichmentStatus.NOT_CONFIGURED,
                    error_code="missing_data_inclusion_credentials",
                )
                for codgeo in codgeos
            }
            results_store[hash_val] = current_val
            return

        logging.info(
            f"🚀 [INCLUSION-ENRICH] Starting background services enrichment for {len(codgeos)} communes (hash: {hash_val})"
        )

        headers = {"Authorization": f"Bearer {api_key}"}
        base_url = "https://api.data.inclusion.gouv.fr/api/v1"

        enrichment_data = {}
        statuses = {}

        for codgeo in codgeos:
            try:
                # 1. Resolve mairie GPS coordinates from engine.pois (with fallback to df_all_communes centroid)
                target_lat, target_lon = None, None
                if (
                    hasattr(engine, "pois")
                    and engine.pois is not None
                    and not engine.pois.empty
                ):
                    mairie = engine.pois[
                        (engine.pois["category"] == "mairie")
                        & (engine.pois["codgeo"] == str(codgeo))
                    ]
                    if (
                        not mairie.empty
                        and "lat" in mairie.columns
                        and "lon" in mairie.columns
                    ):
                        target_lat = float(mairie.iloc[0]["lat"])
                        target_lon = float(mairie.iloc[0]["lon"])

                if (
                    target_lat is None
                    and hasattr(engine, "df_all_communes")
                    and engine.df_all_communes is not None
                    and str(codgeo) in engine.df_all_communes.index
                ):
                    row_c = engine.df_all_communes.loc[str(codgeo)]
                    if (
                        "centroid_lon" in row_c
                        and "centroid_lat" in row_c
                        and pd.notna(row_c["centroid_lon"])
                    ):
                        from utils import common
                        import config as cfg

                        c_lon, c_lat = common.project_point(
                            row_c["centroid_lon"],
                            row_c["centroid_lat"],
                            from_crs=cfg.PROJECTED_CRS,
                            to_crs="EPSG:4326",
                        )
                        target_lat, target_lon = c_lat, c_lon

                # 2. Fetch services using search endpoint (combining code_commune and GPS coordinates)
                services_params: dict = {"code_commune": codgeo, "size": 100}
                if target_lat is not None and target_lon is not None:
                    services_params["lat"] = round(target_lat, 5)
                    services_params["lon"] = round(target_lon, 5)

                if thematique_slugs:
                    services_params["thematiques"] = thematique_slugs

                r_services = requests.get(
                    f"{base_url}/search/services",
                    headers=headers,
                    params=services_params,
                    timeout=10,
                )

                if r_services.status_code != 200:
                    logging.warning(
                        f"⚠️ [INCLUSION-ENRICH] Failed to fetch services for codgeo {codgeo}: {r_services.status_code} {r_services.text[:100]}"
                    )
                    statuses[str(codgeo)] = enrichment_result(
                        EnrichmentStatus.ERROR,
                        error_code=f"http_{r_services.status_code}",
                        retryable=r_services.status_code in {429, 500, 502, 503, 504},
                    )
                    continue

                items = r_services.json().get("items", [])

                # Group by user-friendly thematic label using engine.inclusion_services_index
                grouped_services: dict[str, list] = {}
                # Deduplication key: (structure_id, nom) — avoids duplicates from same structure
                seen_keys: set[tuple] = set()
                # Only index codes matching the user's thematique selection
                active_slugs: set[str] | None = (
                    set(thematique_slugs) if thematique_slugs else None
                )

                for item_wrapper in items:
                    service = item_wrapper.get("service") or {}
                    srv_id = service.get("id") or ""
                    nom = service.get("nom") or ""
                    structure_id = service.get("structure_id") or ""

                    struct_obj = service.get("structure") or {}
                    nom_structure = (
                        struct_obj.get("nom") or service.get("nom_structure") or ""
                    )
                    presentation_structure = (
                        struct_obj.get("presentation_resumee")
                        or struct_obj.get("presentation_detail")
                        or ""
                    )
                    commune_nom = (
                        struct_obj.get("commune") or service.get("commune") or ""
                    )
                    code_postal = (
                        struct_obj.get("code_postal")
                        or service.get("code_postal")
                        or ""
                    )
                    struct_code_insee = (
                        struct_obj.get("code_insee") or service.get("code_insee") or ""
                    )

                    # Filter 1: Broad diffusion zones exclusion (keep local: commune, epci, or None)
                    zone_type = (
                        (service.get("zone_diffusion_type") or "").strip().lower()
                    )
                    if zone_type in {"departement", "region", "pays"}:
                        continue

                    # Filter 2: Max distance <= 10km (when distance is computed by API)
                    dist_val = item_wrapper.get("distance")
                    if dist_val is None:
                        dist_val = service.get("distance")
                    if dist_val is not None and dist_val > 5:
                        continue

                    # Filter 3: External CCAS exclusion (keep local CCAS, CIAS, and other structures)
                    reseaux = struct_obj.get("reseaux_porteurs") or []
                    typologie = (struct_obj.get("typologie") or "").upper()
                    is_ccas = (
                        "ccas-cias" in reseaux
                        or typologie == "CCAS"
                        or "CCAS" in nom_structure.upper()
                        or "CENTRE COMMUNAL D'ACTION SOCIALE" in nom_structure.upper()
                    )
                    is_external = bool(
                        struct_code_insee and str(struct_code_insee) != str(codgeo)
                    )
                    if is_ccas and is_external and "CIAS" not in nom_structure.upper():
                        continue

                    # Deduplication key: same structure offering same service type
                    dedup_key = (structure_id, nom.strip().lower())
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)

                    desc = service.get("description") or ""
                    if desc.lower() in ["nan", "none"]:
                        desc = ""
                    # Cap description to a reasonable length
                    if len(desc) > 250:
                        desc = desc[:250] + "..."

                    # Keep direct lien_source if populated
                    lien_source = service.get("lien_source") or ""
                    source = service.get("source") or ""

                    # Get service thematiques list
                    thematiques = service.get("thematiques") or []
                    if isinstance(thematiques, str):
                        thematiques = [thematiques]

                    # Convert each thematic code to user-friendly label using index
                    # Only process codes that match the user's selection (if filtered)
                    for code in thematiques:
                        if active_slugs and code not in active_slugs:
                            continue
                        label = code
                        try:
                            if (
                                hasattr(engine, "inclusion_services_index")
                                and engine.inclusion_services_index is not None
                                and code in engine.inclusion_services_index.index
                            ):
                                val = engine.inclusion_services_index.loc[code, "label"]
                                label = val if isinstance(val, str) else val.iloc[0]
                        except Exception as e:
                            logging.debug(f"Error mapping thematic label: {e}")

                        if label not in grouped_services:
                            grouped_services[label] = []

                        grouped_services[label].append(
                            {
                                "id": srv_id,
                                "name": nom,
                                "nom_structure": nom_structure,
                                "structure_id": structure_id,
                                "presentation_structure": presentation_structure,
                                "description": desc,
                                "lien_source": lien_source,
                                "source": source,
                                "distance_km": dist_val,
                                "commune_nom": commune_nom,
                                "code_postal": code_postal,
                            }
                        )

                # Sort each thematic category by proximity (distance_km ascending)
                for cat_label in grouped_services:
                    grouped_services[cat_label].sort(
                        key=lambda x: (
                            x.get("distance_km")
                            if x.get("distance_km") is not None
                            else 999,
                            x.get("name", ""),
                        )
                    )

                enrichment_data[str(codgeo)] = grouped_services
                statuses[str(codgeo)] = enrichment_result(
                    EnrichmentStatus.SUCCESS_NONEMPTY
                    if grouped_services
                    else EnrichmentStatus.SUCCESS_EMPTY,
                    data=grouped_services,
                )

            except Exception as e:
                logging.error(
                    f"❌ [INCLUSION-ENRICH] Error fetching services for codgeo {codgeo}: {e}"
                )
                statuses[str(codgeo)] = enrichment_result(
                    EnrichmentStatus.TIMEOUT
                    if isinstance(e, requests.Timeout)
                    else EnrichmentStatus.ERROR,
                    error_code="request_timeout"
                    if isinstance(e, requests.Timeout)
                    else "request_failed",
                    retryable=True,
                )

        # Merge into global results_store
        current_val = results_store.get(hash_val, {})
        if not isinstance(current_val, dict):
            current_val = {}
        current_val["inclusion_services_enrichment"] = enrichment_data
        current_val["inclusion_services_status"] = statuses
        results_store[hash_val] = current_val

        logging.info(
            f"✅ [INCLUSION-ENRICH] Background services enrichment finished for hash {hash_val}"
        )

    thread = threading.Thread(target=bg_inclusion_enrichment_task, args=(store,))
    thread.daemon = True
    thread.start()
    _schedule_enrichment_deadline(store, hash_val, "inclusion_services_status", codgeos)


def _curate_jobs_with_agent(
    jobs: List[Dict[str, Any]],
    profile_brief: str,
    notes_qualitatives: List[str],
    target_city: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Curates a list of job offers using job_curator_agent based on candidate context.

    Args:
        jobs: List of job offer details dictionaries.
        profile_brief: Narrative summary of candidate's situation.
        notes_qualitatives: List of qualitative project notes.
        target_city: Optional CommuneResult representing the city ciblée.

    Returns:
        List of curated job offer details dictionaries (top 10 in AI-free mode, top 5 otherwise).
    """
    if cfg.is_ai_free_mode():
        return jobs[:10]
    try:
        from agents.job_curator import job_curator_agent
        from agents.state import GraphState, ODISDeps
        from core.models import SearchCriterias
        import asyncio

        # Format jobs for the LLM prompt
        jobs_list_str = ""
        for job in jobs:
            jobs_list_str += (
                f"- ID: {job['id']}\n"
                f"  Intitulé: {job['title']}\n"
                f"  Entreprise: {job.get('company') or 'Non spécifiée'}\n"
                f"  Type de contrat: {job.get('contract_label') or job.get('contract_type') or 'N/A'}\n"
                f"  Lieu: {job.get('location') or 'Non spécifié'}\n"
                f"  Description: {job.get('description') or 'Aucune'}\n"
                f"  Salaire: {job.get('salary') or 'Non spécifié'}\n"
                f"  Expérience requise: {job.get('experience') or 'Non spécifiée'}\n"
                f"  Durée de travail: {job.get('work_duration') or 'Non spécifiée'}\n"
                f"  Date de création: {job.get('date_creation') or 'Non spécifiée'}\n\n"
            )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        from agents.agent_config import (
            get_p_model,
            get_model_settings,
            get_gemini_client,
        )

        client = get_gemini_client(attempts=1)
        model = get_p_model("job_curator", client=client)

        # Construct a lightweight GraphState to carry context
        state = GraphState()
        state.odis_brief = profile_brief
        state.focus_city = target_city
        state.search_criteria = SearchCriterias(notes_qualitatives=notes_qualitatives)

        deps = ODISDeps(state=state, client=client)
        prompt = f"Voici la liste des offres d'emploi récupérées à trier et curer :\n\n{jobs_list_str}"

        # Run the curator agent synchronously within the background thread
        result = loop.run_until_complete(
            job_curator_agent.run(
                prompt,
                deps=deps,
                model=model,
                model_settings=get_model_settings("job_curator"),
            )
        )
        selected_jobs_list = getattr(result.output, "selected_jobs", [])

        # Map selected IDs back to job dicts, keeping LLM relevance order
        curated_jobs = []
        seen_ids = set()
        for curated_item in selected_jobs_list:
            j_id = str(curated_item.job_id)
            if j_id in seen_ids:
                continue
            match = next((j for j in jobs if str(j["id"]) == j_id), None)
            if match:
                curated_jobs.append({**match, "job_brief": curated_item.job_brief})
                seen_ids.add(j_id)

        # Fill in with remaining jobs if LLM selected fewer than 5 but total is larger
        if len(curated_jobs) < 5 and len(jobs) > len(curated_jobs):
            for j in jobs:
                if len(curated_jobs) >= 5:
                    break
                j_id = str(j["id"])
                if j_id not in seen_ids:
                    curated_jobs.append({**j, "job_brief": None})
                    seen_ids.add(j_id)

        logging.debug(
            f"✅ [JOBS-CURATE] LLM successfully curated {len(curated_jobs)} jobs."
        )
        return curated_jobs[:5]
    except Exception as e:
        logging.error(
            f"❌ [JOBS-CURATE] LLM job curation failed, falling back to distance sort: {e}",
            exc_info=True,
        )
        return jobs[:5]


def launch_background_job_curation(
    codgeos: List[str], config: Any, hash_val: str, search_results: Optional[Any] = None
):
    """Launches background threads in parallel (one per target commune) to fetch and curate job offers.

    Args:
        codgeos: List of geographic INSEE codes of target communes.
        config: The SearchCriterias configuration object or a list of ROME codes for legacy compatibility.
        hash_val: Unique MD5 search criteria hash.
        search_results: Optional SearchResultsData container for target city lookup.
    """
    store = get_odis_bg_store()

    # 1. Pre-initialize the jobs_enrichment dictionary for all communes to pending state
    current_val = store.get(hash_val, {})
    if not isinstance(current_val, dict):
        current_val = {}

    from core.models import SearchCriterias

    if isinstance(config, SearchCriterias):
        codes_metiers = config.codes_metiers

        # Build candidate profile summary directly using the metadata-driven odis_visibility system
        from agents.state import ODISContextBuilder

        ctx_dict = ODISContextBuilder._auto_build_context(config, "agent_job_hunter")

        profile_parts = []
        for label, val in ctx_dict.items():
            # Skip code lists that are handled separately in query fetching
            if label in ["Métiers ciblés par adulte", "Formations ciblées"]:
                continue
            if isinstance(val, list):
                if val:
                    profile_parts.append(f"{label} : {', '.join(map(str, val))}")
            else:
                profile_parts.append(f"{label} : {val}")

        profile_brief = (
            "\n".join(profile_parts) if profile_parts else "Brief non disponible"
        )
        notes_qualitatives = config.notes_qualitatives or []
    else:
        # Legacy compatibility fallback
        codes_metiers = config
        profile_brief = ""
        notes_qualitatives = []

    # Initialize nested dict
    if "jobs_enrichment" not in current_val:
        current_val["jobs_enrichment"] = {
            str(cg): {"status": EnrichmentStatus.PENDING.value, "jobs": []}
            for cg in codgeos
        }
    else:
        # Reset specific keys to pending
        for cg in codgeos:
            current_val["jobs_enrichment"][str(cg)] = {
                "status": EnrichmentStatus.PENDING.value,
                "jobs": [],
            }
    store[hash_val] = current_val
    _schedule_enrichment_deadline(store, hash_val, "jobs_enrichment", codgeos)

    # 3. Extract unique valid ROME codes per adult
    adult_romes_list = []
    for adult_list in codes_metiers:
        adult_romes = []
        for item in adult_list:
            code = None
            label = None
            if hasattr(item, "code"):
                code = item.code
                label = item.label
            elif isinstance(item, dict) and "code" in item:
                code = item["code"]
                label = item.get("label")
            elif isinstance(item, str):
                code = item
                label = item

            if code and len(code) == 5 and code[0].isalpha() and code[1:].isdigit():
                if not any(r["code"] == code for r in adult_romes):
                    adult_romes.append({"code": code, "label": label or code})
        adult_romes_list.append(adult_romes)

    # If no ROME codes are present at all, return empty results for all communes
    if not any(adult_romes_list):
        logging.info("ℹ️ [JOBS-ENRICH] No valid ROME codes to search.")
        current_val = store.get(hash_val, {})
        if not isinstance(current_val, dict):
            current_val = {}
        current_val["jobs_enrichment"] = {
            cg: {
                **enrichment_result(EnrichmentStatus.SUCCESS_EMPTY),
                "jobs": [[] for _ in adult_romes_list],
                "total": 0,
            }
            for cg in codgeos
        }
        store[hash_val] = current_val
        return

    from services.mcp_france_travail import _search_job_offers_logic

    # Define the worker task for a single city
    def bg_jobs_enrichment_for_city_task(cg: str, results_store: dict):
        try:
            logging.debug(
                f"🚀 [JOBS-ENRICH-CITY] Starting background job enrichment for commune {cg} (hash: {hash_val})"
            )
            city_results = []
            api_total_count = 0
            failed_queries = []

            # Fetch and pool up to 10 offers per ROME code per adult
            for i, adult_romes in enumerate(adult_romes_list):
                adult_pooled_jobs = []
                for rome_entry in adult_romes:
                    rome = rome_entry["code"]
                    rome_label = rome_entry["label"]

                    try:
                        # sort=2 (distance ascending), distance=10 (radius in km)
                        res = _search_job_offers_logic(
                            rome=rome,
                            location=cg,
                            distance=10,
                            sort=2,
                            range_start=0,
                            range_end=9,
                            rome_label=rome_label,
                        )
                        if res.get("status") == EnrichmentStatus.ERROR.value:
                            failed_queries.append(
                                res.get("error_code", "provider_error")
                            )
                            continue
                        offres = res.get("offres", [])[:10]
                        api_total_count += res.get("total", 0)
                        for o in offres:
                            job_detail = {
                                "id": str(o.get("id", "")),
                                "title": str(o.get("intitule", "Poste sans titre")),
                                "company": o.get("entreprise", {}).get("nom")
                                if o.get("entreprise")
                                else None,
                                "contract_type": str(o.get("typeContrat", "")),
                                "contract_label": o.get("typeContratLibelle"),
                                "description": o.get("description_sh"),
                                "location": o.get("lieuTravail", {}).get("libelle")
                                if o.get("lieuTravail")
                                else None,
                                "location_insee": o.get("lieuTravail", {}).get(
                                    "codeINSEE"
                                )
                                if o.get("lieuTravail")
                                else None,
                                "salary": o.get("salaire", {}).get("libelle")
                                if o.get("salaire")
                                else None,
                                "url": o.get("origineOffre", {}).get("urlOrigine")
                                if o.get("origineOffre")
                                else None,
                                "rome_code": rome,
                                "rome_label": rome_label,
                                "date_creation": o.get("dateCreation"),
                                "work_duration": o.get("dureeTravailLibelle"),
                                "experience": o.get("experienceLibelle"),
                            }
                            adult_pooled_jobs.append(job_detail)
                    except Exception as e:
                        logging.warning(
                            f"⚠️ [JOBS-ENRICH-CITY] API error for {cg} ROME {rome}: {e}"
                        )
                        failed_queries.append(
                            "missing_france_travail_credentials"
                            if "Missing FRANCE_TRAVAIL" in str(e)
                            else "request_failed"
                        )

                # Apply post-curation to the pooled jobs list for this adult
                # Note: cfg.is_ai_free_mode() is checked here as an outer guard, returning 10 raw jobs directly.
                if cfg.is_ai_free_mode():
                    curated_jobs = adult_pooled_jobs[:10]
                elif len(adult_pooled_jobs) <= 5:
                    curated_jobs = adult_pooled_jobs
                else:
                    # Resolve target city CommuneResult for this specific city
                    target_city = None
                    if search_results:
                        if hasattr(search_results, "get_by_code"):
                            target_city = search_results.get_by_code(cg)
                        elif isinstance(search_results, dict):
                            results = search_results.get("results", [])
                            for r in results:
                                r_code = (
                                    r.get("codgeo")
                                    if isinstance(r, dict)
                                    else getattr(r, "codgeo", None)
                                )
                                if r_code == cg:
                                    target_city = r
                                    break
                    curated_jobs = _curate_jobs_with_agent(
                        adult_pooled_jobs,
                        profile_brief,
                        notes_qualitatives,
                        target_city=target_city,
                    )

                city_results.append(curated_jobs)

            # Atomic update to results_store nested dictionary
            current_val = results_store.get(hash_val, {})
            if isinstance(current_val, dict) and "jobs_enrichment" in current_val:
                has_jobs = any(city_results)
                if failed_queries and has_jobs:
                    status = EnrichmentStatus.PARTIAL
                elif failed_queries:
                    status = (
                        EnrichmentStatus.NOT_CONFIGURED
                        if all(
                            error == "missing_france_travail_credentials"
                            for error in failed_queries
                        )
                        else EnrichmentStatus.ERROR
                    )
                elif has_jobs:
                    status = EnrichmentStatus.SUCCESS_NONEMPTY
                else:
                    status = EnrichmentStatus.SUCCESS_EMPTY
                current_val["jobs_enrichment"][str(cg)] = {
                    **enrichment_result(
                        status,
                        error_code=failed_queries[0] if failed_queries else None,
                        retryable=bool(failed_queries),
                    ),
                    "jobs": city_results,
                    "total": api_total_count,
                }
                results_store[hash_val] = current_val

            logging.debug(
                f"✅ [JOBS-ENRICH-CITY] Background job enrichment finished for commune {cg} (hash: {hash_val})"
            )
        except Exception as e:
            logging.error(
                f"❌ [JOBS-ENRICH-CITY] Error for commune {cg}: {e}", exc_info=True
            )
            current_val = results_store.get(hash_val, {})
            if isinstance(current_val, dict) and "jobs_enrichment" in current_val:
                current_val["jobs_enrichment"][str(cg)] = {
                    **enrichment_result(
                        EnrichmentStatus.ERROR,
                        error_code="worker_failed",
                        retryable=True,
                    ),
                    "jobs": [],
                }
                results_store[hash_val] = current_val

    # 4. Spawn a concurrent thread for each target commune code
    for cg in codgeos:
        thread = threading.Thread(
            target=bg_jobs_enrichment_for_city_task, args=(str(cg), store)
        )
        thread.daemon = True
        thread.start()


def launch_background_audit_log(
    config: Any,
    search_results: Any,
    h: str,
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
    org_id: Optional[str] = None,
):
    """
    Launches a background thread to log search results to Markdown and Telemetry.
    """

    def bg_logging_task():
        try:
            logging.info(f"💾 [LOGGING] Starting background audit log for hash {h}")

            # 1. Markdown Local Logging (Dev Audit)
            try:
                from utils.logger import log_search_results

                log_search_results(
                    config,
                    search_results,
                    prefix="classic",
                    interaction_id=interaction_id,
                    username=username,
                )
            except Exception as e:
                logging.warning(f"⚠️ [LOGGING] Markdown logging failed: {e}")

            # 2. Telemetry Logging (BigQuery)
            try:
                from services.telemetry import log_search_complete

                log_search_complete(
                    config,
                    search_results,
                    source_flow="classic",
                    interaction_id=interaction_id,
                    username=username,
                    org_id=org_id,
                )
            except Exception as e:
                logging.error(
                    f"❌ [LOGGING] Telemetry logging failed for hash {h}: {e}",
                    exc_info=True,
                )

            logging.info(f"✅ [LOGGING] Background logging finished for hash {h}")
        except Exception as e:
            logging.error(
                f"❌ [LOGGING] Background logging FATAL error for {h}: {e}",
                exc_info=True,
            )

    thread = threading.Thread(target=bg_logging_task)
    thread.daemon = True
    thread.start()


from typing import Union


def generate_static_pitch(commune: Union[CommuneResult, Dict[str, Any]]) -> str:
    """Generates a static pitch list showing the top 3 contributing score indicators.

    Ranks all score details by their weighted contribution (score_normalise * relative_weight)
    and formats the top 3 as a bulleted markdown string. Used as an AI-free fallback for
    the refiner pitch.

    Args:
        commune: A CommuneResult instance or dictionary containing a populated `scores` dict.

    Returns:
        A markdown-formatted string listing the top 3 score contributors.
    """
    all_details = []
    if hasattr(commune, "scores") and commune.scores:
        for cat, details in commune.scores.items():
            for detail in details:
                if hasattr(detail, "score_normalise") and hasattr(
                    detail, "relative_weight"
                ):
                    score_norm = detail.score_normalise
                    rel_weight = detail.relative_weight
                    label = detail.label
                    valeur = detail.valeur_kpi
                    unit = detail.unit
                    score_id = detail.score_id
                    strong_point = getattr(detail, "strong_point_text", "")
                    adj = getattr(detail, "high_value_adjective", "")
                elif isinstance(detail, dict):
                    score_norm = detail.get("score_normalise", 0.0)
                    rel_weight = detail.get("relative_weight", 0.0)
                    label = detail.get("label", "")
                    valeur = detail.get("valeur_kpi")
                    unit = detail.get("unit", "")
                    score_id = detail.get("score_id", "")
                    strong_point = detail.get("strong_point_text", "")
                    adj = detail.get("high_value_adjective", "")
                else:
                    continue

                contrib = float(score_norm or 0.0) * float(rel_weight or 0.0)
                all_details.append(
                    (
                        contrib,
                        label,
                        valeur,
                        unit,
                        rel_weight,
                        score_id,
                        strong_point,
                        adj,
                    )
                )

    all_details.sort(key=lambda x: x[0], reverse=True)
    top_3 = all_details[:3]
    if not top_3:
        name = (
            getattr(commune, "name", commune.get("name", "La commune"))
            if commune
            else "La commune"
        )
        return f"{name} se distingue particulièrement sur vos critères prioritaires."

    pitch_lines = ["**Points forts du territoire :**"]
    for contrib, label, valeur, unit, rel_weight, score_id, strong_point, adj in top_3:
        val_str = str(valeur) if valeur is not None else "N/A"
        unit_str = f" {unit}" if unit and unit not in ["description", ""] else ""

        if strong_point:
            display_title = strong_point
        elif adj:
            display_title = f"{label} ({adj})"
        else:
            display_title = label

        # Clean multiline spaces
        display_title = " ".join(display_title.split())

        if score_id == "mob_gare_scaled":
            val_str = "Gare SNCF présente" if valeur == "Oui" else "Pas de gare SNCF"
            unit_str = ""
            pitch_lines.append(f"- **{display_title}** : {val_str}")
        else:
            pitch_lines.append(f"- **{display_title}** : {val_str}{unit_str}")

    return "\n".join(pitch_lines)


def launch_post_scoring_tasks(engine: Any, config: Any, search_results: Any, h: str):
    """
    Orchestrator for all background tasks triggered after scoring.
    """
    # 0. Initialize the store entry for this hash to prevent race conditions between threads
    store = get_odis_bg_store()
    if h not in store:
        store[h] = {}

    # Capture session metadata FROM THE MAIN THREAD
    try:
        from services.telemetry import get_interaction_id

        interaction_id = get_interaction_id()
        username = st.session_state.get("username", "unknown")
        org = st.session_state.get("org")
        org_id = org.id if org and hasattr(org, "id") else "unknown"
    except (AttributeError, RuntimeError) as exc:
        logger.debug(
            "st.session_state is unavailable in postscoring session capture: %s", exc
        )
        interaction_id = "unknown"
        username = "unknown"
        org_id = "unknown"
    except Exception as exc:
        logger.warning("Error capturing session metadata in postscoring: %s", exc)
        interaction_id = "unknown"
        username = "unknown"
        org_id = "unknown"

    if cfg.is_ai_free_mode():
        # Compute static pitches for results
        pitches = {}
        for c in search_results.results:
            pitch_text = generate_static_pitch(c)
            c.refiner_pitch = pitch_text
            pitches[str(c.codgeo)] = pitch_text

        store[h]["pitches"] = {"global": "", "pitches": pitches}
        store[h]["odis_brief"] = ""
        store[h]["status_refiner"] = "done"

        # Launch non-AI background hydrations
        top_cities_full = [c.model_dump(mode="json") for c in search_results.results]
        commune_pressentie_full = (
            search_results.commune_pressentie.model_dump(mode="json")
            if search_results.commune_pressentie
            else None
        )
        target_codgeos = [c["codgeo"] for c in top_cities_full]
        if commune_pressentie_full:
            target_codgeos.append(commune_pressentie_full["codgeo"])

        launch_background_association_enrichment(engine, target_codgeos, h)
        thematique_slugs = [
            i.code if hasattr(i, "code") else str(i)
            for i in getattr(config, "inc_services_selection", [])
        ]
        launch_background_inclusion_enrichment(
            engine, target_codgeos, h, thematique_slugs or None
        )
        launch_background_job_curation(target_codgeos, config, h, search_results)
        launch_background_audit_log(
            config,
            search_results,
            h,
            interaction_id=interaction_id,
            username=username,
            org_id=org_id,
        )
        return

    store[h]["status_refiner"] = "running"

    # Extract city data for Scorer Agent (using mode='json' for safe cross-thread serialization)
    top_cities_full = [c.model_dump(mode="json") for c in search_results.results]
    current_geo_full = (
        search_results.current_geo.model_dump(mode="json")
        if search_results.current_geo
        else None
    )
    commune_pressentie_full = (
        search_results.commune_pressentie.model_dump(mode="json")
        if search_results.commune_pressentie
        else None
    )

    # 3. Launch Refiner (AI Briefing & Pitch)
    launch_background_refining(
        config,
        {},
        h,
        top_cities=top_cities_full,
        current_geo=current_geo_full,
        commune_pressentie=commune_pressentie_full,
        interaction_id=interaction_id,
        username=username,
    )

    # 4. Launch Enrichment (Detailed Associations - BQ/RAG)
    target_codgeos = [c["codgeo"] for c in top_cities_full]
    if commune_pressentie_full:
        target_codgeos.append(commune_pressentie_full["codgeo"])
    launch_background_association_enrichment(engine, target_codgeos, h)
    thematique_slugs = [
        i.code if hasattr(i, "code") else str(i)
        for i in getattr(config, "inc_services_selection", [])
    ]
    launch_background_inclusion_enrichment(
        engine, target_codgeos, h, thematique_slugs or None
    )

    # 4b. Launch Employment Enrichment (Detailed Jobs - France Travail)
    launch_background_job_curation(target_codgeos, config, h, search_results)

    # 4c. Launch Automated City Analysis (if enabled)
    if not cfg.is_ai_free_mode() and cfg.is_auto_analyse_top_cities_enabled():
        for city in (getattr(search_results, "results", []) or [])[:5]:
            nom = getattr(city, "name", None) or (
                city.get("name") if isinstance(city, dict) else ""
            )
            codgeo = getattr(city, "codgeo", None) or (
                city.get("codgeo") if isinstance(city, dict) else ""
            )
            if nom and codgeo:
                launch_background_city_analysis(
                    nom=nom,
                    codgeo=str(codgeo),
                    search_criterias=config,
                    search_results=search_results,
                    h=h,
                    interaction_id=interaction_id,
                    username=username,
                    organization_id=org_id,
                    trigger="post_scoring_auto",
                )

    # 5. Launch Logging & Telemetry
    launch_background_audit_log(
        config,
        search_results,
        h,
        interaction_id=interaction_id,
        username=username,
        org_id=org_id,
    )
