import asyncio
import copy
import logging
import os
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, TYPE_CHECKING, cast

import logfire
import pandas as pd
import streamlit as st

from core.models import SearchCriterias, CriteriaItem

if TYPE_CHECKING:
    from agents.state import GraphState


GraphRunStatus = Literal["running", "done", "error", "timeout", "cancelled"]
TERMINAL_GRAPH_RUN_STATUSES = frozenset({"done", "error", "timeout", "cancelled"})
DEFAULT_GRAPH_RUN_TIMEOUT_SECONDS = 60.0
GRAPH_RUN_POLICY_VERSION = "p5-02-v2"


class GraphRunTimedOut(TimeoutError):
    """The optional graph did not finish inside its end-to-end deadline."""


class GraphRunCancelled(RuntimeError):
    """The user superseded or cancelled the optional graph run."""


@dataclass(frozen=True)
class GraphRunIdentity:
    """Stable identity for one logical run and its retry attempt."""

    run_id: str
    attempt: int


@dataclass(frozen=True)
class GraphRunRecord:
    """Serializable lifecycle metadata for a session-local graph attempt."""

    task_key: str
    identity: GraphRunIdentity
    task_type: str
    criteria_hash: str
    focus_city_code: str
    owner_username: str
    organization_id: str
    started_at: float
    deadline_at: float
    policy_version: str = GRAPH_RUN_POLICY_VERSION

    def as_store_value(self) -> dict[str, Any]:
        return {
            "status": "running",
            "run_id": self.identity.run_id,
            "attempt": self.identity.attempt,
            "task_key": self.task_key,
            "task_type": self.task_type,
            "criteria_hash": self.criteria_hash,
            "focus_city_code": self.focus_city_code,
            "owner_username": self.owner_username,
            "organization_id": self.organization_id,
            "start_time": self.started_at,
            "deadline_at": self.deadline_at,
            "policy_version": self.policy_version,
            "cancel_requested": False,
            "retryable": True,
        }


# Global storage for background tasks (Now restricted to session state for privacy)
def get_odis_bg_store() -> dict:
    """Returns a session-specific dictionary for background task results."""
    if "odis_bg_store" not in st.session_state:
        st.session_state["odis_bg_store"] = {}
    return st.session_state["odis_bg_store"]


def odis_get_bg_result(hash_val: str) -> Any:
    """Safely retrieves a background result from the global store."""
    return get_odis_bg_store().get(hash_val)


def is_terminal_graph_run_status(status: str | None) -> bool:
    """Return whether an AI-analysis task no longer needs Streamlit polling."""
    return status in TERMINAL_GRAPH_RUN_STATUSES


def get_graph_run_timeout_seconds() -> float:
    """Read the bounded optional-AI deadline without accepting an unsafe value."""
    raw_value = os.getenv(
        "ODIS_GRAPH_RUN_TIMEOUT_SECONDS", str(DEFAULT_GRAPH_RUN_TIMEOUT_SECONDS)
    )
    try:
        timeout_seconds = float(raw_value)
    except (TypeError, ValueError):
        logging.warning(
            "Invalid ODIS_GRAPH_RUN_TIMEOUT_SECONDS=%r; using %.0fs",
            raw_value,
            DEFAULT_GRAPH_RUN_TIMEOUT_SECONDS,
        )
        return DEFAULT_GRAPH_RUN_TIMEOUT_SECONDS

    if timeout_seconds <= 0:
        logging.warning(
            "Non-positive ODIS_GRAPH_RUN_TIMEOUT_SECONDS=%r; using %.0fs",
            raw_value,
            DEFAULT_GRAPH_RUN_TIMEOUT_SECONDS,
        )
        return DEFAULT_GRAPH_RUN_TIMEOUT_SECONDS
    return timeout_seconds


def _copy_run_input(value: Any) -> Any:
    """Detach a background run from Streamlit's mutable state objects."""
    return copy.deepcopy(value)


def _clear_city_ai_analysis(search_results: Any, codgeo: str) -> None:
    """Remove generated city-analysis artifacts before an explicit retry."""
    if isinstance(search_results, dict):
        candidates = search_results.get("results", [])
    else:
        candidates = getattr(search_results, "results", [])

    for city in candidates or []:
        if isinstance(city, dict):
            city_code = city.get("codgeo")
            if str(city_code) != str(codgeo):
                continue
            city["expert_analysis"] = {}
            city["odis_synthesis"] = []
            return

        if str(getattr(city, "codgeo", "")) == str(codgeo):
            expert_analysis = getattr(city, "expert_analysis", None)
            if isinstance(expert_analysis, dict):
                expert_analysis.clear()
            else:
                setattr(city, "expert_analysis", {})
            synthesis = getattr(city, "odis_synthesis", None)
            if isinstance(synthesis, list):
                synthesis.clear()
            else:
                setattr(city, "odis_synthesis", [])
            return


def _owner_organization_id(
    search_criterias: Any, organization_id: Optional[str]
) -> str:
    if organization_id:
        return organization_id
    if isinstance(search_criterias, dict):
        value = search_criterias.get("org_context")
    else:
        value = getattr(search_criterias, "org_context", None)
    return str(value or "unknown")


def _run_control(entry: dict[str, Any]) -> tuple[threading.Event, threading.Lock]:
    """Return the in-memory control objects attached to one session run record."""
    control = entry.get("_control")
    if not isinstance(control, dict):
        control = {"cancel_event": threading.Event(), "lock": threading.Lock()}
        entry["_control"] = control
    return (
        cast(threading.Event, control["cancel_event"]),
        cast(threading.Lock, control["lock"]),
    )


def _matches_run(entry: dict[str, Any], identity: GraphRunIdentity) -> bool:
    return (
        entry.get("run_id") == identity.run_id
        and entry.get("attempt") == identity.attempt
    )


def _complete_background_run(
    store: dict[str, Any],
    task_key: str,
    identity: GraphRunIdentity,
    status: GraphRunStatus,
    *,
    result: Any = None,
    error: str | None = None,
    error_code: str | None = None,
) -> bool:
    """Commit a terminal result only when its attempt is still current."""
    entry = store.get(task_key)
    if not isinstance(entry, dict):
        return False

    _, lock = _run_control(entry)
    with lock:
        current = store.get(task_key)
        if not isinstance(current, dict) or not _matches_run(current, identity):
            return False
        if current.get("status") != "running":
            return False

        current["status"] = status
        current["completed_at"] = time.time()
        if result is not None:
            current["result"] = result
        if error:
            current["error"] = error
        if error_code:
            current["error_code"] = error_code
        store[task_key] = current
        return True


def _cancel_current_background_run(
    store: dict[str, Any], task_key: str, *, reason: str = "cancelled"
) -> bool:
    """Mark the current attempt terminal and signal its worker to stop."""
    entry = store.get(task_key)
    if not isinstance(entry, dict):
        return False

    cancel_event, lock = _run_control(entry)
    with lock:
        current = store.get(task_key)
        if not isinstance(current, dict) or current.get("status") != "running":
            return False
        cancel_event.set()
        current["cancel_requested"] = True
        current["status"] = "cancelled"
        current["completed_at"] = time.time()
        current["error_code"] = reason
        current["error"] = "L'analyse IA a été annulée."
        store[task_key] = current
        return True


def cancel_background_city_analysis(task_key: str) -> bool:
    """Cancel the visible result of the current session-local AI attempt."""
    return _cancel_current_background_run(get_odis_bg_store(), task_key)


async def _run_until_terminal(
    input_data: dict[str, Any],
    cancel_event: threading.Event,
    deadline_monotonic: float,
) -> dict[str, Any]:
    """Run the graph while enforcing cancellation and an overall deadline."""
    task = asyncio.create_task(run_logic(input_data))
    try:
        while not task.done():
            if cancel_event.is_set():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise GraphRunCancelled()

            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise GraphRunTimedOut()

            await asyncio.wait({task}, timeout=min(0.2, remaining))

        return task.result()
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def sanitize_llm_markdown(text: str) -> str:
    """
    Cleans up common LLM artifacts in markdown strings,
    specifically literal '\\n' strings and other escaping artifacts.
    """
    if not text:
        return ""

    # Handle literal double-escaped newlines
    # Some LLMs return "\\n" which becomes "\n" literal in Python
    # Some might return "\\\\n"
    res = text
    for _ in range(3):
        res = res.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")

    # Also handle literal markdown escaping if the LLM is too aggressive
    # (e.g. \" replaced by ")
    res = res.replace('\\"', '"').replace("\\'", "'")

    return res


def map_ui_config_to_search_criterias(
    config: SearchCriterias, app_data: Dict[str, Any]
) -> SearchCriterias:
    """
    Converts a UI SearchCriterias into a Pydantic SearchCriterias object
    expected by the IA agents.
    """
    # 1. Commune Actuelle
    codgeo = config.commune_actuelle
    libgeo = (
        app_data["odis"].loc[codgeo, "libgeo"]
        if codgeo in app_data["odis"].index
        else str(codgeo)
    )
    commune_actuelle = CriteriaItem(code=str(codgeo), label=str(libgeo))

    # 2. Métiers
    rome_index = app_data.get("rome_index", pd.DataFrame())
    codes_metiers = []
    for metier_list in config.codes_metiers:
        enriched_list = []
        for code in metier_list:
            label = (
                rome_index.loc[code, "label"]
                if not rome_index.empty and code in rome_index.index
                else str(code)
            )
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_metiers.append(enriched_list)

    # 3. Formations
    form_index = app_data.get("codformations_index", pd.DataFrame())
    codes_formations = []
    for form_list in config.codes_formations:
        enriched_list = []
        for code in form_list:
            label = (
                form_index.loc[code, "label"]
                if not form_index.empty and code in form_index.index
                else str(code)
            )
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_formations.append(enriched_list)

    # 4. Inclusion Services
    inc_index = app_data.get("inclusion_services_index", pd.DataFrame())
    inc_services = []
    for code in config.inc_services_selection:
        label = (
            inc_index.loc[code, "label"]
            if not inc_index.empty and code in inc_index.index
            else str(code)
        )
        inc_services.append(CriteriaItem(code=str(code), label=str(label)))

    # 5. Inclusion Associations
    import config as cfg

    inc_assos = []
    waldec_index = app_data.get("waldec_index", pd.DataFrame())
    for item in config.inc_asso_add_selection:
        if isinstance(item, CriteriaItem):
            inc_assos.append(item)
        else:
            # item is likely a label (string)
            code_str = "000"
            if not waldec_index.empty:
                matches = waldec_index[waldec_index["label"] == item]
                if not matches.empty:
                    code_str = str(matches.index[0])
            inc_assos.append(CriteriaItem(code=code_str, label=str(item)))

    # 6. Type Logement
    type_log = None
    if config.type_logement and config.type_logement in cfg.HOUSING_TYPE_OPTIONS:
        type_log = CriteriaItem(
            code=config.type_logement,
            label=cfg.HOUSING_TYPE_OPTIONS[config.type_logement],
        )

    return SearchCriterias(
        commune_actuelle=commune_actuelle,
        loc_search_area=config.loc_search_area,
        loc_search_code=config.loc_search_code,
        nb_adultes=config.nb_adultes,
        nb_enfants=config.nb_enfants,
        classe_enfants=config.classe_enfants,
        codes_metiers=codes_metiers,
        codes_formations=codes_formations,
        inc_services_selection=inc_services,
        inc_asso_add_selection=inc_assos,
        hebergement_cible=config.hebergement_cible,
        logement=config.logement,
        type_logement=type_log,
        sante=config.besoin_sante,
        weight_profile=config.weight_profile,
        criteria_weights=config.criteria_weights,
        notes_qualitatives=[],
        # Org Specifics
        org_context=getattr(config, "org_context", None),
        org_strategic_locations=getattr(config, "org_strategic_locations", []),
        org_strategic_locations_type=getattr(
            config, "org_strategic_locations_type", "departement"
        ),
        poids_territoire=getattr(config, "poids_territoire", 1.0),
    )


def launch_background_city_analysis(
    nom: str,
    codgeo: str,
    search_criterias: Any,
    search_results: Any,
    h: str,
    messages: Optional[list] = None,
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
    organization_id: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    retry: bool = False,
) -> dict[str, Any]:
    """
    Launch one attempt-safe, bounded background city analysis.

    The run remains session-local and best-effort. It is intentionally not a
    durable workflow: a Cloud Run restart can still lose it. Each retry carries
    a new attempt number, and only the current run/attempt may update the UI.
    """
    store = get_odis_bg_store()
    task_key = f"analysis_{h}_{codgeo}"
    existing = store.get(task_key)
    if isinstance(existing, dict) and existing.get("status") == "running":
        if not retry:
            return existing
        _cancel_current_background_run(store, task_key, reason="superseded")

    if timeout_seconds is None:
        timeout_seconds = get_graph_run_timeout_seconds()
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    if retry and isinstance(existing, dict) and existing.get("run_id"):
        identity = GraphRunIdentity(
            run_id=str(existing["run_id"]),
            attempt=int(existing.get("attempt", 0)) + 1,
        )
    else:
        identity = GraphRunIdentity(run_id=uuid.uuid4().hex, attempt=1)

    try:
        current_username = username or st.session_state.get("username", "unknown")
    except Exception:
        current_username = username or "unknown"

    now = time.time()
    record = GraphRunRecord(
        task_key=task_key,
        identity=identity,
        task_type="city_analysis",
        criteria_hash=h,
        focus_city_code=str(codgeo),
        owner_username=str(current_username),
        organization_id=_owner_organization_id(search_criterias, organization_id),
        started_at=now,
        deadline_at=now + timeout_seconds,
    ).as_store_value()
    cancel_event, _ = _run_control(record)
    store[task_key] = record

    run_search_results = _copy_run_input(search_results)
    if retry:
        _clear_city_ai_analysis(run_search_results, codgeo)

    input_data = {
        "search_criteria": _copy_run_input(search_criterias),
        # This value is only a bootstrap default. triage_step replaces it with
        # the validated coordinator decision before any expert/synthesis node.
        "execution_mode": "full_analysis",
        "focus_city": {"name": nom, "codgeo": codgeo},
        "search_results": run_search_results,
        "criteria_hash": h,
        "messages": _copy_run_input(messages)
        if messages
        else [
            {
                "role": "user",
                "content": f"Fais une analyse complète pour {nom}.",
            }
        ],
        "interaction_id": interaction_id or "unknown",
        "username": str(current_username),
        "organization_id": record["organization_id"],
        "run_id": identity.run_id,
        "run_attempt": identity.attempt,
        "run_deadline_at": record["deadline_at"],
        "run_timeout_seconds": timeout_seconds,
    }

    def bg_analysis_task(
        results_store: dict[str, Any],
        run_identity: GraphRunIdentity,
        run_input: dict[str, Any],
        run_cancel_event: threading.Event,
        run_timeout_seconds: float,
    ) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        deadline_monotonic = time.monotonic() + run_timeout_seconds

        try:
            logging.info(
                "[BG-ANALYSIS] Starting run_id=%s attempt=%s city=%s (%s)",
                run_identity.run_id,
                run_identity.attempt,
                nom,
                codgeo,
            )
            final_state = loop.run_until_complete(
                _run_until_terminal(run_input, run_cancel_event, deadline_monotonic)
            )
            committed = _complete_background_run(
                results_store,
                task_key,
                run_identity,
                "done",
                result=final_state.get("search_results"),
            )
            if committed:
                logging.info(
                    "[BG-ANALYSIS] Finished run_id=%s attempt=%s city=%s",
                    run_identity.run_id,
                    run_identity.attempt,
                    codgeo,
                )
            else:
                logging.info(
                    "[BG-ANALYSIS] Dropped stale completion run_id=%s attempt=%s",
                    run_identity.run_id,
                    run_identity.attempt,
                )
        except GraphRunCancelled:
            logging.info(
                "[BG-ANALYSIS] Cancelled run_id=%s attempt=%s",
                run_identity.run_id,
                run_identity.attempt,
            )
        except GraphRunTimedOut:
            committed = _complete_background_run(
                results_store,
                task_key,
                run_identity,
                "timeout",
                error="L'analyse IA a dépassé le délai prévu. Réessayez.",
                error_code="deadline_exceeded",
            )
            if committed:
                logging.warning(
                    "[BG-ANALYSIS] Timed out run_id=%s attempt=%s",
                    run_identity.run_id,
                    run_identity.attempt,
                )
        except Exception:
            logging.exception(
                "[BG-ANALYSIS] Failed run_id=%s attempt=%s city=%s",
                run_identity.run_id,
                run_identity.attempt,
                codgeo,
            )
            _complete_background_run(
                results_store,
                task_key,
                run_identity,
                "error",
                error="L'analyse IA a rencontré une erreur technique. Réessayez.",
                error_code="graph_run_failed",
            )
        finally:
            if not loop.is_closed():
                loop.close()

    thread = threading.Thread(
        target=bg_analysis_task,
        args=(store, identity, input_data, cancel_event, timeout_seconds),
        name=f"odis-ai-{codgeo}-{identity.attempt}",
        daemon=True,
    )
    thread.start()
    return record


def run_async_safe(input_data: dict):
    """
    Exécute la logique asynchrone de manière sécurisée.
    Stratégie: "Non-Destructive Loop Management".
    On réutilise la loop du thread si elle existe, on en crée une si besoin,
    MAIS on ne la ferme JAMAIS explicitement ici. C'est le thread/process
    qui gérera son cycle de vie.
    """
    # 1. Harvest Telemetry Metadata (Main Thread)
    try:
        from services.telemetry import get_interaction_id

        interaction_id = get_interaction_id()
        username = st.session_state.get("username", "unknown")
    except:
        interaction_id = "unknown"
        username = "unknown"

    # 2. Inject into input_data
    input_data["interaction_id"] = interaction_id
    input_data["username"] = username

    try:
        # 3. Check current loop
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # 2. If no loop exists, create new
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_closed():
        # 3. If found loop is closed, replace it
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # 4. Run without closing
    return loop.run_until_complete(run_logic(input_data))


@logfire.instrument("Agent: Interviewer (Auto-Detect)")
def run_autodetect_safe(text: str):
    logfire.info("Interviewer Auto-Detect started")
    from agents.interviewer import interviewer_agent
    from agents.agent_config import get_gemini_client, get_p_model
    from agents.state import ODISDeps, GraphState

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    client = get_gemini_client()
    deps = ODISDeps(state=GraphState(), client=client)
    model = get_p_model("interviewer", client=client)

    result = loop.run_until_complete(
        interviewer_agent.run(text, deps=deps, model=model)
    )
    return result.output


def rehydrate_graph_state(input_data: dict) -> "GraphState":
    """
    Rehydrates raw dictionary data into a rich GraphState object.
    Ensures that all nested models (SearchResultsData, CommuneResult, etc.)
    are properly validated and instantiated.
    """
    from agents.state import GraphState
    from core.models import SearchCriterias, SearchResultsData, CommuneResult

    # 1. Search Criteria
    sc_data = input_data.get("search_criteria", {})
    sc = (
        SearchCriterias.model_validate(sc_data)
        if isinstance(sc_data, dict)
        else sc_data
    )

    # 2. Search Results (The most critical part for Scorer)
    sr_data = input_data.get("search_results")
    sr = None
    if sr_data:
        # Pydantic v2 recursive validation handles nested thematic data automatically
        sr = (
            SearchResultsData.model_validate(sr_data)
            if isinstance(sr_data, dict)
            else sr_data
        )

    # 3. Focus City
    fc_data = input_data.get("focus_city")
    fc = None
    if fc_data:
        fc = (
            CommuneResult.model_validate(fc_data)
            if isinstance(fc_data, dict)
            else fc_data
        )

    # 4. Construct Final State
    return GraphState(
        search_criteria=sc,
        search_results=sr,
        focus_city=fc,
        execution_mode=input_data.get("execution_mode", "full_analysis"),
        odis_brief=getattr(sc, "odis_brief", ""),
        messages=input_data.get("messages", []),
        interaction_id=input_data.get("interaction_id", "unknown"),
        username=input_data.get("username", "unknown"),
        organization_id=input_data.get("organization_id", "unknown"),
        run_id=input_data.get("run_id", "unknown"),
        run_attempt=int(input_data.get("run_attempt", 1)),
        run_deadline_at=input_data.get("run_deadline_at"),
    )


@logfire.instrument("ODIS Graph Logic")
async def run_logic(input_data: dict):
    """Execute one graph against a detached input snapshot within a deadline."""
    # Label the trace with metadata if available
    h = input_data.get("criteria_hash")
    iid = input_data.get("interaction_id")
    run_id = input_data.get("run_id", "unknown")
    run_attempt = input_data.get("run_attempt", 1)
    timeout_seconds = input_data.get("run_timeout_seconds")
    if timeout_seconds is None:
        timeout_seconds = get_graph_run_timeout_seconds()
    timeout_seconds = float(timeout_seconds)
    if timeout_seconds <= 0:
        raise ValueError("run_timeout_seconds must be positive")

    logfire.info(
        "Processing ODIS Graph Logic for {search_hash}",
        search_hash=h or "unknown",
        interaction_id=iid or "unknown",
        run_id=run_id,
        run_attempt=run_attempt,
    )

    from agents.state import ODISDeps
    from agents.graph import create_odis_graph
    from agents.agent_config import get_gemini_client

    # 1. Client Local (Centralized helper)
    client = get_gemini_client()

    # 2. State & Deps
    input_state = rehydrate_graph_state(input_data)
    deps = ODISDeps(state=input_state, client=client)

    # 3. Graphe
    app = create_odis_graph()

    # End-to-end graph deadline. The background runner additionally observes a
    # cancellation event so that a user retry cannot publish a late completion.
    try:
        await asyncio.wait_for(
            app.run(state=input_state, deps=deps), timeout=timeout_seconds
        )
    except asyncio.TimeoutError as exc:
        raise GraphRunTimedOut() from exc

    return {
        "search_results": input_state.search_results,
        "messages": input_state.messages,
        "run_id": input_state.run_id,
        "run_attempt": input_state.run_attempt,
    }
