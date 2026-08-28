import logging
from typing import Any
import streamlit as st

import config as cfg
from core.models import (
    CommuneResult,
    SearchResultsData,
    CityAnalysisReport,
    DomainReport,
)
from agents.utils import (
    cancel_background_city_analysis,
    launch_background_city_analysis,
    odis_get_bg_result,
)
from agents.source_registry import (
    is_vertex_grounding_redirect,
    source_references_for_result,
)
from services import telemetry

logger = logging.getLogger("ui.ai_analysis_dialog")


def _on_ia_dialog_dismiss():
    st.session_state.active_ia_city_index = None


def _merge_agent_results(final_state_results, codgeo: str, commune: CommuneResult):
    """Helper to merge graph state results back into session state."""
    if not final_state_results:
        return

    # 🧪 SOTA: Robust merging with type checking to prevent page-level crashes
    def _get_field(obj, field, default=None):
        if isinstance(obj, dict):
            return obj.get(field, default)
        return getattr(obj, field, default)

    # 1. Update Global Brief (Sync to config)
    if st.session_state.get("config"):
        st.session_state.config.odis_brief = _get_field(
            final_state_results, "odis_brief", st.session_state.config.odis_brief
        )

    # 2. Find and update the specific focus city
    target_city = None
    if hasattr(final_state_results, "get_by_code"):
        target_city = final_state_results.get_by_code(codgeo)
    if not target_city:
        new_results = _get_field(final_state_results, "results", []) or []
        for city_data in new_results:
            if str(_get_field(city_data, "codgeo")) == str(codgeo):
                target_city = city_data
                break
        if not target_city and _get_field(final_state_results, "commune_pressentie"):
            cp = _get_field(final_state_results, "commune_pressentie")
            if str(_get_field(cp, "codgeo")) == str(codgeo):
                target_city = cp
        if not target_city and _get_field(final_state_results, "current_geo"):
            cg = _get_field(final_state_results, "current_geo")
            if str(_get_field(cg, "codgeo")) == str(codgeo):
                target_city = cg

    if target_city:
        new_synth = _get_field(target_city, "odis_synthesis", [])
        if new_synth:
            commune.odis_synthesis = new_synth

        expert_data = _get_field(target_city, "expert_analysis", {})
        if expert_data and isinstance(expert_data, dict):
            commune.expert_analysis.update(expert_data)

        artifact_data = _get_field(target_city, "expert_artifacts", {})
        if artifact_data and isinstance(artifact_data, dict):
            commune.expert_artifacts.update(artifact_data)

        source_data = _get_field(target_city, "expert_sources", {})
        if source_data and isinstance(source_data, dict):
            commune.expert_sources.update(source_data)

        new_pitch = _get_field(target_city, "refiner_pitch")
        if new_pitch:
            commune.refiner_pitch = new_pitch

        new_report = _get_field(target_city, "analysis_report")
        if new_report:
            if isinstance(new_report, dict):
                commune.analysis_report = CityAnalysisReport.model_validate(new_report)
            else:
                commune.analysis_report = new_report


@st.fragment(run_every=2.0)
def polling_synthesis_fragment(
    task_key: str,
    nom: str,
    codgeo: str,
    search_criterias: Any,
    commune: CommuneResult,
    h: str,
):
    """Fragment that automatically polls for synthesis completion every 2s."""
    status_data = odis_get_bg_result(task_key)
    if not status_data:
        launch_background_city_analysis(
            nom, codgeo, search_criterias, st.session_state.search_results, h
        )
        st.caption("Lancement de la synthèse...")
    elif status_data.get("status") == "running":
        import time

        start_time = status_data.get("start_time", time.time())
        deadline_at = status_data.get("deadline_at")
        elapsed = time.time() - start_time
        timeout_seconds = (
            max(1.0, float(deadline_at) - float(start_time))
            if deadline_at is not None
            else 60.0
        )
        progress = min(1.0, elapsed / timeout_seconds)
        st.progress(
            progress,
            text=f"Préparation de la synthèse (jusqu'à {timeout_seconds:.0f} secondes)...",
        )
        if st.button("Annuler l'analyse", key=f"cancel_analysis_{task_key}"):
            cancel_background_city_analysis(task_key)
            st.rerun()
    elif status_data.get("status") in {"error", "timeout", "cancelled"}:
        st.error(
            status_data.get("error")
            or "L'analyse IA n'a pas pu être réalisée. Réessayez."
        )
        if st.button("Réessayer", key=f"retry_analysis_{task_key}"):
            # Product decision: a retry replaces the prior displayed analysis.
            # Clearing the live object also makes ia_analysis_content return to
            # the polling state immediately after the rerun.
            commune.odis_synthesis.clear()
            commune.expert_analysis.clear()
            commune.expert_artifacts.clear()
            commune.expert_sources.clear()
            launch_background_city_analysis(
                nom,
                codgeo,
                search_criterias,
                st.session_state.search_results,
                h,
                retry=True,
            )
            st.rerun()
    elif status_data.get("status") == "done":
        _merge_agent_results(status_data.get("result"), codgeo, commune)
        if not commune.odis_synthesis:
            commune.odis_synthesis = [
                {
                    "role": "assistant",
                    "content": "⚠️ *Synthèse introuvable ou erreur de génération.*",
                }
            ]
        st.rerun()  # Full dialog rerun to reveal content


@st.fragment(run_every=2.0)
def polling_chat_fragment(
    task_key: str, chat_task_key: str, codgeo: str, commune: CommuneResult
):
    """Fragment that automatically polls for follow-up chat response every 2s."""
    status_data = odis_get_bg_result(task_key)
    if status_data and status_data.get("status") == "done":
        _merge_agent_results(status_data.get("result"), codgeo, commune)
        if chat_task_key in st.session_state:
            del st.session_state[chat_task_key]
        st.rerun()  # Full dialog rerun
    elif status_data and status_data.get("status") in {
        "error",
        "timeout",
        "cancelled",
    }:
        st.error(
            status_data.get("error")
            or "La réponse IA n'a pas pu être générée. Réessayez."
        )
        if chat_task_key in st.session_state:
            del st.session_state[chat_task_key]
        st.rerun()
    else:
        with st.chat_message("assistant"):
            st.write("✨ _Recherche de la réponse en cours (Job Hunter / Scouts)..._")
            if st.button("Annuler", key=f"cancel_chat_{task_key}"):
                cancel_background_city_analysis(task_key)
                st.rerun()


def _render_sources_popover(
    source_data: list[dict[str, Any]] | None,
    domain: str,
) -> None:
    """Render compact, application-owned source provenance beside a fiche."""
    references = source_data or source_references_for_result(domain, None)
    with st.popover("ⓘ Sources"):
        st.markdown("**Sources utilisées par cette fiche**")
        lines = []
        for reference in references:
            label = reference.get("label", reference.get("source_key", "Source"))
            status = reference.get("status", "contexte")
            note = reference.get("note", "")
            source_url = reference.get("source_url")
            reference_id = reference.get("reference_id")
            # Persisted runs may contain the previous technical wording. Keep
            # the user-facing popover stable even before a run is recomputed.
            if reference.get("source_key") == "web":
                if source_url:
                    note = ""
                elif reference.get("grounding_confirmed") is False:
                    note = (
                        "L'outil a été appelé, mais Google n'a confirmé aucune "
                        "recherche. Aucun résumé Web n'est retenu comme preuve."
                    )
                elif reference.get("grounding_queries"):
                    note = (
                        "Google a été consulté, mais n'a transmis aucune URL "
                        "de page source exploitable."
                    )
                else:
                    note = "Aucune page source n'a été transmise pour cette recherche."

            # Top level list item
            if source_url:
                if reference_id:
                    lines.append(
                        f"- **{reference_id}** — [{label}]({source_url}) — *{status}*"
                    )
                else:
                    lines.append(f"- [{label}]({source_url}) — *{status}*")
                if not is_vertex_grounding_redirect(source_url):
                    lines.append(
                        f"  - <small style='color: gray;'>URL : {source_url}</small>"
                    )
            else:
                lines.append(f"- **{label}** — *{status}*")

            # Sub-items (formatted as nested bullets in caption style)
            domain_name = reference.get("grounding_domain")
            if domain_name:
                lines.append(
                    f"  - <small style='color: gray;'>Domaine : {domain_name}</small>"
                )
            queries = reference.get("grounding_queries") or []
            if queries:
                lines.append(
                    f"  - <small style='color: gray;'>Mots clés : {' · '.join(str(q) for q in queries)}</small>"
                )
            elif reference.get("search_terms"):
                lines.append(
                    f"  - <small style='color: gray;'>Mots clés demandés : {' · '.join(str(term) for term in reference['search_terms'])}</small>"
                )
            if note:
                lines.append(f"  - <small style='color: gray;'>{note}</small>")

        if lines:
            st.markdown("\n".join(lines), unsafe_allow_html=True)
        if not source_data:
            st.caption(
                "Historique détaillé des appels indisponible pour cette fiche persistée."
            )


def _get_or_build_analysis_report(
    commune: CommuneResult,
    fallback_content: str = "",
) -> CityAnalysisReport | None:
    """Retrieves or builds the CityAnalysisReport from structured commune data."""
    report = getattr(commune, "analysis_report", None)
    if isinstance(report, dict):
        try:
            report = CityAnalysisReport.model_validate(report)
            commune.analysis_report = report
            return report
        except Exception:
            pass
    if isinstance(report, CityAnalysisReport):
        return report

    # Auto-build from structured expert_analysis on commune if available
    expert_analysis = getattr(commune, "expert_analysis", {}) or {}
    if not expert_analysis:
        return None

    domain_defs = [
        ("housing_expert", "🏠 Logement & Hébergement", "🏠 Logement"),
        ("mobility_expert", "🚆 Mobilité & Transports", "🚆 Mobilité"),
        ("healthcare_expert", "🏥 Santé & Accompagnement Médical", "🏥 Santé"),
        ("education_expert", "🎓 Éducation & Petite Enfance", "🎓 Éducation"),
        ("social_integration_expert", "🤝 Insertion Sociale & Solidarité", "🤝 Insertion"),
        ("job_hunter", "💼 Emploi & Insertion Professionnelle", "💼 Emploi"),
    ]
    domains: dict[str, DomainReport] = {}
    sources_map = getattr(commune, "expert_sources", {}) or {}
    artifacts_map = getattr(commune, "expert_artifacts", {}) or {}

    for key, label, short_label in domain_defs:
        content = expert_analysis.get(key)
        if content and content.strip():
            domains[key] = DomainReport(
                domain_key=key,
                label=label,
                short_label=short_label,
                content=content.strip(),
                sources=sources_map.get(key, []),
                artifacts=artifacts_map.get(key),
            )

    if not domains:
        return None

    ccas_content = expert_analysis.get("ccas_locator")
    report = CityAnalysisReport(
        city_name=commune.name,
        city_code=commune.codgeo,
        avis_global=fallback_content,
        domains=domains,
        ccas_contact=ccas_content.strip() if ccas_content and ccas_content.strip() else None,
    )
    commune.analysis_report = report
    return report


def _render_initial_analysis_report(
    commune: CommuneResult,
    fallback_content: str = "",
) -> None:
    """Renders the initial full analysis report with executive brief, tabs for experts, and CTA at the end."""
    report = _get_or_build_analysis_report(commune, fallback_content)
    if report:
        # 1. Executive overview (Top)
        if report.avis_global:
            avis_text = report.avis_global.strip()
            if not avis_text.startswith("## 🧭") and not avis_text.startswith("# 🧭"):
                avis_text = f"## 🧭 Avis Global d'Orientation pour {report.city_name}\n\n{avis_text}"
            st.markdown(avis_text)

        # 2. Render Tabs for Domain Experts
        active_domains = [
            d for d in report.domains.values() if d.content and d.content.strip()
        ]
        if active_domains:
            st.divider()
            st.header("🔬 Analyses Thématiques Détaillées")
            tab_objs = st.tabs([d.short_label for d in active_domains])
            for tab_obj, domain in zip(tab_objs, active_domains):
                with tab_obj:
                    report_col, source_col = st.columns([0.88, 0.12])
                    with report_col:
                        st.markdown(domain.content)
                    with source_col:
                        _render_sources_popover(domain.sources, domain.domain_key)

        # 3. Digested territorial comparison
        if report.analyse_comparative:
            comp_text = report.analyse_comparative.strip()
            if not comp_text.startswith("## ⚖️") and not comp_text.startswith("# ⚖️"):
                comp_text = f"## ⚖️ Analyse Comparative Territoriale\n\n{comp_text}"
            st.divider()
            st.markdown(comp_text)

        # 4. Unverified elements / gaps
        if report.elements_non_verifies:
            gap_text = report.elements_non_verifies.strip()
            if not gap_text.startswith("## ⚠️") and not gap_text.startswith("# ⚠️"):
                gap_text = f"## ⚠️ Éléments Non Vérifiés & Vigilances\n\n{gap_text}"
            st.divider()
            st.markdown(gap_text)

        # 5. Call to Action: CCAS Contact
        if report.ccas_contact:
            st.divider()
            st.markdown(report.ccas_contact.strip())

        # 6. Call to Action: Et ensuite ?
        if report.et_ensuite:
            next_text = report.et_ensuite.strip()
            if not next_text.startswith("## ❓") and not next_text.startswith("# ❓"):
                next_text = f"## ❓ Et ensuite ? (Pistes d'action)\n\n{next_text}"
            st.divider()
            st.markdown(next_text)
    else:
        # Standard fallback rendering
        st.markdown(fallback_content)


def ia_analysis_content(nom: str, codgeo: str, search_criterias: Any):
    """Main component for AI synthesis, rendered inside a @st.dialog."""
    results: SearchResultsData = st.session_state.get("search_results")
    if not results:
        st.error("Résultats introuvables.")
        return
    commune = results.get_by_code(codgeo)
    if not commune:
        st.error(f"Détails introuvables pour {nom} ({codgeo}).")
        return

    # Use a unique key for background tracking
    h = st.session_state.get("active_search_hash")
    task_key = f"analysis_{h}_{codgeo}"

    # 2. Trigger analysis if synthesis is missing (Polled within its own fragment)
    if not commune.odis_synthesis:
        polling_synthesis_fragment(task_key, nom, codgeo, search_criterias, commune, h)
        return

    # 3. Render Full Structured Analysis Report directly
    history = list(commune.odis_synthesis)
    _render_initial_analysis_report(
        commune,
        history[0]["content"] if history else "",
    )

    # 4. Check if Interactive Chat is enabled for the active organization
    active_org = st.session_state.get("org")
    if not cfg.is_interactive_chat_enabled(active_org, search_criterias):
        return

    # 5. Interactive Chat Section (Enabled for J'Accueille / authorized orgs)
    st.divider()
    st.subheader(f"💬 Questions complémentaires sur {nom}")

    # Render follow-up Q&A messages (skip message 0 which is the full report)
    if len(history) > 1:
        for msg in history[1:]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Check if a follow-up chat task is running
    chat_task_key = f"chat_active_flag_{codgeo}"

    if st.session_state.get(chat_task_key):
        polling_chat_fragment(task_key, chat_task_key, codgeo, commune)

    # Handle follow-up questions
    question = st.chat_input(
        f"Ex: Quelles associations facilitent le logement à {nom} ?",
        key=f"chat_input_ia_{codgeo}",
    )

    if question:
        # Note: the actual graph run will return the full history including this message
        launch_background_city_analysis(
            nom,
            codgeo,
            search_criterias,
            results,
            h,
            messages=history + [{"role": "user", "content": question}],
        )
        st.session_state[chat_task_key] = True
        st.rerun()


@st.dialog(title=" ", width="large", on_dismiss=_on_ia_dialog_dismiss)
def show_ia_analysis_dialog(index: Any):
    """Displays AI synthesis and chat for a city in a modal."""
    if (
        "search_results" not in st.session_state
        or not st.session_state.search_results
        or not st.session_state.search_results.get_by_code(index)
    ):
        st.error("Données de la ville introuvables.")
        return

    commune = st.session_state.search_results.get_by_code(index)

    nom = commune.name
    codgeo = commune.codgeo

    telemetry.log_usage_event("run_ia_analysis", {"codgeo": codgeo, "name": nom})

    st.header(f"Analyse OD&IS pour {nom}")
    st.info(
        "Cette synthèse est générée par une intelligence artificielle. "
        "Elle est fournie à titre indicatif et peut comporter des inexactitudes : "
        "pensez à vérifier les informations."
    )

    search_criterias = st.session_state.config
    ia_analysis_content(nom, codgeo, search_criterias)
