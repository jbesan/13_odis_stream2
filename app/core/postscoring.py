import asyncio
import logging
import os
import string
import threading
from functools import partial
from typing import Any, Callable, Dict, List, Optional
import logfire
import pandas as pd
import requests

import config as cfg
from core.enrichment_status import (
    EnrichmentStatus,
    enrichment_result,
    is_terminal_enrichment_status,
    is_terminal_refiner_status,
)
from core.models import (
    AssociationDetail,
    CommuneResult,
    InclusionServiceDetail,
    JobOfferDetail,
    SearchCriterias,
    SearchResultsData,
)
from core.hydration import (
    HydrationProvider,
    HydrationRun,
    HydrationTaskResult,
    SUCCESS_STATUSES,
    submit_background_work,
)
from core.hydration_payloads import (
    AssociationsPayload,
    ServicesPayload,
    JobsPayload,
    apply_associations,
    apply_services,
    apply_jobs,
    project_associations,
    project_services,
    project_jobs,
)
from agents.state import ODISContextBuilder, GraphState, ODISDeps
from agents.refiner import refiner_agent
from agents.job_curator import job_curator_agent
from agents import agent_config
from services import telemetry
from utils import logger as result_logger
from services import mcp_france_travail as france_travail
from utils import common
from agents.utils import (
    get_odis_bg_store,
    launch_background_city_analysis,
    rehydrate_graph_state,
    sanitize_llm_markdown,
)

logger = logging.getLogger(__name__)
ENRICHMENT_DEADLINE_SECONDS = 30


def hydration_providers(
    engine: Any,
    config: Any,
    search_results: Optional[SearchResultsData],
    *,
    is_ai_free: bool,
) -> tuple[HydrationProvider, ...]:
    """Register built-in adapters; consumers depend on the plan, not their names.

    Args:
        engine: Read-only territorial resources.
        config: Criteria captured for this execution.
        search_results: Territorial context captured for this execution.
        is_ai_free: Explicit AI policy.

    Returns:
        The ordered provider registry used by map and reduce.
    """
    criteria = (
        config.model_copy(deep=True) if isinstance(config, SearchCriterias) else config
    )
    results = (
        search_results.model_copy(deep=True)
        if isinstance(search_results, SearchResultsData)
        else search_results
    )
    slugs = [
        i.code if hasattr(i, "code") else str(i)
        for i in getattr(config, "inc_services_selection", [])
    ]
    return (
        HydrationProvider(
            "associations",
            partial(fetch_associations, engine),
            apply_associations,
            project_associations,
            ("inclusion",),
            batched=True,
        ),
        HydrationProvider(
            "services",
            partial(fetch_inclusion_services, engine, thematique_slugs=slugs or None),
            apply_services,
            project_services,
            ("inclusion",),
        ),
        HydrationProvider(
            "jobs",
            lambda codes: {
                code: fetch_jobs(code, criteria, results, is_ai_free=is_ai_free)
                for code in codes
            },
            apply_jobs,
            project_jobs,
            ("employment",),
        ),
    )


def launch_hydrations(
    engine: Any,
    config: Any,
    search_results: SearchResultsData,
    h: str,
    *,
    is_ai_free: bool,
) -> HydrationRun:
    """Prepare the complete run before dispatching any provider task.

    Args:
        engine: Territorial resources.
        config: Search criteria.
        search_results: Deterministic results.
        h: Unique execution key.
        is_ai_free: Explicit caller policy.

    Returns:
        An unstarted run, allowing dependency hooks to be installed first.
    """
    codes = [city.codgeo for city in search_results.results]
    if search_results.commune_pressentie:
        codes.append(search_results.commune_pressentie.codgeo)
    return HydrationRun(
        codes,
        hydration_providers(engine, config, search_results, is_ai_free=is_ai_free),
        get_odis_bg_store(),
        h,
        timeout_seconds=ENRICHMENT_DEADLINE_SECONDS,
    )


def launch_background_association_enrichment(
    engine: Any, codgeos: List[str], hash_val: str
) -> None:
    """Run the association adapter alone for legacy callers.

    Args:
        engine: Association service owner.
        codgeos: Target codes.
        hash_val: Background key.
    """
    provider = HydrationProvider(
        "associations",
        partial(fetch_associations, engine),
        apply_associations,
        project_associations,
        ("inclusion",),
        batched=True,
    )
    HydrationRun(codgeos, (provider,), get_odis_bg_store(), hash_val).start()


def launch_background_inclusion_enrichment(
    engine: Any,
    codgeos: List[str],
    hash_val: str,
    thematique_slugs: Optional[List[str]] = None,
) -> None:
    """Run the services adapter alone for legacy callers.

    Args:
        engine: Territorial resources.
        codgeos: Target codes.
        hash_val: Background key.
        thematique_slugs: Requested thematic filters.
    """
    provider = HydrationProvider(
        "services",
        partial(fetch_inclusion_services, engine, thematique_slugs=thematique_slugs),
        apply_services,
        project_services,
        ("inclusion",),
    )
    HydrationRun(codgeos, (provider,), get_odis_bg_store(), hash_val).start()


def launch_background_job_curation(
    codgeos: List[str],
    config: Any,
    hash_val: str,
    search_results: Optional[Any] = None,
    *,
    is_ai_free: Optional[bool] = None,
) -> None:
    """Run the jobs adapter alone for legacy callers.

    Args:
        codgeos: Target codes.
        config: Search criteria or legacy ROME lists.
        hash_val: Background key.
        search_results: Territorial context.
        is_ai_free: Caller policy, resolved before workers start.
    """
    ai_free = cfg.is_ai_free_mode() if is_ai_free is None else is_ai_free
    provider = HydrationProvider(
        "jobs",
        lambda codes: {
            code: fetch_jobs(code, config, search_results, is_ai_free=ai_free)
            for code in codes
        },
        apply_jobs,
        project_jobs,
        ("employment",),
    )
    HydrationRun(codgeos, (provider,), get_odis_bg_store(), hash_val).start()


def launch_background_refining(
    search_criterias: Any,
    results_dict_ignored: dict,
    hash_val: str,
    top_cities: Optional[list] = None,
    current_geo: Optional[dict] = None,
    commune_pressentie: Optional[dict] = None,
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
    on_terminal: Optional[Callable[[], None]] = None,
) -> None:
    """Run a bounded refiner stage, then notify dependent analysis steps.

    Args:
        search_criterias: Criteria captured by the caller.
        results_dict_ignored: Legacy argument, unused.
        hash_val: Isolated execution key.
        top_cities: Serialized city inputs.
        current_geo: Serialized current commune.
        commune_pressentie: Serialized preferred commune.
        interaction_id: Telemetry correlation identifier.
        username: Authenticated caller.
        on_terminal: Dependency notification, called exactly once on completion.
    """
    store = get_odis_bg_store()
    entry = store.setdefault(hash_val, {})
    entry["status_refiner"] = "running"
    context = logfire.get_context()
    lock = threading.Lock()
    closed = False

    def publish(payload: dict[str, Any]) -> None:
        """Publish one final result; ignore superseded and late completions.

        Args:
            payload: Final refiner status and optional content.
        """
        nonlocal closed
        with lock:
            if closed or store.get(hash_val) is not entry:
                return
            closed = True
            entry.update(payload)
            timer.cancel()
        if on_terminal is not None:
            on_terminal()

    timer = threading.Timer(
        ENRICHMENT_DEADLINE_SECONDS,
        lambda: publish(
            {
                "status_refiner": "timeout",
                "pitches_error": "Refiner deadline exceeded",
            }
        ),
    )
    timer.daemon = True
    timer.start()

    @logfire.instrument("Background Refiner")
    def work() -> None:
        """Compute the briefing without accessing Streamlit or shared models."""
        with lock:
            if closed or store.get(hash_val) is not entry:
                return
        try:
            logfire.attach_context(context)
            client = agent_config.get_gemini_client(attempts=2)
            state = rehydrate_graph_state(
                {
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
            )
            deps = ODISDeps(state=state, client=client)
            model = agent_config.get_p_model("refiner", client=client)

            async def run_agent() -> Any:
                """Execute the refiner under its transport cancellation deadline."""
                return await asyncio.wait_for(
                    refiner_agent.run(
                        "Génère le briefing du dossier et les explications des résultats.",
                        deps=deps,
                        model=model,
                    ),
                    timeout=ENRICHMENT_DEADLINE_SECONDS,
                )

            with asyncio.Runner() as runner:
                result = runner.run(run_agent()).output
            publish(
                {
                    "status_refiner": "done",
                    "odis_brief": sanitize_llm_markdown(result.odis_brief),
                    "pitches": {
                        "global": sanitize_llm_markdown(result.global_pitch),
                        "pitches": {
                            str(p.codgeo).strip(): sanitize_llm_markdown(p.pitch)
                            for p in result.pitches_per_city
                        },
                    },
                }
            )
        except Exception as exc:
            logger.exception("Background refiner failed")
            publish(
                {
                    "status_refiner": "timeout"
                    if isinstance(exc, TimeoutError)
                    else "error",
                    "pitches_error": "Refiner unavailable",
                }
            )

    try:
        submit_background_work(work)
    except RuntimeError:
        logger.exception("Refiner executor unavailable")
        publish(
            {"status_refiner": "error", "pitches_error": "Refiner executor unavailable"}
        )


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


def fetch_associations(
    engine: Any, codgeos: tuple[str, ...]
) -> dict[str, HydrationTaskResult]:
    """Fetch one association batch and validate individual commune results.

    Args:
        engine: Association service owner.
        codgeos: Deduplicated target codes.

    Returns:
        Typed outcomes for the batch.
    """
    if not engine.rna_rag_service:
        logger.warning("Association service is not configured")
        return {
            code: HydrationTaskResult(
                status=EnrichmentStatus.NOT_CONFIGURED,
                error_code="association_service_not_configured",
            )
            for code in codgeos
        }
    data = prefetch_associations(engine, list(codgeos))
    return {
        code: HydrationTaskResult(
            status=EnrichmentStatus.SUCCESS_NONEMPTY
            if data.get(code, {}).get("refugee") or data.get(code, {}).get("inclusion")
            else EnrichmentStatus.SUCCESS_EMPTY,
            payload=AssociationsPayload.model_validate(data.get(code, {})),
        )
        for code in codgeos
    }


def fetch_inclusion_services(
    engine: Any,
    codgeos: tuple[str, ...],
    thematique_slugs: Optional[List[str]] = None,
) -> dict[str, HydrationTaskResult]:
    """Fetch and validate service outcomes without publishing to the UI store.

    Args:
        engine: Territorial data and thematic index.
        codgeos: Communes assigned to this task.
        thematique_slugs: Requested thematic filters.

    Returns:
        Typed terminal results for each commune.
    """
    api_key = os.getenv("DATA_INCLUSION_API_KEY")
    if not api_key:
        logger.warning("Data Inclusion credentials are not configured")
        return {
            code: HydrationTaskResult(
                status=EnrichmentStatus.NOT_CONFIGURED,
                error_code="missing_data_inclusion_credentials",
            )
            for code in codgeos
        }
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
                commune_nom = struct_obj.get("commune") or service.get("commune") or ""
                code_postal = (
                    struct_obj.get("code_postal") or service.get("code_postal") or ""
                )
                struct_code_insee = (
                    struct_obj.get("code_insee") or service.get("code_insee") or ""
                )

                # Filter 1: Broad diffusion zones exclusion (keep local: commune, epci, or None)
                zone_type = (service.get("zone_diffusion_type") or "").strip().lower()
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

    return {
        code: HydrationTaskResult(
            status=item["status"],
            error_code=item.get("error_code"),
            payload=ServicesPayload(services=enrichment_data[code])
            if code in enrichment_data
            else None,
        )
        for code, item in statuses.items()
    }


def _curate_jobs_with_agent(
    jobs: List[Dict[str, Any]],
    profile_brief: str,
    notes_qualitatives: List[str],
    target_city: Optional[Any] = None,
    is_ai_free: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Curates a list of job offers using job_curator_agent based on candidate context.

    Args:
        jobs: List of job offer details dictionaries.
        profile_brief: Narrative summary of candidate's situation.
        notes_qualitatives: List of qualitative project notes.
        target_city: Optional CommuneResult representing the city ciblée.
        is_ai_free: Optional bool indicating if AI-free mode is active.

    Returns:
        List of curated job offer details dictionaries (top 10 in AI-free mode, top 5 otherwise).
    """
    ai_free = cfg.is_ai_free_mode() if is_ai_free is None else is_ai_free
    if ai_free:
        return jobs[:10]
    try:
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

        client = agent_config.get_gemini_client(attempts=1)
        model = agent_config.get_p_model("job_curator", client=client)

        # Construct a lightweight GraphState to carry context
        state = GraphState()
        state.odis_brief = profile_brief
        state.focus_city = target_city
        state.search_criteria = SearchCriterias(notes_qualitatives=notes_qualitatives)

        deps = ODISDeps(state=state, client=client)
        prompt = f"Voici la liste des offres d'emploi récupérées à trier et curer :\n\n{jobs_list_str}"

        # Run the curator agent synchronously within the background thread
        with asyncio.Runner() as runner:
            result = runner.run(
                job_curator_agent.run(
                    prompt,
                    deps=deps,
                    model=model,
                    model_settings=agent_config.get_model_settings("job_curator"),
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


def fetch_jobs(
    cg: str,
    config: Any,
    search_results: Optional[Any] = None,
    *,
    is_ai_free: bool,
) -> HydrationTaskResult:
    """Fetch and curate one commune without mutating shared state.

    Args:
        cg: Target commune code.
        config: Frozen criteria or legacy ROME-code lists.
        search_results: Frozen territorial context for curation.
        is_ai_free: Explicit caller policy.

    Returns:
        Validated job data and terminal provider status.
    """
    if isinstance(config, SearchCriterias):
        codes_metiers = config.codes_metiers

        # Build candidate profile summary directly using the metadata-driven odis_visibility system

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
                res = france_travail._search_job_offers_logic(
                    rome=rome,
                    location=cg,
                    distance=10,
                    sort=2,
                    range_start=0,
                    range_end=9,
                    rome_label=rome_label,
                )
                if res.get("status") == EnrichmentStatus.ERROR.value:
                    failed_queries.append(res.get("error_code", "provider_error"))
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
                        "location_insee": o.get("lieuTravail", {}).get("codeINSEE")
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
        if is_ai_free:
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
                is_ai_free=is_ai_free,
            )

        city_results.append(curated_jobs)

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

    return HydrationTaskResult(
        status=status,
        error_code=failed_queries[0] if failed_queries else None,
        payload=JobsPayload(jobs=city_results, total=api_total_count)
        if status in SUCCESS_STATUSES
        else None,
    )


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
                result_logger.log_search_results(
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
                telemetry.log_search_complete(
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


class PostScoringRun:
    """Join hydration publication and refiner completion before optional analysis."""

    def __init__(
        self,
        hydration: HydrationRun,
        config: SearchCriterias,
        results: SearchResultsData,
        *,
        auto_analysis: bool,
        interaction_id: str,
        username: str,
        org_id: str,
    ) -> None:
        """Capture execution inputs and initialize the optional analysis stage.

        Args:
            hydration: Planned provider execution.
            config: Search criteria, copied for asynchronous use.
            results: Deterministic results, copied for asynchronous use.
            auto_analysis: Whether to dispatch the top-five analysis stage.
            interaction_id: Telemetry correlation identifier.
            username: Authenticated caller.
            org_id: Authenticated organization.
        """
        self.hydration = hydration
        self.config = config.model_copy(deep=True)
        self.results = results.model_copy(deep=True)
        self._lock = threading.RLock()
        self.context = dict(
            interaction_id=interaction_id, username=username, organization_id=org_id
        )
        self.steps = {
            city.codgeo: {"status": "waiting" if auto_analysis else "skipped"}
            for city in self.results.results[:5]
        }
        hydration.store[hydration.key]["auto_analysis_steps"] = self.steps

    def advance(self) -> None:
        """Dispatch each eligible city once, after both prerequisite stages."""
        with self._lock:
            bg = self.hydration.store.get(self.hydration.key, {})
            if bg.get("hydration_run") is not self.hydration:
                return
            if not is_terminal_refiner_status(bg.get("status_refiner")):
                return
            for city in self.results.results[:5]:
                step = self.steps[city.codgeo]
                if step["status"] != "waiting" or not self.hydration.ready(city.codgeo):
                    continue
                # Reduce into private input models; UI publication is independent.
                try:
                    self.hydration.reduce_into(city)
                except Exception:
                    logger.exception(
                        "Automatic analysis blocked by hydration publication: %s",
                        city.codgeo,
                    )
                    step["status"] = "error"
                    continue
                if not city.commune_results_hydrated:
                    continue
                sync_search_results_data(
                    self.results,
                    {
                        "pitches": bg.get("pitches", {}),
                        "odis_brief": bg.get("odis_brief", ""),
                    },
                    self.config,
                )
                step["status"] = "dispatched"
                step["task_key"] = f"analysis_{self.hydration.key}_{city.codgeo}"
                try:
                    launch_background_city_analysis(
                        nom=city.name,
                        codgeo=city.codgeo,
                        search_criterias=self.config,
                        search_results=self.results,
                        h=self.hydration.key,
                        trigger="post_scoring_auto",
                        **self.context,
                    )
                except Exception:
                    logger.exception(
                        "Automatic city analysis dispatch failed: %s", city.codgeo
                    )
                    step["status"] = "error"


def launch_post_scoring_tasks(
    engine: Any,
    config: SearchCriterias,
    search_results: SearchResultsData,
    h: str,
    *,
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
    org_id: Optional[str] = None,
    is_ai_free: Optional[bool] = None,
) -> None:
    """Run the postscoring DAG: providers and refiner, then optional city analysis.

    Args:
        engine: Read-only territorial resources.
        config: Search criteria.
        search_results: Deterministic results.
        h: Isolated execution key.
        interaction_id: Telemetry identifier resolved by the controller.
        username: Authenticated caller.
        org_id: Authenticated organization.
        is_ai_free: Explicit AI policy, resolved on the caller thread.
    """
    ai_free = cfg.is_ai_free_mode() if is_ai_free is None else is_ai_free
    run = launch_hydrations(engine, config, search_results, h, is_ai_free=ai_free)
    store = get_odis_bg_store()
    entry = store[h]
    entry["status_refiner"] = "running"
    coordinator = PostScoringRun(
        run,
        config,
        search_results,
        auto_analysis=not ai_free and cfg.is_auto_analyse_top_cities_enabled(),
        interaction_id=interaction_id or "unknown",
        username=username or "unknown",
        org_id=org_id or "unknown",
    )
    run.on_change = coordinator.advance
    if ai_free:
        cities = list(search_results.results)
        if search_results.commune_pressentie:
            cities.append(search_results.commune_pressentie)
        entry["pitches"] = {
            "global": "",
            "pitches": {city.codgeo: generate_static_pitch(city) for city in cities},
        }
        entry["odis_brief"] = ""
        entry["status_refiner"] = "done"
    else:
        launch_background_refining(
            config.model_copy(deep=True),
            {},
            h,
            top_cities=[
                city.model_dump(mode="json") for city in search_results.results
            ],
            current_geo=search_results.current_geo.model_dump(mode="json")
            if search_results.current_geo
            else None,
            commune_pressentie=search_results.commune_pressentie.model_dump(mode="json")
            if search_results.commune_pressentie
            else None,
            interaction_id=interaction_id,
            username=username,
            on_terminal=coordinator.advance,
        )
    run.start()
    coordinator.advance()
    launch_background_audit_log(
        config,
        search_results,
        h,
        interaction_id=interaction_id,
        username=username,
        org_id=org_id,
    )


def sync_commune_data(commune: CommuneResult, bg_res: Optional[dict[str, Any]]) -> None:
    """Join/reduce a live run, or read a legacy result without an explicit plan.

    Args:
        commune: Domain model owned by the caller.
        bg_res: Background execution entry.
    """
    if not isinstance(bg_res, dict):
        return
    run = bg_res.get("hydration_run")
    if isinstance(run, HydrationRun):
        run.reduce_into(commune)
        # Refiner output has its own dependency and never gates data publication.
        _sync_legacy_commune_data(commune, {"pitches": bg_res.get("pitches", {})})
    else:
        _sync_legacy_commune_data(commune, bg_res)


def _sync_legacy_commune_data(
    commune: CommuneResult, bg_res: Optional[dict[str, Any]]
) -> None:
    """Syncs enrichment results and pitch from the background store into a CommuneResult model.

    Args:
        commune: The commune result domain model to hydrate.
        bg_res: The background execution store dictionary for this search run.
    """
    if not isinstance(bg_res, dict):
        return

    # Workers replace per-city records. Keep the records observed for this pass
    # so a completion during validation cannot mark uncopied data as hydrated.
    bg_res = {
        key: value.copy() if isinstance(value, dict) else value
        for key, value in bg_res.copy().items()
    }

    # 1. Sync Enrichment (Associations)
    if "enrichment" in bg_res:
        enrich_data = bg_res["enrichment"].get(str(commune.codgeo))
        if enrich_data and not commune.inclusion.asso_inclusion_list_by_cat:
            logger.debug("✨ [SYNC] Associations sync for %s", commune.codgeo)
            inc_data = commune.inclusion
            inc_data.asso_refugee_list = [
                AssociationDetail.model_validate(a)
                for a in enrich_data.get("refugee", [])
            ]
            inc_data.asso_refugee_count = len(inc_data.asso_refugee_list)

            raw_inclusion = enrich_data.get("inclusion", {})
            inc_data.asso_inclusion_list_by_cat = {
                cat: [AssociationDetail.model_validate(a) for a in asso_list]
                for cat, asso_list in raw_inclusion.items()
            }
            inc_data.asso_inclusion_count = sum(
                len(asso_list)
                for asso_list in inc_data.asso_inclusion_list_by_cat.values()
            )

    # 1b. Sync Enrichment (Job Offers)
    if "jobs_enrichment" in bg_res:
        jobs_city_data = bg_res["jobs_enrichment"].get(str(commune.codgeo))
        if (
            jobs_city_data
            and jobs_city_data.get("status")
            in {
                EnrichmentStatus.SUCCESS_NONEMPTY.value,
                EnrichmentStatus.SUCCESS_EMPTY.value,
                EnrichmentStatus.PARTIAL.value,
            }
            and not commune.employment.matching_job_offers
        ):
            logger.debug("✨ [SYNC] Jobs sync for %s", commune.codgeo)
            emp_data = commune.employment

            raw_jobs = jobs_city_data.get("jobs", [])
            emp_data.matching_job_offers = [
                [JobOfferDetail.model_validate(o) for o in adult_list]
                for adult_list in raw_jobs
            ]
            if "total" in jobs_city_data:
                emp_data.standard_jobs_matching_total = jobs_city_data["total"]

    # 1c. Sync Enrichment (Inclusion Services)
    if "inclusion_services_enrichment" in bg_res:
        incl_services_data = bg_res["inclusion_services_enrichment"].get(
            str(commune.codgeo)
        )
        if incl_services_data and not commune.inclusion.services_detailed:
            logger.debug("✨ [SYNC] Inclusion services sync for %s", commune.codgeo)
            inc_data = commune.inclusion
            inc_data.services_detailed = {
                cat: [InclusionServiceDetail.model_validate(s) for s in svc_list]
                for cat, svc_list in incl_services_data.items()
            }

    # 2. Sync Pitch for this specific commune
    if "pitches" in bg_res:
        pitches_data = bg_res["pitches"]
        if isinstance(pitches_data, dict) and "pitches" in pitches_data:
            city_pitches = pitches_data["pitches"]
            if isinstance(city_pitches, dict):
                cg = str(commune.codgeo).strip()
                cname = commune.name.lower().strip() if commune.name else ""
                pitch_for_city = (
                    city_pitches.get(cg)
                    or city_pitches.get(cg.zfill(5))
                    or city_pitches.get(cg.lstrip("0"))
                    or city_pitches.get(cname)
                    or next(
                        (
                            v
                            for k, v in city_pitches.items()
                            if k.lower().strip() == cname
                        ),
                        None,
                    )
                )
                if pitch_for_city and not commune.refiner_pitch:
                    logger.debug("✨ [SYNC] Pitch sync for %s", commune.codgeo)
                    commune.refiner_pitch = pitch_for_city

    # 3. Mark commune hydrated when all post-scoring tasks reach terminal state
    if not getattr(commune, "commune_results_hydrated", False):
        codgeo_str = str(commune.codgeo)
        jobs_status = (
            bg_res.get("jobs_enrichment", {}).get(codgeo_str, {}).get("status")
        )
        assos_status = (
            bg_res.get("association_enrichment_status", {})
            .get(codgeo_str, {})
            .get("status")
        )
        inc_status = (
            bg_res.get("inclusion_services_status", {})
            .get(codgeo_str, {})
            .get("status")
        )
        if inc_status is None:
            inc_status = (
                bg_res.get("inclusion_enrichment_status", {})
                .get(codgeo_str, {})
                .get("status")
            )

        jobs_done = is_terminal_enrichment_status(jobs_status)
        assos_done = is_terminal_enrichment_status(assos_status)
        # A legacy entry is complete only when every planned provider has
        # published a terminal status. Missing inclusion status used to be
        # treated as success, which could expose a partially copied model.
        inc_done = is_terminal_enrichment_status(inc_status)

        if jobs_done and assos_done and inc_done:
            commune.commune_results_hydrated = True
            logger.debug("✨ [SYNC] Commune %s marked hydrated", commune.codgeo)


def sync_search_results_data(
    search_results: SearchResultsData,
    bg_res: Optional[dict[str, Any]],
    config: Optional[SearchCriterias] = None,
) -> None:
    """Syncs background post-scoring results across all communes, global pitch, and config briefing.

    Args:
        search_results: The deterministic search results container to hydrate.
        bg_res: The background execution store dictionary for this search run.
        config: Optional search criteria model to hydrate with refined briefing if present.
    """
    if not isinstance(bg_res, dict):
        return

    # Sync all candidate cities
    for commune in search_results.results:
        sync_commune_data(commune, bg_res)

    # Sync commune pressentie if present
    if search_results.commune_pressentie:
        sync_commune_data(search_results.commune_pressentie, bg_res)

    # Sync Global Pitch
    if "pitches" in bg_res:
        pitches_data = bg_res["pitches"]
        if isinstance(pitches_data, dict) and "global" in pitches_data:
            if not search_results.global_pitch:
                search_results.global_pitch = pitches_data["global"]

    # Sync Unified Briefing
    if "odis_brief" in bg_res and config is not None:
        brief_val = bg_res["odis_brief"]
        if brief_val and config.odis_brief != brief_val:
            logger.debug("✨ [SYNC] Unified Briefing sync")
            config.odis_brief = brief_val
