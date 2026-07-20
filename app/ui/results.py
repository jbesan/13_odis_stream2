import streamlit as st
import pandas as pd
import logging
import config as cfg
from core.models import (
    CommuneResult,
    CommuneScoreDetail,
    SearchResultsData,
    AssociationDetail,
)
from utils.data_loader import get_app_data
from agents.utils import odis_get_bg_result, launch_background_city_analysis
from typing import List, Optional, Any
import plotly.graph_objects as go
from core import maps
from core.pdf_generator import generate_pdf_report

# Configure Logging
logger = logging.getLogger("ui.results")


# --- Dialog Dismiss Callbacks (Necessary for modular UI state management) ---
def _on_ia_dialog_dismiss():
    st.session_state.active_ia_city_index = None


def _on_details_dialog_dismiss():
    st.session_state.active_details_index = None


def _on_ccas_dialog_dismiss():
    st.session_state.active_ccas_index = None


@st.dialog("Export des résultats en PDF")
def pdf_modal():
    """Dialog to handle PDF generation and download."""
    # State 1: Loading / Generating
    if (
        "pdf_modal_data" not in st.session_state
        or st.session_state.pdf_modal_data is None
    ):
        with st.spinner("Veuillez patienter, nous générons votre document..."):
            search_results = st.session_state.get("search_results")

            pdf_bytes = generate_pdf_report(
                search_results=search_results,
                config=st.session_state.config,
                processed_gdf=st.session_state.get("processed_gdf"),
            )
            st.session_state.pdf_modal_data = pdf_bytes

    # State 2: Download Ready
    if st.session_state.get("pdf_modal_data"):
        st.success("Votre document est prêt !")
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                label="Télécharger le PDF",
                data=st.session_state.pdf_modal_data,
                file_name="synthese_jaccueille.pdf",
                mime="application/pdf",
                icon=":material/picture_as_pdf:",
                type="primary",
                width="stretch",
            )
        with col2:
            if st.button("Fermer", width="stretch"):
                st.session_state.pdf_modal_data = None
                st.rerun()


def render_export_pdf_button(h: str):
    """Component to handle background status and PDF triggering."""
    if not h:
        return

    refiner_res = odis_get_bg_result(h)

    # 🧪 SOTA: Robust check for BOTH Scorer and Enrichment completion
    has_pitches = isinstance(refiner_res, dict) and "pitches" in refiner_res
    has_enrichment = isinstance(refiner_res, dict) and "enrichment" in refiner_res

    if has_pitches and has_enrichment:
        if st.button(
            "Exporter résultats",
            icon=":material/picture_as_pdf:",
            type="secondary",
            width="stretch",
            key=f"pdf_btn_{h}",  # Keyed by hash to ensure fresh button per search
        ):
            pdf_modal()
    elif isinstance(refiner_res, dict) and "pitches_error" in refiner_res:
        st.error(refiner_res["pitches_error"])
    else:
        # Still running or not started
        st.button(
            "Patientez...",
            disabled=True,
            icon=":material/hourglass_empty:",
            type="secondary",
            width="stretch",
        )


# --- Module Level Fragments for Stability ---
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
    import streamlit as st

    if st.session_state.get("config"):
        st.session_state.config.odis_brief = _get_field(
            final_state_results, "odis_brief", st.session_state.config.odis_brief
        )

    # 2. Find and update the specific focus city
    new_results = _get_field(final_state_results, "results", [])
    for city_data in new_results:
        city_codgeo = _get_field(city_data, "codgeo")
        if str(city_codgeo) == str(codgeo):
            new_synth = _get_field(city_data, "odis_synthesis", [])
            if new_synth:
                commune.odis_synthesis = new_synth

            expert_data = _get_field(city_data, "expert_analysis", {})
            if expert_data and isinstance(expert_data, dict):
                commune.expert_analysis.update(expert_data)

            new_pitch = _get_field(city_data, "refiner_pitch")
            if new_pitch:
                commune.refiner_pitch = new_pitch
            break


# --- Module Level Polling Helpers ---
# Removed nested fragments to avoid scope issues. Polling is now handled in the parent container.


# --- Module Level Polling Helpers ---
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
        elapsed = time.time() - start_time
        progress = min(1.0, elapsed / 30.0)
        st.progress(progress, text="Préparation de la synthèse (~30 secondes)...")
    elif status_data.get("status") == "error":
        st.error(f"Erreur d'analyse : {status_data.get('error')}")
        if st.button("Réessayer"):
            del st.session_state.odis_bg_store[task_key]
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
    elif status_data and status_data.get("status") == "error":
        st.error(f"Erreur de l'agent : {status_data.get('error')}")
        if chat_task_key in st.session_state:
            del st.session_state[chat_task_key]
        st.rerun()
    else:
        with st.chat_message("assistant"):
            st.write("✨ _Recherche de la réponse en cours (Job Hunter / Scouts)..._")


@st.fragment(run_every=3.0)
def polling_associations_fragment(commune: CommuneResult, h: Optional[str]):
    """Fragment that automatically polls for association enrichment every 3s."""
    inc_data = commune.inclusion
    if h and not inc_data.asso_inclusion_list_by_cat:
        bg_res = odis_get_bg_result(h)
        if isinstance(bg_res, dict) and "enrichment" in bg_res:
            enrich_data = bg_res["enrichment"].get(str(commune.codgeo))
            if enrich_data:
                # We use the new AssociationDetail model instead of raw strings
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
                    len(l) for l in inc_data.asso_inclusion_list_by_cat.values()
                )
                st.rerun()  # Trigger dialog rerun to reveal content

    total_assos = (inc_data.asso_refugee_count or 0) + (
        inc_data.asso_inclusion_count or 0
    )
    if total_assos > 0:
        st.info(f"**{total_assos} associations** actives.")
        categories_to_show = {}
        if inc_data.asso_refugee_list:
            categories_to_show["Intégration des réfugiés & migrants"] = (
                inc_data.asso_refugee_list
            )
        if inc_data.asso_inclusion_list_by_cat:
            for cat, asso_list in inc_data.asso_inclusion_list_by_cat.items():
                categories_to_show[cat] = asso_list

        for cat, asso_list in sorted(categories_to_show.items()):
            with st.expander(f"**{cat}** ({len(asso_list)})", expanded=False):
                for asso in asso_list:
                    url = f"https://www.assoce.fr/waldec/{asso.id}" if asso.id else "#"
                    st.markdown(
                        f"**{asso.name}**: {asso.description or ''} [Détails]({url})"
                    )
    elif h and (not odis_get_bg_result(h) or "enrichment" not in odis_get_bg_result(h)):
        st.write("⌛ _Chargement des associations..._")
    else:
        st.info("Aucune association répertoriée.")


@st.fragment(run_every=3.0)
def polling_jobs_fragment(commune: CommuneResult, h: Optional[str]):
    """Fragment that automatically polls for France Travail job enrichment every 3s."""
    emp_data = commune.employment
    matching_total = emp_data.standard_jobs_matching_total

    if h and not emp_data.matching_job_offers:
        bg_res = odis_get_bg_result(h)
        if isinstance(bg_res, dict) and "jobs_enrichment" in bg_res:
            jobs_city_data = bg_res["jobs_enrichment"].get(str(commune.codgeo))
            if jobs_city_data:
                if jobs_city_data.get("status") == "done":
                    # We deserialize the nested list structure List[List[JobOfferDetail]]
                    from core.models import JobOfferDetail

                    raw_jobs = jobs_city_data.get("jobs", [])
                    emp_data.matching_job_offers = [
                        [JobOfferDetail.model_validate(o) for o in adult_list]
                        for adult_list in raw_jobs
                    ]
                    if "total" in jobs_city_data:
                        emp_data.standard_jobs_matching_total = jobs_city_data["total"]
                    st.rerun()  # Trigger dialog rerun to reveal content
                elif jobs_city_data.get("status") == "error":
                    # Put a dummy empty list to stop polling on error
                    emp_data.matching_job_offers = [[]]
                    st.rerun()

    bg_res = odis_get_bg_result(h) if h else None
    jobs_city_data = (
        bg_res.get("jobs_enrichment", {}).get(str(commune.codgeo))
        if isinstance(bg_res, dict) and "jobs_enrichment" in bg_res
        else None
    )

    # Helper function to render a single job offer in the premium style
    def render_job_card(offer):
        company_display = f" chez **{offer.company}**" if offer.company else ""
        st.markdown(f"**{offer.title}**{company_display}")

        # Badges line
        badges = []
        if offer.contract_label or offer.contract_type:
            badges.append(f"💼 {offer.contract_label or offer.contract_type}")
        if offer.location:
            badges.append(f"📍 {offer.location}")
        loc_insee = getattr(offer, "location_insee", None)
        if loc_insee and str(loc_insee) == str(commune.codgeo):
            badges.append("🟢 **Même commune**")
        if offer.salary:
            badges.append(f"💰 {offer.salary}")

        # New context badges (experience and work duration)
        experience = getattr(offer, "experience", None)
        if experience:
            badges.append(f"🎓 {experience}")
        work_duration = getattr(offer, "work_duration", None)
        if work_duration:
            badges.append(f"⏱️ {work_duration}")

        if badges:
            st.markdown(" | ".join(badges))

        brief = getattr(offer, "job_brief", None)
        if brief:
            st.markdown(f"{brief}")
        elif offer.description:
            st.caption(offer.description)

        # Display publication date
        date_creation = getattr(offer, "date_creation", None)
        if date_creation:
            date_str = (
                date_creation.split("T")[0] if "T" in date_creation else date_creation
            )
            st.caption(f"Publiée le : {date_str}")

        if offer.url:
            st.link_button("Voir l'Offre", offer.url, type="secondary")
        st.divider()

    if emp_data.matching_job_offers and any(emp_data.matching_job_offers):
        for i, adult_jobs in enumerate(emp_data.matching_job_offers):
            if not adult_jobs:
                continue
            matching_total_adult = len(adult_jobs)
            title = f"💼 Meilleures correspondances avec le projet de l'Adulte {i + 1}"
            with st.expander(title, expanded=True):
                # Group offers by ROME label
                from collections import defaultdict

                grouped = defaultdict(list)
                for offer in adult_jobs:
                    r_label = getattr(offer, "rome_label", None) or "Autre"
                    grouped[r_label].append(offer)

                # If we have results for more than 1 ROME code, group in nested expanders
                if len(grouped) > 1:
                    for rome_label, rome_offers in grouped.items():
                        with st.expander(
                            f"💼 {rome_label} ({len(rome_offers)})", expanded=False
                        ):
                            for offer in rome_offers:
                                render_job_card(offer)
                else:
                    # Otherwise, list them directly
                    for offer in adult_jobs:
                        render_job_card(offer)

    elif jobs_city_data and jobs_city_data.get("status") == "error":
        with st.expander("💼 Offres d'emploi directes", expanded=True):
            st.info("⚠️ Offres d'emploi temporairement indisponibles.")
    elif h and (not bg_res or "jobs_enrichment" not in bg_res or not jobs_city_data):
        st.write("⌛ _Chargement des offres d'emploi en cours..._")
    else:
        st.info("Aucune offre d'emploi directe répertoriée dans le rayon de recherche.")


def ia_analysis_content(nom: str, codgeo: str, search_criterias: Any):
    """Main component for AI synthesis, rendered inside a @st.dialog."""

    # 1. Access Single Source of Truth from unified state
    if "search_results" not in st.session_state or not st.session_state.search_results:
        st.error("Résultats introuvables.")
        return

    results: SearchResultsData = st.session_state.search_results
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

    # 3. Display Synthesis and Chat History
    history = list(commune.odis_synthesis)

    # Container for chat history
    history_container = st.container()
    with history_container:
        for msg in history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Check if a follow-up chat task is running
    chat_task_key = f"chat_active_flag_{codgeo}"

    if st.session_state.get(chat_task_key):
        polling_chat_fragment(task_key, chat_task_key, codgeo, commune)

    # 4. Handle follow-up questions
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

    st.header(f"Analyse OD&IS pour {nom}")

    search_criterias = st.session_state.config
    ia_analysis_content(nom, codgeo, search_criterias)


def ai_pitch_container(main_code: str, h: Optional[str]):
    """
    Displays the AI-generated pitch for a city.
    Assumes data is already synced into the CommuneResult model.
    """
    if "search_results" in st.session_state and st.session_state.search_results:
        commune = st.session_state.search_results.get_by_code(main_code)
        if commune and commune.refiner_pitch:
            st.markdown(commune.refiner_pitch)
            return

    st.info("✨ _L'analyse des points forts est en cours..._")


def sync_background_data(commune: CommuneResult, h: Optional[str]):
    """
    Syncs both enrichment (associations) and pitches from the background store
    back into the CommuneResult model for persistence.
    """
    if not h:
        return

    bg_res = odis_get_bg_result(h)
    if not isinstance(bg_res, dict):
        return

    # 1. Sync Enrichment (Associations)
    if "enrichment" in bg_res:
        enrich_data = bg_res["enrichment"].get(str(commune.codgeo))
        if enrich_data and not commune.inclusion.asso_inclusion_list_by_cat:
            logging.debug(f"✨ [SYNC] Associations sync for {commune.codgeo}")
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
                len(l) for l in inc_data.asso_inclusion_list_by_cat.values()
            )

    # 1b. Sync Enrichment (Job Offers)
    if "jobs_enrichment" in bg_res:
        jobs_city_data = bg_res["jobs_enrichment"].get(str(commune.codgeo))
        if (
            jobs_city_data
            and jobs_city_data.get("status") == "done"
            and not commune.employment.matching_job_offers
        ):
            logging.debug(f"✨ [SYNC] Jobs sync for {commune.codgeo}")
            emp_data = commune.employment
            from core.models import JobOfferDetail

            raw_jobs = jobs_city_data.get("jobs", [])
            emp_data.matching_job_offers = [
                [JobOfferDetail.model_validate(o) for o in adult_list]
                for adult_list in raw_jobs
            ]
            if "total" in jobs_city_data:
                emp_data.standard_jobs_matching_total = jobs_city_data["total"]

    # 2. Sync Pitches (AI analysis)
    if "pitches" in bg_res:
        pitches_data = bg_res["pitches"]
        if isinstance(pitches_data, dict):
            # A. City-specific pitch
            if "pitches" in pitches_data:
                pitch_for_city = pitches_data["pitches"].get(str(commune.codgeo))
                if pitch_for_city and not commune.refiner_pitch:
                    logging.debug(f"✨ [SYNC] Pitch sync for {commune.codgeo}")
                    commune.refiner_pitch = pitch_for_city

            # B. Global introduction (Global Pitch)
            if "global" in pitches_data and "search_results" in st.session_state:
                if not st.session_state.search_results.global_pitch:
                    st.session_state.search_results.global_pitch = pitches_data[
                        "global"
                    ]

    # 3. Sync Unified Briefing (Profile Summary)
    if "odis_brief" in bg_res and st.session_state.get("config"):
        brief_val = bg_res["odis_brief"]
        if brief_val and st.session_state.config.odis_brief != brief_val:
            logging.debug("✨ [SYNC] Unified Briefing sync")
            st.session_state.config.odis_brief = brief_val


@st.dialog(
    "Centre Communal d'Action Sociale",
    width="large",
    on_dismiss=_on_ccas_dialog_dismiss,
)
def show_ccas_dialog(index: Any):
    if (
        "search_results" not in st.session_state
        or not st.session_state.search_results
        or not st.session_state.search_results.get_by_code(index)
    ):
        st.error("Données de la ville introuvables.")
        return

    commune = st.session_state.search_results.get_by_code(index)
    codgeo = commune.codgeo
    libgeo = commune.name
    structures_df = get_app_data().get("structures_ccas", pd.DataFrame())

    target_codes = [codgeo.strip()]
    # Optional logic for binome if needed (fallback to df_all_communes)
    df_all = get_app_data().get("odis", pd.DataFrame())
    if codgeo in df_all.index:
        row = df_all.loc[codgeo]
        if "binome" in row and row["binome"] and "codgeo_binome" in row:
            target_codes.append(str(row["codgeo_binome"]).strip())

    if not structures_df.empty and "codgeo" in structures_df.columns:
        # Filter with clean string types
        subset = structures_df[structures_df["codgeo"].isin(target_codes)].copy()

        if not subset.empty:
            for _, struct in subset.iterrows():
                st.divider()
                # Layout: Commune First
                label = struct["commune"] if pd.notna(struct.get("commune")) else libgeo
                st.subheader(f"📍 {label}")

                # Name
                st.write(f"**{struct['nom']}**")

                # Address
                if pd.notna(struct.get("adresse")):
                    st.write(f"{struct['adresse']}")

                # Contact Info
                c1, c2 = st.columns(2)
                with c1:
                    if pd.notna(struct.get("telephone")):
                        st.write(f"📞 {struct['telephone']}")
                with c2:
                    if pd.notna(struct.get("courriel")):
                        # Simple email link
                        st.markdown(
                            f"✉️ [{struct['courriel']}](mailto:{struct['courriel']})"
                        )

                if pd.notna(struct.get("site_web")):
                    st.markdown(f"🌐 [Site Web]({struct['site_web']})")

        else:
            st.info(
                f"Aucune structure CCAS/CIAS référencée (avec contact) pour {libgeo}."
            )
    else:
        st.warning("Données structures non disponibles.")


@st.dialog(
    title="Détails du Territoire", width="large", on_dismiss=_on_details_dialog_dismiss
)
def show_details_dialog(index: Any):
    """Displays thematic details for a city in a large modal."""
    if (
        "search_results" not in st.session_state
        or not st.session_state.search_results
        or not st.session_state.search_results.get_by_code(index)
    ):
        st.error("Données de la ville introuvables.")
        return

    commune = st.session_state.search_results.get_by_code(index)

    if not commune:
        st.error("Détails non disponibles.")
        return

    # --- Header ---
    st.markdown(f"## 📍 {commune.name} (code INSEE: {commune.codgeo})")

    # Active search hash for background enrichment (SOTA Pattern)
    h = st.session_state.get("active_search_hash")

    # Sync background results into model if available
    sync_background_data(commune, h)

    with st.container(border=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(
                "Population",
                f"{commune.population:,}".replace(",", " "),
                help="Population totale de la commune",
            )
        with col2:
            st.metric(
                "Bassin de Vie",
                commune.name_bdv,
                help="Territoire d'influence économique et sociale",
            )
        with col3:
            st.metric(
                "Score Global",
                f"{commune.global_score * 100:.1f}%",
                help="Adéquation globale avec votre projet de vie",
            )

    # --- Helper to render scores table ---
    def render_scores_for_category(
        category_key: str, scores_list: Optional[List[CommuneScoreDetail]] = None
    ):
        # category_key: emploi, logement, education, sante, inclusion, mobilite
        scores: List[CommuneScoreDetail] = (
            scores_list
            if scores_list is not None
            else commune.scores.get(category_key, [])
        )
        if not scores:
            st.info("Aucun indicateur spécifique pour cette catégorie.")
            return

        # Filter out redundant education presence scores if we have the counts tab
        if category_key == "education" and scores_list is None:
            scores = [s for s in scores if not s.label.startswith("Présence")]

        # Sort by score_normalise desc to show strengths
        if scores_list is None:
            scores = sorted(scores, key=lambda x: x.score_normalise, reverse=True)

        with st.container(border=False):
            for s in scores:
                c_label, c_val = st.columns([3, 1])
                with c_label:
                    st.markdown(f"**{s.label}**")
                    p_val_raw = s.score_normalise
                    p_val = (
                        float(max(0.0, min(1.0, p_val_raw)))
                        if p_val_raw is not None and pd.notna(p_val_raw)
                        else 0.0
                    )

                    # Auto color based on score value
                    if p_val < 0.35:
                        bar_color = "linear-gradient(90deg, #f87171, #ef4444)"  # Soft to dark Red
                    elif p_val < 0.65:
                        bar_color = "linear-gradient(90deg, #fbbf24, #f59e0b)"  # Warm Orange/Yellow
                    else:
                        bar_color = (
                            "linear-gradient(90deg, #34d399, #10b981)"  # Emerald Green
                        )

                    st.markdown(
                        f"""
                        <div style="width: 100%; background-color: rgba(128, 128, 128, 0.15); border-radius: 4px; height: 8px; margin-top: 4px; overflow: hidden;">
                            <div style="width: {p_val * 100}%; background: {bar_color}; height: 100%; border-radius: 4px;"></div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with c_val:
                    val_display = s.valeur_kpi
                    if isinstance(val_display, (int, float)) and pd.notna(val_display):
                        if isinstance(val_display, int) and val_display > 1000:
                            st.markdown(f"### {val_display:,}".replace(",", " "))
                        else:
                            st.markdown(f"### {val_display}")
                    else:
                        st.markdown(f"### {val_display}")

                    st.caption(s.unit if s.unit and s.unit != "None" else "")
            st.markdown("<br>", unsafe_allow_html=True)  # Minor spacing

    # --- Tabs ---
    tab_emploi, tab_logement, tab_edu, tab_sante, tab_vie, tab_mob, tab_ter = st.tabs(
        [
            "💼 Emploi & Formation",
            "🏠 Logement",
            "🎓 Éducation",
            "🏥 Santé",
            "🤝 Vie Sociale & Inclusion",
            "🚉 Mobilité",
            "🛡️ Contexte Territorial",
        ]
    )

    with tab_emploi:
        employment_data = commune.employment
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/work: Opportunités")

                live_total = employment_data.standard_jobs_total
                matching_total = employment_data.standard_jobs_matching_total

                if live_total > 0:
                    st.info(
                        f"**{matching_total} postes** correspondent à votre recherche sur cette zone."
                    )

                # 1. Hydrated live France Travail job offers first
                polling_jobs_fragment(commune, h)

                # 2. SIAE matching or local listings second
                matching_siae = employment_data.inclusive_jobs_matching_summary
                if matching_siae:
                    with st.expander(
                        f"Offres par les SIAE correspondant au projet ({employment_data.inclusive_jobs_matching_total})",
                        expanded=True,
                    ):
                        for label, count in matching_siae.items():
                            st.write(
                                f"• **{label}** : {count} offre{'s' if count > 1 else ''}"
                            )
                elif employment_data.inclusive_jobs_total > 0:
                    with st.expander(
                        f"Toutes les offres par les SIAE locales ({employment_data.inclusive_jobs_total})",
                        expanded=False,
                    ):
                        for (
                            label,
                            count,
                        ) in employment_data.inclusive_jobs_summary.items():
                            st.write(
                                f"• **{label}** : {count} offre{'s' if count > 1 else ''}"
                            )

                # 3. Métiers recherchés at the bottom
                with st.expander("Métiers les plus recherchés", expanded=False):
                    top_professions = employment_data.top_professions
                    if top_professions:
                        for m in top_professions:
                            st.write(f"• {m}")
                    else:
                        st.write("Pas de données détaillées.")

                # 4. Formations proposées at the bottom
                with st.expander("Formations proposées", expanded=False):
                    training_programs = employment_data.training_programs
                    if training_programs:
                        pref_forms = []
                        for k in st.session_state:
                            if k.startswith("ui_formations_adult"):
                                val = st.session_state[k]
                                if isinstance(val, list):
                                    pref_forms.extend(val)
                                elif isinstance(val, str) and val:
                                    pref_forms.append(val)
                        unique_prefs = set(str(p).lower() for p in pref_forms)
                        for label in training_programs:
                            is_pref = any(p in label.lower() for p in unique_prefs)
                            icon = "⭐ " if is_pref else ""
                            st.write(f"• {icon}{label}")
                    else:
                        st.info(
                            "Aucune formation spécifique listée pour ce territoire."
                        )

        with c2:
            st.markdown("#### :material/monitoring: Indicateurs Emploi")
            render_scores_for_category("emploi")

    with tab_logement:
        housing_data = commune.housing
        c1, c2 = st.columns([1, 1], gap="medium")

        # Split housing indicators equally
        housing_scores = commune.scores.get("logement", [])
        housing_scores = sorted(
            housing_scores, key=lambda x: x.score_normalise, reverse=True
        )

        mid = (len(housing_scores) + 1) // 2
        scores_left = housing_scores[:mid]
        scores_right = housing_scores[mid:]

        with c1:
            st.markdown("#### :material/home: Indicateurs Logement")
            j_count = housing_data.host_count
            if j_count > 0:
                st.info(
                    f"**{int(j_count)} accueillants** J'Accueille identifiés dans le bassin de vie."
                )
            render_scores_for_category("logement", scores_list=scores_left)

        with c2:
            st.markdown("#### :material/home: Indicateurs Logement")
            render_scores_for_category("logement", scores_list=scores_right)

    with tab_edu:
        education_data = commune.education
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/school: Établissements")
                facility_details = education_data.facility_details
                if facility_details:
                    for cat, names in sorted(facility_details.items()):
                        items = sorted(list(set([n for n in names if pd.notna(n)])))
                        if items:
                            with st.expander(f"{cat} ({len(items)})", expanded=False):
                                for name in items:
                                    st.write(f"• {name}")
                else:
                    st.info("Aucune information détaillée sur les établissements.")
        with c2:
            st.markdown("#### :material/analytics: Indicateurs Éducation")
            render_scores_for_category("education")

    with tab_sante:
        health_data = commune.health
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/medical_services: Établissements de Santé")
                facility_details = health_data.facility_details
                if facility_details:
                    for cat, names in sorted(facility_details.items()):
                        items = sorted(list(set([n for n in names if pd.notna(n)])))
                        if items:
                            with st.expander(f"{cat} ({len(items)})", expanded=False):
                                for name in items:
                                    st.write(f"• {name}")
                else:
                    st.info("Aucune information détaillée sur les structures de santé.")
        with c2:
            st.markdown("#### :material/medical_services: Indicateurs Santé")
            render_scores_for_category("sante")

    with tab_vie:
        inclusion_data = commune.inclusion
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/volunteer_activism: Services d'Inclusion")
                with st.expander("Consulter les services disponibles", expanded=False):
                    services_grouped = inclusion_data.services_grouped
                    if services_grouped:
                        for thematique, names in sorted(services_grouped.items()):
                            items = sorted(list(set([n for n in names if pd.notna(n)])))
                            if items:
                                with st.expander(
                                    f"{thematique} ({len(items)})", expanded=False
                                ):
                                    for name in items:
                                        st.write(f"• {name}")
                    else:
                        st.info("Aucun service spécifique référencé.")

                st.markdown("#### :material/groups: Associations de l'inclusion")

                polling_associations_fragment(commune, h)

        with c2:
            st.markdown("#### :material/diversity_3: Indicateurs Inclusion")
            render_scores_for_category("inclusion")

    with tab_mob:
        c1, c2 = st.columns([1, 1], gap="medium")
        mob_scores = commune.scores.get("mobilite", [])
        mob_scores = sorted(mob_scores, key=lambda x: x.score_normalise, reverse=True)
        mid = (len(mob_scores) + 1) // 2
        scores_left = mob_scores[:mid]
        scores_right = mob_scores[mid:]

        with c1:
            st.markdown("#### :material/commute: Mobilité")
            render_scores_for_category("mobilite", scores_list=scores_left)
        with c2:
            st.markdown("#### :material/commute: Mobilité")
            render_scores_for_category("mobilite", scores_list=scores_right)

    with tab_ter:
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            st.markdown("#### :material/security: Contexte Territorial")
            if commune.territoire.ter_insecurite:
                st.info(
                    f"🚨 **Sécurité** : {commune.territoire.ter_insecurite:.1f} crimes+délits pour 1000 hab. (Moyenne départementale)."
                )
            
            if commune.territoire.maire_extreme_droite:
                st.warning("⚠️ **Municipalité** : Le maire actuel est classé à l'extrême droite.")

            if commune.territoire.electoral_history:
                try:
                    import json
                    history = json.loads(commune.territoire.electoral_history)
                    if history:
                        st.markdown("##### 🗳️ Historique Électoral (5 derniers scrutins)")
                        table_rows = []
                        for item in history:
                            pct_str = f"**{item['percentage']:.1f}%**"
                            table_rows.append(f"| {item['election']} | {item['nuance']} | {pct_str} |")
                        
                        table_content = "\n".join([
                            "| Scrutin | Nuance Majoritaire | Score |",
                            "| :--- | :--- | :--- |",
                        ] + table_rows)
                        st.markdown(table_content)
                except Exception as e:
                    st.caption("Erreur lors du chargement de l'historique électoral.")
        with c2:
            st.markdown("#### :material/security: Indicateurs Territoriaux")
            render_scores_for_category("territoire")


def _result_highlight_callback(index: int) -> None:
    """Callback to handle highlighting a result by its index in the top results."""
    search_results: SearchResultsData = st.session_state.get("search_results")
    if not search_results:
        return

    if index == -1:
        if not search_results.commune_pressentie:
            return
        commune = search_results.commune_pressentie
    else:
        if index < 0 or index >= len(search_results.results):
            return
        commune = search_results.results[index]

    is_highlighted, highlighted_rank = st.session_state.highlighted_result

    # If the same button is clicked again, un-highlight it
    if is_highlighted and index == highlighted_rank:
        st.session_state.highlighted_result = [False, None]
        st.session_state.zoom = None
    else:
        st.session_state.highlighted_result = [True, index]
        c_pt = maps._get_geom(
            commune, "centroid", gdf_context=st.session_state.processed_gdf
        )
        if c_pt:
            st.session_state.center = [c_pt.y, c_pt.x]
        st.session_state.zoom = cfg.DETAIL_MAP_ZOOM


def _on_result_feedback(cid: str, c_name: str, score: float, fb_key: str) -> None:
    """Callback for st.feedback to submit relevance directly to BQ."""
    val = st.session_state.get(fb_key)
    if val is not None:
        # Avoid duplicate submission for the same selection state during reruns/fragment updates
        submission_key = f"last_submitted_{fb_key}"
        if st.session_state.get(submission_key) == val:
            return

        try:
            from ui.feedback import _submit_to_bq
            import json

            context = json.dumps({"codgeo": cid, "libgeo": c_name, "score": score})
            # st.feedback values are 0-4 (5 faces), we map to 1-5 for BQ
            if _submit_to_bq("Result Relevance", str(val + 1), context=context):
                st.session_state[submission_key] = val
                logger.info(f"✨ Feedback submitted for {c_name} ({cid}): {val + 1}")
        except Exception as e:
            logger.error(f"Failed to submit result feedback: {e}")


def render_global_pitch(h: Optional[str] = None):
    """Renders the global intro pitch if available, or a loading message."""
    search_results: SearchResultsData = st.session_state.get("search_results")
    if not search_results:
        return

    if not h:
        h = st.session_state.get("active_search_hash")

    bg_res = odis_get_bg_result(h) if h else None
    refiner_status = bg_res.get("status_refiner") if isinstance(bg_res, dict) else None
    is_ready = refiner_status == "done"

    if not is_ready:
        st.info("✨ _Analyse stratégique des résultats en cours..._")
        return

    if bg_res and "pitches" in bg_res:
        if not search_results.global_pitch:
            search_results.global_pitch = bg_res["pitches"].get("global", "")

    if search_results.global_pitch:
        st.markdown(
            f"""
        <div style="background-color: #f8f9fa; padding: 20px; border-radius: 10px; border-left: 5px solid #006268; margin-bottom: 20px;">
            {search_results.global_pitch}
        </div>
        """,
            unsafe_allow_html=True,
        )


def display_results_list(display_gdf: Optional[pd.DataFrame] = None) -> None:
    """Renders the list of search results or the detailed view for the highlighted result."""
    h = st.session_state.get("active_search_hash")
    search_results: SearchResultsData = st.session_state.get("search_results")

    if not search_results or not search_results.results:
        st.info("Aucun résultat à afficher.")
        return

    # Handle Active Dialogs (at page/list rendering level)
    if st.session_state.get("active_ia_city_index") is not None:
        show_ia_analysis_dialog(st.session_state.active_ia_city_index)

    if st.session_state.get("active_details_index") is not None:
        show_details_dialog(st.session_state.active_details_index)

    if st.session_state.get("active_ccas_index") is not None:
        show_ccas_dialog(st.session_state.active_ccas_index)

    st.markdown(
        '<style> [class*="st-key-button_top"] .stButton button div, [class*="st-key-button_top"] .stButton button p { justify-content: flex-start !important; text-align: left !important; width: 100%; } </style>',
        unsafe_allow_html=True,
    )

    is_highlighted, highlighted_rank = st.session_state.highlighted_result

    # UI Guardrail (F-58): Check if Refiner is done
    bg_res = odis_get_bg_result(h) if h else None
    refiner_status = bg_res.get("status_refiner") if isinstance(bg_res, dict) else None
    is_ready = refiner_status == "done"

    if is_ready:
        # Sync global data once (handled in render_global_pitch now, but keeping brief sync here for safety)
        if bg_res and "odis_brief" in bg_res and st.session_state.get("config"):
            brief_val = bg_res["odis_brief"]
            if brief_val and st.session_state.config.odis_brief != brief_val:
                st.session_state.config.odis_brief = brief_val
                st.rerun()
    # Shortlisted City (Ville Pressentie) Button (Feature F-61)
    if search_results.commune_pressentie:
        st.markdown(
            """
        <style>
        [class*="st-key-btn_pressentie"] .stButton button div, [class*="st-key-btn_pressentie"] .stButton button p {
            justify-content: flex-start !important; 
            text-align: left !important; 
            width: 100%;
        }
        div[class*="st-key-btn_pressentie"] button {
            background-color: #F5D819 !important;
            color: #1B4429 !important;
            font-weight: bold !important;
            border: 1px solid #F5D819 !important;
        }
        div[class*="st-key-btn_pressentie"] button:hover {
            background-color: #E2C617 !important;
            color: #1B4429 !important;
        }
        </style>
        """,
            unsafe_allow_html=True,
        )

        p_commune = search_results.commune_pressentie
        title_p = f"**{p_commune.global_score * 100:.1f}%**  |  {p_commune.name} (Ville Souhaitée)"

        st.button(
            title_p,
            on_click=_result_highlight_callback,
            args=(-1,),
            width="stretch",
            key="btn_pressentie",
            type="primary",
            icon=":material/push_pin:",
            disabled=not is_ready,
        )

        if is_highlighted and highlighted_rank == -1:
            _display_result_details(p_commune, is_ready)

        st.text("Alternatives : ")

    for i, commune in enumerate(search_results.results):
        title = f"**{commune.global_score * 100:.1f}%**  |  {commune.name}"

        st.button(
            title,
            on_click=_result_highlight_callback,
            args=(i,),
            width="stretch",
            key=f"button_top{i + 1}",
            type="primary",
            icon=f":material/counter_{i + 1}:",
            disabled=not is_ready,
        )

        # Check if this row's index matches the highlighted index
        if is_highlighted and i == highlighted_rank:
            _display_result_details(commune, is_ready)


def _display_result_details(commune: CommuneResult, is_ready: bool = False) -> None:
    """Displays the detailed information for a single search result (Commune)."""
    h = st.session_state.get("active_search_hash")

    with st.container(border=True):
        # --- Pitch ---
        population = f"{commune.population:,}".replace(",", " ")
        libgeo = commune.name
        score_percent = f"{commune.global_score * 100:.1f}%"

        st.markdown(
            f"**{libgeo}** ({population} habitants) fait partie du bassin de vie de : **{commune.name_bdv}**.  \nLa correspondance avec le projet est évaluée à **{score_percent}**."
        )

        # Sync background results into model if available
        sync_background_data(commune, h)

        # --- AI Pitch Fragment ---
        ai_pitch_container(commune.codgeo, h)

        # F-IA: AI Dialog Trigger (Session State based)
        if not cfg.is_ai_free_mode():
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                st.markdown(
                    '<style> [class*="st-key-btn_ia"] .stButton button { background-color: #F5D819; color: #1B4429; } </style>',
                    unsafe_allow_html=True,
                )

                # Premium Guardrail (F-IA): Verify if background hydrations (jobs & associations) are completed
                jobs_ready = False
                assos_ready = False
                if h:
                    bg_res = odis_get_bg_result(h)
                    if isinstance(bg_res, dict):
                        # Check jobs hydration status
                        jobs_city_data = bg_res.get("jobs_enrichment", {}).get(
                            str(commune.codgeo)
                        )
                        if jobs_city_data and jobs_city_data.get("status") in [
                            "done",
                            "error",
                        ]:
                            jobs_ready = True

                        # Check associations hydration status
                        enrich_data = bg_res.get("enrichment", {}).get(
                            str(commune.codgeo)
                        )
                        if enrich_data is not None:
                            assos_ready = True

                if not is_ready:
                    btn_label = "Analyse Avancée (Calcul...)"
                    btn_disabled = True
                elif not jobs_ready or not assos_ready:
                    btn_label = "Analyse Avancée (Préparation...)"
                    btn_disabled = True
                else:
                    btn_label = "Analyse Avancée"
                    btn_disabled = False

                if st.button(
                    btn_label,
                    key=f"btn_ia_comm_{commune.codgeo}",
                    icon=":material/bolt:",
                    width="content",
                    type="primary",
                    disabled=btn_disabled,
                ):
                    st.session_state.active_ia_city_index = commune.codgeo
                    st.rerun()

        # --- Radar Chart with Comparison ---
        all_cats = [
            "emploi",
            "logement",
            "education",
            "sante",
            "inclusion",
            "mobilite",
            "territoire",
        ]
        cat_map = {
            "emploi": "employment",
            "logement": "housing",
            "education": "education",
            "sante": "health",
            "inclusion": "inclusion",
            "mobilite": "mobility",
            "territoire": "territoire",
        }

        config = st.session_state.get("config")
        if config and hasattr(config, "active_categories") and config.active_categories:
            active_cats = [
                cat
                for cat in all_cats
                if cat in config.active_categories or cat in ["mobilite", "territoire"]
            ]
        else:
            active_cats = all_cats

        def get_radar_data(c: CommuneResult, active_cats: List[str]):
            label_map = {
                "emploi": "Emploi",
                "logement": "Logement",
                "education": "Éducation",
                "sante": "Santé",
                "inclusion": "Inclusion",
                "mobilite": "Mobilité",
                "territoire": "Territoire",
            }
            labels = [label_map.get(cat, cat.capitalize()) for cat in active_cats]

            vals = []
            for cat in active_cats:
                attr_name = cat_map.get(cat, cat)
                data = getattr(c, attr_name, None)
                if data and hasattr(data, "cat_score"):
                    val = float(data.cat_score) if data.cat_score is not None else 0.0
                    vals.append(val * 100)
                else:
                    vals.append(0.0)

            if vals:
                vals.append(vals[0])
                labels.append(labels[0])
            return labels, vals

        labels_target, vals_target = get_radar_data(commune, active_cats)

        search_results: SearchResultsData = st.session_state.get("search_results")

        fig = go.Figure()

        # Add trace for target city (Green)
        fig.add_trace(
            go.Scatterpolar(
                r=vals_target,
                theta=labels_target,
                fill="toself",
                name=libgeo,
                fillcolor="rgba(0, 98, 104, 0.5)",
                line=dict(color="#006268"),
                hovertemplate="%{theta}: %{r:.1f}%<extra></extra>",
            )
        )

        # Add trace for current city (Blue) if available
        if search_results and search_results.current_geo:
            _, vals_current = get_radar_data(search_results.current_geo, active_cats)
            current_name = search_results.current_geo.name or "Actuel"

            fig.add_trace(
                go.Scatterpolar(
                    r=vals_current,
                    theta=labels_target,
                    fill="toself",
                    name=current_name,
                    fillcolor="rgba(31, 119, 180, 0.4)",
                    line=dict(color="#1f77b4"),
                    hovertemplate="%{theta}: %{r:.1f}%<extra></extra>",
                )
            )

        fig.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            showlegend=True,
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
            ),
            margin=dict(l=50, r=50, t=50, b=50),
        )

        st.plotly_chart(fig, width="stretch", config=None)

        current_geo = search_results.current_geo if search_results else None
        current_label = current_geo.name if current_geo else "votre ville"
        st.caption(
            f"**Comparaison des profils** : la zone verte représente **{commune.name}**, la zone bleue **{current_label}**. Une plus grande surface indique une meilleure adéquation avec vos critères.",
            text_alignment="center",
        )
        st.space("small")

        c1, c2 = st.columns(2)
        with c1:
            if st.button(
                "En savoir plus",
                key=f"btn_details_comm_{commune.codgeo}",
                width="stretch",
            ):
                st.session_state.active_details_index = commune.codgeo
                st.rerun()
        with c2:
            if st.button(
                "Contact local",
                key=f"btn_ccas_commune_{commune.codgeo}",
                icon=":material/phone:",
                type="secondary",
                width="stretch",
            ):
                st.session_state.active_ccas_index = commune.codgeo
                st.rerun()

        st.divider()
        with st.container(
            horizontal=True,
            horizontal_alignment="center",
            key=f"faces_feedback_container_{commune.codgeo}",
        ):
            st.text("Évaluez la pertinence de ce résultat")
            fb_key = f"fb_result_{commune.codgeo}"
            st.feedback(
                "faces",
                key=fb_key,
                on_change=_on_result_feedback,
                args=(commune.codgeo, commune.name, commune.global_score, fb_key),
                width="content",
            )
