import json
import logging
from typing import List, Dict, Any, Optional, Annotated, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from google import genai
import operator
from core.models import SearchCriterias, SearchResultsData, CommuneResult

logger = logging.getLogger(__name__)

class UsageStats(BaseModel):
    """Cumulative usage statistics for the graph."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    breakdown: Dict[str, Dict[str, Any]] = Field(default_factory=dict) # {node_name: {model_id, in_tokens, out_tokens, cost, ...}}

    model_config = ConfigDict(revalidate_instances='never')

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        return data

def add_usage(left: UsageStats, right: Any) -> UsageStats:
    # left and right are usually UsageStats or dicts
    if right is None:
        return left
    if isinstance(right, dict):
        right = UsageStats(**right)
        
    new_breakdown = getattr(left, 'breakdown', {}).copy()
    right_breakdown = getattr(right, 'breakdown', {})
    
    if right_breakdown:
        for node, metrics in right_breakdown.items():
            if node in new_breakdown:
                existing = new_breakdown[node]
                new_breakdown[node] = {
                    "model": metrics.get("model", existing.get("model")),
                    "input": existing.get("input", 0) + metrics.get("input", 0),
                    "output": existing.get("output", 0) + metrics.get("output", 0),
                    "total": existing.get("total", 0) + metrics.get("total", 0),
                    "cost": existing.get("cost", 0.0) + metrics.get("cost", 0.0)
                }
            else:
                new_breakdown[node] = metrics
    
    return UsageStats(
        input_tokens=left.input_tokens + (right.input_tokens or 0),
        output_tokens=left.output_tokens + (right.output_tokens or 0),
        total_tokens=left.total_tokens + (right.total_tokens or 0),
        cost_usd=left.cost_usd + (right.cost_usd or 0.0),
        breakdown=new_breakdown
    )

def merge_search_criteria(left: SearchCriterias, right: Any) -> SearchCriterias:
    """Reducer to merge search criteria updates."""
    # right is usually a dict or an ODISGraphState update
    if not right:
        return left
    
    # We want to keep existing values that are not in the update (right)
    # We dump CURRENT with exclude_unset=False to get defaults too
    try:
        current_data = left.model_dump() if left else {}
    except AttributeError as e:
        logger.error(f"Failed to dump left criteria in reducer: {e}. left type: {type(left)}")
        current_data = left if isinstance(left, dict) else {}
    except Exception as e:
        logger.error(f"Unexpected error in merge_search_criteria: {e}")
        raise e
    
    if isinstance(right, dict):
        current_data.update(right)
    elif isinstance(right, SearchCriterias):
        # We only update with what was EXPLICITLY set in the new model
        update_data = right.model_dump(exclude_unset=True)
        current_data.update(update_data)
        
    return SearchCriterias(**current_data)


def take_latest_hash(left: Optional[str], right: Any) -> Optional[str]:
    """
    Ensures that the state always uses the LATEST hash provided.
    Across turns, nodes compute a NEW hash if criteria have changed.
    Within a turn, parallel nodes return the SAME next hash.
    """
    return right if right is not None else left

def compute_criteria_hash(criteria: SearchCriterias) -> str:
    """Helper to compute a stable hash for search criteria."""
    if not criteria:
        return ""
    return criteria.compute_hash()

def merge_search_results(left: Optional[SearchResultsData], right: Any) -> Optional[SearchResultsData]:
    """Reducer to merge search results and expert artifacts."""
    # right is usually a dict or an ODISGraphState update
    
    if right is None:
        return left
    
    if left is None:
        if isinstance(right, dict):
            return SearchResultsData(**right)
        return right

    if isinstance(right, SearchResultsData):
        right = right.model_dump(exclude_unset=True)
    
    if not isinstance(right, dict):
        return left

    try:
        new_data = left.model_dump()
    except AttributeError as e:
        logger.error(f"Failed to dump left model in reducer: {e}. left type: {type(left)}")
        new_data = left if isinstance(left, dict) else {}
    except Exception as e:
        logger.error(f"Unexpected error in merge_search_results: {e}")
        raise e
    
    # 1. Merge results list by codgeo or name (robust matching)
    # NOTE: Expert nodes emit partial dicts (codgeo + expert_analysis only).
    # We MUST NOT coerce to CommuneResult here — required fields (population,
    # global_score) will be absent. Work entirely at dict level.
    if "results" in right and right["results"]:
        from utils.common import normalize_text

        # Build lookup maps for existing results
        existing_results = {str(r["codgeo"]): i for i, r in enumerate(new_data.get("results", []))}
        existing_names = {normalize_text(str(r.get("name", ""))): i for i, r in enumerate(new_data.get("results", []))}

        for new_res in right["results"]:
            # Normalise: accept both dicts and CommuneResult objects
            if isinstance(new_res, CommuneResult):
                new_res = new_res.model_dump()
            elif not isinstance(new_res, dict):
                logger.warning(f"Unexpected item type in reducer results: {type(new_res)}")
                continue

            cg = str(new_res.get("codgeo", ""))
            name_norm = normalize_text(str(new_res.get("name", "")))

            # Identify target index (Code first, then Name fallback)
            idx = existing_results.get(cg)
            if idx is None:
                idx = existing_names.get(name_norm)

            if idx is not None:
                target = new_data["results"][idx]

                # Merge expert_analysis
                if "expert_analysis" in new_res and new_res["expert_analysis"]:
                    if "expert_analysis" not in target or target["expert_analysis"] is None:
                        target["expert_analysis"] = {}
                    target["expert_analysis"].update(new_res["expert_analysis"])

                # Merge odis_synthesis (append, deduplicate)
                if "odis_synthesis" in new_res and new_res["odis_synthesis"]:
                    existing_val = target.get("odis_synthesis", [])
                    if isinstance(existing_val, str):
                        existing_list = [{"role": "assistant", "content": existing_val}] if existing_val else []
                    else:
                        existing_list = list(existing_val)
                    new_msgs = new_res["odis_synthesis"] if isinstance(new_res["odis_synthesis"], list) else [{"role": "assistant", "content": new_res["odis_synthesis"]}]
                    seen_content = {m.get("content") for m in existing_list if isinstance(m, dict)}
                    for msg in new_msgs:
                        if isinstance(msg, dict) and msg.get("content") not in seen_content:
                            existing_list.append(msg)
                            seen_content.add(msg.get("content"))
                    target["odis_synthesis"] = existing_list

                # Update all other scalar fields (skip None, expert_analysis, odis_synthesis already handled)
                for k, v in new_res.items():
                    if k in ("expert_analysis", "odis_synthesis") or v is None:
                        continue
                    target[k] = v
            else:
                # City not found — only append if it's a complete record (has population)
                # This prevents partial expert updates from creating invalid "skeleton" results
                if new_res.get("population"):
                    new_data.setdefault("results", []).append(new_res)
                    idx_new = len(new_data["results"]) - 1
                    existing_results[cg] = idx_new
                    existing_names[name_norm] = idx_new
                else:
                    logger.debug(f"Dropped partial update for unknown city: {cg}")

    # 2. Update other top-level fields (whitelist — prevents accidental overrides)
    for k in ["global_pitch", "odis_brief", "current_geo", "search_hash"]:
        if k in right and right[k]:
            new_data[k] = right[k]
                
    # Diagnostic logging before final creation
    try:
        res_obj = SearchResultsData(**new_data)
        if res_obj.results:
            logger.debug(f"Combined state merge successful.")
        return res_obj
    except Exception as e:
        logger.error(f"SearchResultsData validation failed: {e}")
        raise e

class FocusCity(BaseModel):
    """Structured representation of the focus city."""
    name: str = Field("", description="Nom de la commune")
    codgeo: str = Field("", description="Code INSEE de la commune")

    model_config = ConfigDict(revalidate_instances='never')

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        return data

class UserProfile(BaseModel):
    """Basic extraction of user identity and raw initial request."""
    name: Optional[str] = None
    raw_request: str = ""

    model_config = ConfigDict(revalidate_instances='never')

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        return data

class ODISGraphState(BaseModel):
    """Global Graph State."""
    messages: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    user_profile: UserProfile = Field(default_factory=UserProfile)
    search_criteria: Annotated[SearchCriterias, merge_search_criteria] = Field(default_factory=SearchCriterias)
    search_results: Annotated[Optional[SearchResultsData], merge_search_results] = None
    focus_city: Optional[FocusCity] = None
    criteria_hash: Annotated[Optional[str], take_latest_hash] = None
    pending_experts: List[str] = Field(default_factory=list)
    execution_mode: Literal['full_analysis', 'specific_ask'] = 'full_analysis'
    odis_brief: str = ""
    last_summarized_idx: int = 0
    next_node: Optional[str] = None
    active_agent: Optional[str] = Field(None, description="The last active agent node name")
    is_interview_complete: bool = Field(False, description="True if Interviewer has finished collection")
    interaction_id: str = Field("", description="Unique session interaction ID")
    username: str = Field("unknown", description="Authenticated user email/name")
    usage: Annotated[UsageStats, add_usage] = Field(default_factory=UsageStats)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        revalidate_instances='never', # Crucial for Streamlit class redefinition issues
        from_attributes=True
    )

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            data = data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        
        # Robust focus_city normalization (string -> object)
        if isinstance(data, dict) and "focus_city" in data:
            val = data.get("focus_city")
            if isinstance(val, str) and val.strip():
                data["focus_city"] = {"name": val.strip(), "codgeo": ""}
            elif isinstance(val, dict):
                # Clean up if TRULY empty dict
                if not val.get("name") and not val.get("codgeo"):
                    data["focus_city"] = None
            elif val is not None:
                # If it's an object, check its name attribute if possible
                has_name = getattr(val, 'name', None)
                has_codgeo = getattr(val, 'codgeo', None)
                if not has_name and not has_codgeo:
                    data["focus_city"] = None
        return data


FocusCity.model_rebuild()
UserProfile.model_rebuild()
ODISGraphState.model_rebuild()

from dataclasses import dataclass

@dataclass
class ODISDeps:
    state: ODISGraphState  # Shared States/Data
    client: genai.Client         # Shared Client
    
    # Allow arbitrary types for genai.Client
    class Meta:
        arbitrary_types_allowed = True


class ODISContextBuilder:
    """
    Centralized service for building LLM-ready context blocks for all ODIS sub-agents.

    This class enforces an allow-list per agent and uses Pydantic field descriptions
    as human-readable JSON keys to maximize LLM readability and minimize token usage.

    All public methods return a JSON string ready to be injected into a prompt.
    """

    # -------------------------------------------------------------------------
    # PUBLIC ENTRY POINT
    # -------------------------------------------------------------------------

    @classmethod
    def agent_context(cls, state: "ODISGraphState", agent_name: str) -> str:
        """
        Assembles the full dynamic context block for a given agent.

        Args:
            state: The current ODISGraphState.
            agent_name: One of 'synthesizer', 'refiner', 'scorer', 'scout',
                        'web', 'job_hunter', 'interviewer'.

        Returns:
            A formatted JSON string ready to inject into a system prompt.
        """
        builders = {
            "synthesizer": cls._synthesizer_context,
            "refiner":     cls._refiner_context,
            "scorer":      cls._scorer_context,
            "scout":       cls._scout_context,
            "web":         cls._web_context,
            "job_hunter":  cls._job_hunter_context,
            "interviewer": cls._interviewer_context,
            "router":      cls._router_context,
        }
        builder_fn = builders.get(agent_name)
        if not builder_fn:
            logger.warning(f"[CTX] Unknown agent '{agent_name}' — returning empty context.")
            return "{}"

        ctx = builder_fn(state)
        result = json.dumps(ctx, ensure_ascii=False, indent=2)
        logger.debug(f"[CTX] {agent_name} context assembled ({len(result)} chars)")
        return result

    # -------------------------------------------------------------------------
    # AGENT-SPECIFIC BUILDERS
    # -------------------------------------------------------------------------

    @classmethod
    def _synthesizer_context(cls, state: "ODISGraphState") -> dict:
        """Focus City (full_thematic) + Baseline (scores_only) + Top 5 summary + Experts + Briefing."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief
            if state.search_criteria and state.search_criteria.notes_qualitatives:
                ctx["Notes qualitatives"] = state.search_criteria.notes_qualitatives
        else: 
            if state.search_criteria:
                ctx["Critères de recherche"] = cls._criteria_labels(state.search_criteria)


        if state.focus_city and state.search_results:
            focus = state.search_results.get_by_code(state.focus_city.codgeo)
            if focus:
                ctx["Ville analysée"] = cls._city_full_thematic(focus)
                ctx["Artefacts experts"] = {
                    "Scout (terrain)": focus.expert_analysis.get("scout", "Non disponible"),
                    "Web (actualités)": focus.expert_analysis.get("web", "Non disponible"),
                    "Job Hunter (emploi)": focus.expert_analysis.get("job_hunter", "Non disponible"),
                }

        if state.search_results and state.search_results.current_geo:
            ctx["Ville actuelle (référence)"] = cls._city_full_thematic(state.search_results.current_geo)

        # if state.search_results and state.search_results.results:
        #     ctx["Top 5 communes recommandées"] = cls._top5_summary(state.search_results.results)
        
        if state.messages:
            ctx["Dernier message"] = state.messages[-1].get("content", "")

        return ctx

    @classmethod
    def _refiner_context(cls, state: "ODISGraphState") -> dict:
        """Criteria labels + last messages + Top 5 summary + Briefing."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Dernier Briefing"] = state.odis_brief

        if state.search_criteria:
            ctx["Critères de recherche"] = cls._criteria_labels(state.search_criteria)

        new_messages = state.messages[state.last_summarized_idx:]
        if new_messages:
            ctx["Nouveaux échanges"] = [
                {"role": m.get("role"), "message": m.get("content", "")}
                for m in new_messages
            ]

        if state.search_results and state.search_results.results:
            ctx["Top 5 communes"] = cls._top5_summary(state.search_results.results)

        return ctx

    @classmethod
    def _scorer_context(cls, state: "ODISGraphState") -> dict:
        """Criteria labels only + Briefing + Top 5 results if available."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief
        else:
            if state.search_criteria and state.search_criteria.notes_qualitatives:
                ctx["Notes qualitatives"] = state.search_criteria.notes_qualitatives
            if state.search_criteria:
                ctx["Critères de recherche"] = cls._criteria_labels(state.search_criteria)
        
        # Include Top 5 results if they exist to avoid redundant tool calls
        if state.search_results and state.search_results.results:
            ctx["Top 5 communes identifiées (scores calculés)"] = [
                {
                    "Rang": i + 1,
                    "Code INSEE": r.codgeo,
                    **cls._city_full_thematic(r)
                }
                for i, r in enumerate(state.search_results.results[:5])
            ]
            
        return ctx

    @classmethod
    def _scout_context(cls, state: "ODISGraphState") -> dict:
        """City identity + criteria labels + existing artifact + Briefing."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief
        else:
            if state.search_criteria and state.search_criteria.notes_qualitatives:
                ctx["Notes qualitatives"] = state.search_criteria.notes_qualitatives
            if state.search_criteria:
                ctx["Critères de recherche"] = cls._criteria_labels(state.search_criteria)

        if state.focus_city and state.search_results:
            focus = state.search_results.get_by_code(state.focus_city.codgeo)
            
            if focus:
                ctx["Ville analysée"] = cls._city_identity(focus)
                existing = focus.expert_analysis.get("scout")
                if existing:
                    ctx["Connaissances actuelles (Scout)"] = existing

        if state.messages:
            ctx["Dernière question"] = state.messages[-1].get("content", "")

        return ctx

    @classmethod
    def _web_context(cls, state: "ODISGraphState") -> dict:
        """City identity + criteria labels + existing artifact + Briefing."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief
        else: 
            if state.search_criteria and state.search_criteria.notes_qualitatives:
                ctx["Notes qualitatives"] = state.search_criteria.notes_qualitatives
            if state.search_criteria:
                ctx["Critères de recherche"] = cls._criteria_labels(state.search_criteria)

        if state.focus_city and state.search_results:
            focus = state.search_results.get_by_code(state.focus_city.codgeo)
                
            if focus:
                ctx["Ville analysée"] = cls._city_identity(focus)
                existing = focus.expert_analysis.get("web")
                if existing:
                    ctx["Connaissances actuelles (Web)"] = existing

        if state.messages:
            ctx["Dernière question"] = state.messages[-1].get("content", "")

        return ctx

    @classmethod
    def _job_hunter_context(cls, state: "ODISGraphState") -> dict:
        """City identity + ROME codes with labels + own existing artifact."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief

            if state.search_criteria and state.search_criteria.notes_qualitatives:
                ctx["Notes qualitatives"] = state.search_criteria.notes_qualitatives
        if state.search_criteria:
            ctx["Codes ROME à rechercher"] = cls._criteria_rome_codes(state.search_criteria)

        if state.focus_city and state.search_results:
            focus = state.search_results.get_by_code(state.focus_city.codgeo)
                
            if focus:
                ctx["Ville analysée"] = cls._city_identity(focus)
                existing = focus.expert_analysis.get("job_hunter")
                if existing:
                    ctx["Connaissances actuelles (Job Hunter)"] = existing

        if state.messages:
            ctx["Dernière question"] = state.messages[-1].get("content", "")

        return ctx

    @classmethod
    def _interviewer_context(cls, state: "ODISGraphState") -> dict:
        """Full criteria (with readable keys) + last user message."""
        ctx: Dict[str, Any] = {}
        if state.search_criteria:
            ctx["Critères identifiés"] = cls._criteria_full(state.search_criteria)
        if state.messages:
            ctx["Dernier message utilisateur"] = state.messages[-1].get("content", "")
        return ctx

    @classmethod
    def _router_context(cls, state: "ODISGraphState") -> dict:
        """Specific context for the Router: Briefing + Identified Cities + Focus + Interview Status."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief
            
        if state.search_results and state.search_results.results:
            ctx["Villes identifiées"] = [
                {"nom": r.name, "code_insee": r.codgeo} 
                for r in state.search_results.results[:5]
            ]
        
        if state.focus_city:
            ctx["Ville cible"] = f"{state.focus_city.name} ({state.focus_city.codgeo})"
        else:
            ctx["Ville cible"] = "Non définie"
        
        if state.messages:
            ctx["Dernier message"] = state.messages[-1].get("content", "")
        return ctx

    # -------------------------------------------------------------------------
    # REUSABLE BUILDING BLOCKS
    # -------------------------------------------------------------------------

    @classmethod
    def _criteria_labels(cls, sc: "SearchCriterias") -> dict:
        """Returns a human-readable summary of search criteria (labels only, no codes)."""
        def _labels(items):
            if not items:
                return []
            flat = []
            for item in items:
                if isinstance(item, list):
                    flat.extend([i.label if hasattr(i, "label") else str(i) for i in item if i])
                elif hasattr(item, "label"):
                    flat.append(item.label)
                else:
                    flat.append(str(item))
            return flat

        return {
            "Commune actuelle": sc.commune_actuelle.label if sc.commune_actuelle else None,
            "Zone de recherche": sc.loc_search_area,
            "Nombre d'adultes": sc.nb_adultes,
            "Nombre d'enfants": sc.nb_enfants,
            "Niveaux scolaires": sc.classe_enfants,
            "Métiers ciblés": _labels(sc.codes_metiers),
            "Formations ciblées": _labels(sc.codes_formations),
            "Hébergement cible": sc.hebergement_cible,
            "Logement cible": sc.logement,
            "Besoin santé": sc.besoin_sante,
            "Profil de pondération": sc.weight_profile,
            "Services d'inclusion": _labels(sc.inc_services_add_selection),
            "Associations / centres d'intérêt": _labels(sc.inc_asso_add_selection),
            "Fréquence de retour (attache)": getattr(sc, 'freq_retour', "Pas d'attache particulière") or "Pas d'attache particulière",
            "Notes qualitatives": sc.notes_qualitatives,
        }

    @classmethod
    def _criteria_full(cls, sc: "SearchCriterias") -> dict:
        """Full criteria with readable keys — used by Interviewer."""
        base = cls._criteria_labels(sc)
        # Add codes for Interviewer's update logic
        def _items_with_code(items):
            if not items:
                return []
            flat = []
            for item in items:
                if isinstance(item, list):
                    flat.extend([{"code": i.code, "label": i.label} for i in item if hasattr(i, "code")])
                elif hasattr(item, "code"):
                    flat.append({"code": item.code, "label": item.label})
            return flat

        base["Codes métiers (avec codes)"] = _items_with_code(sc.codes_metiers)
        base["Codes formations (avec codes)"] = _items_with_code(sc.codes_formations)
        base["Commune actuelle (code INSEE)"] = sc.commune_actuelle.code if sc.commune_actuelle else None
        base["Zone de recherche (codes)"] = sc.loc_search_code
        return base

    @classmethod
    def _criteria_rome_codes(cls, sc: "SearchCriterias") -> list:
        """Returns a structured list of ROME codes per adult — for Job Hunter."""
        result = []
        for i, adult_codes in enumerate(sc.codes_metiers):
            adult_entry: Dict[str, Any] = {"adulte": i + 1, "métiers": []}
            for code in adult_codes:
                if hasattr(code, "code"):
                    adult_entry["métiers"].append({"code": code.code, "libellé": code.label})
                else:
                    adult_entry["métiers"].append({"code": str(code), "libellé": str(code)})
            result.append(adult_entry)
        return result

    @classmethod
    def _city_identity(cls, city: "CommuneResult") -> dict:
        """Minimal city block — used by Scout, Web, Job Hunter."""
        return {
            "Nom": city.name,
            "Code INSEE": city.codgeo,
            "Population": city.population,
            "Bassin de vie": city.name_bdv,
        }

    @classmethod
    def _city_scores_only(cls, city: "CommuneResult") -> dict:
        """Category scores only — used for Baseline City in Synthesizer."""
        return {
            "Nom": city.name,
            "Score global": f"{round(city.global_score * 100, 1)}%",
            "Scores par catégorie": {
                "Emploi": f"{round(city.employment.cat_score * 100, 1)}%",
                "Logement": f"{round(city.housing.cat_score * 100, 1)}%",
                "Éducation": f"{round(city.education.cat_score * 100, 1)}%",
                "Santé": f"{round(city.health.cat_score * 100, 1)}%",
                "Inclusion": f"{round(city.inclusion.cat_score * 100, 1)}%",
                "Mobilité": f"{round(city.mobility.cat_score * 100, 1)}%",
            },
        }

    @classmethod
    def _city_full_thematic(cls, city: "CommuneResult") -> dict:
        """Full thematic snapshot — used for Focus City in Synthesizer."""
        ctx = cls._city_identity(city) # Start with identity (INSEE, Pop, etc.)
        ctx.update(cls._city_scores_only(city))
        ctx["Emploi & Formation"] = {
            "Offres standard (total bassin)": city.employment.standard_jobs_total,
            "Offres correspondant au projet": city.employment.standard_jobs_matching_total,
            "Top métiers en tension": city.employment.top_professions[:5],
            "Offres SIAE correspondantes": city.employment.inclusive_jobs_matching_summary,
        }
        ctx["Logement"] = {
            "Loyer moyen au m²": city.housing.price_per_sqm,
            "Accueillants J'Accueille (bassin)": city.housing.host_count,
        }
        ctx["Éducation"] = {"Établissements par niveau": city.education.facility_counts}
        ctx["Santé"] = {"Établissements de santé": city.health.facility_counts}
        ctx["Inclusion"] = {
            "Associations réfugiés identifiées": city.inclusion.asso_refugee_count,
            "Associations d'inclusion (total)": city.inclusion.asso_inclusion_count,
            # "Thématiques services d'inclusion": list(city.inclusion.services_grouped.keys()),
        }
        mobility_ctx = {"Arrêts transports en commun (total bassin)": city.mobility.total_stops}
        
        if city.mobility.is_same_epci is not None:
            mobility_ctx["Même EPCI que commune actuelle"] = "Oui" if city.mobility.is_same_epci else "Non"
            
        if city.mobility.distance_to_current_km is not None:
            mobility_ctx["Distance commune actuelle"] = f"{city.mobility.distance_to_current_km} km"

        ctx["Mobilité"] = mobility_ctx

        if city.scorer_pitch:
            ctx["Points forts (Scorer)"] = city.scorer_pitch
        return ctx

    @classmethod
    def _top5_summary(cls, results: list) -> list:
        """Compact ranked list of the Top 5 communes."""
        return [
            {
                "Rang": i + 1,
                "Nom": city.name,
                "Code INSEE": city.codgeo,
                "Score global": f"{round(city.global_score * 100, 1)}%",
                "Résumé expert": city.scorer_pitch or "—",
            }
            for i, city in enumerate(results[:5])
        ]
