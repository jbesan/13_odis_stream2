import logging
import pandas as pd
import streamlit as st

from core import maps_deck
from core.models import SearchResultsData
from services.app_session import AppSession
from services.search_controller import SearchController
from ui import forms as ui_forms
from ui import page_shell
from ui import results as ui_results
from ui.form_state import FormState
from utils import data_loader

logger = logging.getLogger(__name__)

st.set_page_config(page_title="OD&IS", page_icon="👋", layout="wide")

# Full-bleed edge-to-edge layout for results map with comprehensive line-by-line comments
st.markdown(
    """
    <style>
    /* ==========================================================================
       1. VERROUILLAGE GLOBAL DU SCROLL & SUPPRESSION DES GAPS
       ========================================================================== */
    html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewBlockContainer"] {
        overflow: hidden !important;          /* Empêche l'apparition des barres de défilement globales du navigateur */
        height: 100vh !important;            /* Force la page à occuper exactement 100% de la hauteur de l'écran */
        max-height: 100vh !important;        /* Empêche tout dépassement vertical au survol ou lors des animations */
    }



    /* ==========================================================================
       2. CONTENEUR PRINCIPAL STREAMLIT (Zone de travail à droite de la sidebar)
       ========================================================================== */
    .stMainBlockContainer,
    div[data-testid="stMainBlockContainer"] {
        padding: 0 !important;                /* Supprime les marges blanches internes par défaut de Streamlit */
        margin: 0 !important;                 /* Supprime les marges externes */
        max-width: 100% !important;           /* Permet d'occuper 100% de la largeur disponible */
        height: 100vh !important;            /* Hauteur totale 100vh */
        max-height: 100vh !important;        /* Bloque la hauteur maximale */
        overflow: hidden !important;          /* Aucun débordement possible en dehors du conteneur */
        position: relative !important;        /* Repère de référence pour le positionnement absolu des panneaux flottants */
    }

    /* Supprime l'espace vide en haut lié au header Streamlit par défaut */
    div[data-testid="stAppViewBlockContainer"] > div:first-child {
        padding-top: 0 !important;            /* 0 espace au-dessus de la carte */
    }

    /* ==========================================================================
       3. CARTE WEBGL PYDECK (Fond d'écran 100% plein écran)
       ========================================================================== */
    div:has(> div[data-testid="stPydeckChart"]),
    div[data-testid="stPydeckChart"],
    div[data-testid="stPydeckChart"] > div,
    div[data-testid="stPydeckChart"] iframe,
    div[data-testid="stPydeckChart"] canvas {
        position: absolute !important;        /* Place la carte en arrière-plan absolu */
        top: 0 !important;                    /* Commence tout en haut (0px) */
        left: 0 !important;                   /* Commence tout à gauche (0px) */
        width: 100% !important;               /* Étirement sur 100% de la largeur */
        height: 100vh !important;            /* Étirement sur 100% de la hauteur de l'écran */
        min-height: 100vh !important;        /* Hauteur minimale garantie */
        max-height: 100vh !important;        /* Hauteur maximale verrouillée */
        z-index: 1 !important;                /* Niveau de profondeur : 1 (derrière les éléments flottants) */
        border-radius: 0 !important;          /* Bords nets sans arrondi pour le fond */
    }

    /* ==========================================================================
       4. NEUTRALISATION DES ENVELOPPES STREAMLIT (Supprime l'effet boîte dans boîte)
       ========================================================================== */
    /* Les wrappers générés automatiquement par Streamlit deviennent invisibles et sans épaisseur */
    div:has(> div[class*="st-key-top_pills_bar"]),
    div:has(> div[class*="st-key-results_floating_panel"]),
    div:has(> div[class*="st-key-legend_floating_box"]) {
        position: absolute !important;
        height: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
    }

    /* Les wrappers générés automatiquement par Streamlit deviennent invisibles et sans épaisseur */
    div:has(> div[class*="st-key-top_pills_bar"]){
        width: 15% !important;
    }
    div:has(> div[class*="st-key-results_floating_panel"]){
        width: 30% !important;
    }
    div:has(> div[class*="st-key-legend_floating_box"]) {
        width: 30% !important;
    }

    /* Neutralise les sous-wrappers internes créés par stLayoutWrapper / stVerticalBlockBorderWrapper */
    div[class*="st-key-top_pills_bar"] div[data-testid="stLayoutWrapper"],
    div[class*="st-key-top_pills_bar"] div[data-testid="stVerticalBlockBorderWrapper"],
    div[class*="st-key-results_floating_panel"] div[data-testid="stLayoutWrapper"],
    div[class*="st-key-results_floating_panel"] div[data-testid="stVerticalBlockBorderWrapper"],
    div[class*="st-key-legend_floating_box"] div[data-testid="stLayoutWrapper"],
    div[class*="st-key-legend_floating_box"] div[data-testid="stVerticalBlockBorderWrapper"] {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 !important;
        margin: 0 !important;
    }

    /* ==========================================================================
       5. BOÎTE FLOTTANTE 1 : PASTILLES DE COUCHES (Top-Left)
       ========================================================================== */
    div[class*="st-key-top_pills_bar"] {
        position: absolute !important;        /* Flotte au-dessus de la carte */
        top: 1rem !important;                 /* Distance depuis le haut */
        left: 70rem !important;              /* Marge gauche par rapport au bord */
        z-index: 1000 !important;             /* Profondeur élevée pour rester au-dessus de la carte */
        background: rgba(255, 255, 255, 0.95) !important; /* Fond blanc opaque à 95% */
        backdrop-filter: blur(14px) !important;          /* Effet de flou verre dépoli */
        -webkit-backdrop-filter: blur(14px) !important;  /* Compatibilité Safari/WebKit */
        border-radius: 30px !important;       /* Forme de pilule arrondie */
        padding: 6px 14px !important;         /* Espacement interne */
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.12), 0 0 0 1px rgba(0, 0, 0, 0.06) !important; /* Ombre douce */
        border: none !important;              /* Pas de double bordure */
        height: auto !important;              /* Hauteur automatique selon le contenu */
        margin: 0 !important;                 /* Aucune marge externe */
    }

    /* ==========================================================================
       6. BOÎTE FLOTTANTE 2 : LÉGENDE DE LA CARTE (Top-Right)
       ========================================================================== */
    div[class*="st-key-legend_floating_box"] {
        position: absolute !important;        /* Flotte au-dessus de la carte */
        top: 1rem !important;                 /* Distance depuis le haut */
        right: 1.5rem !important;             /* Alignement en haut à droite */
        z-index: 1000 !important;             /* Même niveau que la barre de pills */
        background: rgba(255, 255, 255, 0.95) !important; /* Fond blanc opaque à 95% */
        backdrop-filter: blur(14px) !important;          /* Effet de flou verre dépoli */
        -webkit-backdrop-filter: blur(14px) !important;  /* Compatibilité Safari/WebKit */
        border-radius: 30px !important;       /* Forme de pilule arrondie */
        padding: 8px 18px !important;         /* Espacement interne */
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.12), 0 0 0 1px rgba(0, 0, 0, 0.06) !important; /* Ombre douce */
        border: none !important;              /* Pas de bordure parasite */
        height: auto !important;              /* Hauteur automatique selon le contenu */
        margin: 0 !important;                 /* Aucune marge externe */
    }

    /* ==========================================================================
       7. BOÎTE FLOTTANTE 3 : VOLET DE RÉSULTATS (Top 5 + Accordéon à gauche)
       ========================================================================== */
    div[class*="st-key-results_floating_panel"] {
        position: absolute !important;        /* Flotte au-dessus de la carte */
        top: 4.8rem !important;               /* Position sous la barre de pastilles */
        left: 1.5rem !important;              /* Alignement sur la même marge gauche */
        width: 450px !important;              /* Largeur fixe du panneau de résultats */
        max-width: 90vw !important;           /* Sécurité sur petits écrans */
        max-height: calc(100vh - 6rem) !important; /* Hauteur maximale dynamique */
        overflow-y: auto !important;          /* Active le défilement vertical uniquement si nécessaire */
        overflow-x: hidden !important;        /* Désactive le défilement horizontal */
        z-index: 999 !important;              /* Profondeur sous la barre du haut mais au-dessus de la carte */
        background: rgba(255, 255, 255, 0.95) !important; /* Fond blanc translucide */
        backdrop-filter: blur(16px) !important;          /* Effet de flou verre dépoli */
        -webkit-backdrop-filter: blur(16px) !important;  /* Compatibilité Safari */
        border-radius: 16px !important;       /* Arrondi des angles du volet */
        padding: 14px 16px !important;        /* Espacement intérieur unique */
        box-shadow: 0 20px 40px -15px rgba(0, 0, 0, 0.22), 0 0 0 1px rgba(0, 0, 0, 0.08) !important; /* Ombre nette unique */
        border: none !important;              /* Pas de bordure parasite */
        margin: 0 !important;                 /* Aucune marge parasite */
    }

    /* ==========================================================================
       8. BARRE DE DÉFILEMENT DU VOLET DE RÉSULTATS (Design sobre et discret)
       ========================================================================== */
    div[class*="st-key-results_floating_panel"]::-webkit-scrollbar {
        width: 5px;                           /* Épaisseur de l'ascenseur (fin et discret) */
    }
    div[class*="st-key-results_floating_panel"]::-webkit-scrollbar-track {
        background: transparent;              /* Fond de la piste invisible */
    }
    div[class*="st-key-results_floating_panel"]::-webkit-scrollbar-thumb {
        background: rgba(0, 0, 0, 0.2);       /* Couleur du curseur de défilement (gris translucide) */
        border-radius: 4px;                   /* Bouts arrondis du curseur */
    }

    /* ==========================================================================
       9. RESPONSIVE / PETITS ÉCRANS (Tablettes et Mobiles < 960px)
       ========================================================================== */
    @media (max-width: 960px) {
        div[class*="st-key-top_pills_bar"],
        div[class*="st-key-legend_floating_box"],
        div[class*="st-key-results_floating_panel"] {
            position: static !important;      /* Repasse en affichage vertical standard sur petit écran */
            width: 100% !important;           /* Occupe toute la largeur disponible */
            max-width: 100% !important;       /* Pas de restriction de largeur */
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

page_shell.enter_page("Resultats", handle_shared_search=True)


# --- Session/controller convention ---
app_session = AppSession(st.session_state)
app_session.ensure_result_view()
search_controller = SearchController(app_session)


# --- PDF Modal Execution (moved to bottom for reliability) ---

is_immutable_snapshot = bool(st.session_state.get("immutable_shared_snapshot"))
is_editing_snapshot = bool(st.session_state.get("shared_snapshot_editing"))

# A snapshot is self-contained. Only a live search or an explicit fork loads
# the complete release, including the referentials dataset.
if is_immutable_snapshot and not is_editing_snapshot:
    data_loader.initialize_session_state()
    app_data = None
else:
    with st.spinner("Chargement des indicateurs et données territoriales..."):
        app_data = data_loader.ensure_data_initialized()

# This page deliberately does not render the form except inside the dialog.
# Keep native widget keys alive across full Results-page reruns so Streamlit's
# multipage cleanup cannot turn an unsaved draft back into defaults.
if not is_immutable_snapshot or is_editing_snapshot:
    FormState(st.session_state).preserve_widgets_across_steps()

search_results: SearchResultsData = st.session_state.get("search_results")


def run_search() -> None:
    """Collect the draft and delegate the complete lifecycle to the controller."""
    complete_data = data_loader.ensure_data_initialized()
    config = ui_forms.create_search_criterias_from_inputs(complete_data)
    search_controller.execute(config, complete_data)


def prepare_search_criteria_editor(complete_data: dict) -> None:
    """Restore the active search exactly once before opening its editor."""
    active_config = st.session_state.get("config")
    if active_config is None:
        return
    FormState(st.session_state).prepare_editor(
        active_config,
        source_hash=active_config.compute_hash(),
        app_data=complete_data,
    )


@st.dialog(
    "Modifier les critères de recherche",
    width="large",
    icon=":material/edit:",
    on_dismiss="rerun",
)
def edit_search_criteria_dialog(complete_data: dict) -> None:
    """Edit widget state without rerunning the results page or Folium map."""
    ui_forms.display_input_tabs(complete_data)
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button(
            "Relancer la recherche",
            type="primary",
            icon=":material/search:",
            key="rerun_search_from_criteria_editor",
        ):
            run_search()
            st.rerun()


# Submit from the form always replaces a prior result with the current draft.
if st.session_state.get("form_completed"):
    run_search()
    st.session_state["form_completed"] = False

# --- UI LAYOUT ---


def action_buttons_container_static(h: str):
    ui_results.render_export_pdf_button(h)
    ui_results.render_share_search_button(
        h=h, button_text="Partager les résultats", key_prefix="sidebar_share"
    )


# Sidebar
with st.sidebar:
    page_shell.render_sidebar_logo()

    st.write("")
    st.markdown(
        "Découvrez les lieux de vie correspondant le mieux au projet renseigné. Les scores vous permettent de comparer facilement leurs atouts.",
        unsafe_allow_html=True,
    )
    st.divider()

    # --- Action de modification des critères ---
    if not is_immutable_snapshot or is_editing_snapshot:
        if st.button(
            "Modifier la recherche",
            width="stretch",
            type="primary",
            icon=":material/edit:",
            key="open_results_criteria_editor",
        ):
            prepare_search_criteria_editor(app_data)
            edit_search_criteria_dialog(app_data)
    else:
        if st.button(
            "Modifier les critères",
            width="stretch",
            type="primary",
            key="fork_shared_snapshot",
            icon=":material/edit:",
        ):
            search_controller.begin_snapshot_edit()
            st.rerun()


    # --- Export to PDF & Partager ---
    if st.session_state.get("search_results") is not None:
        h = st.session_state.search_results.search_hash
        # Deterministic results are immediately shareable/exportable. Optional
        # providers must not hold these actions in a permanent loading state.
        action_buttons_container_static(h)

    st.divider()
    # --- Navigation / Actions secondaires ---
    page_shell.render_primary_sidebar_actions(show_home=True, show_feedback=True)
    page_shell.render_account_sidebar_actions()


if is_immutable_snapshot:
    st.info(
        "Vous consultez une page de résultats partagée. Modifier les critères puis relancer la recherche crée une nouvelle recherche avec les données actuelles."
    )

# Global Pitch (Strategic intro + Loading state)
# if st.session_state.get('search_results'):
#     h = st.session_state.search_results.search_hash
# @st.fragment(run_every=3.0)
# def global_pitch_container(h: str):
#     ui_results.render_global_pitch(h)
# global_pitch_container(h)

# Main results & full-screen map layout
if st.session_state.get("processed_gdf") is not None:
    config = st.session_state.get("config")
    search_results = st.session_state.get("search_results")
    h = search_results.search_hash if search_results else None
    snapshot_mode = bool(st.session_state.get("immutable_shared_snapshot"))
    current_map_context = st.session_state.get("snapshot_current_map_context")
    if not isinstance(current_map_context, pd.DataFrame):
        current_map_context = st.session_state.processed_gdf

    # Default zoom if not set
    if st.session_state.get("zoom") is None:
        st.session_state["zoom"] = maps_deck.get_map_zoom(
            config.loc_search_area if config else "departement"
        )

    # 1. Floating Box 1: Pastilles de couches (Top-Left)
    selected_ids = set()
    with st.container(key="top_pills_bar"):
        pill_options = ["🥇 Top 5", "🎓 Éducation", "🏥 Santé", "🤝 Inclusion"]
        pill_id_map = {
            "🥇 Top 5": "top_5",
            "🎓 Éducation": "edu",
            "🏥 Santé": "sante",
            "🤝 Inclusion": "inc",
        }
        selected_pills = st.pills(
            "Afficher sur la carte :",
            pill_options,
            selection_mode="multi",
            default=["🥇 Top 5"],
            key="map_layers_pills",
            label_visibility="collapsed",
        )
        selected_ids = {pill_id_map[p] for p in (selected_pills or []) if p in pill_id_map}
        show_top_5 = "top_5" in selected_ids

    # 2. Floating Box 2: Légende de la carte (Top-Right)
    with st.container(key="legend_floating_box"):
        st.markdown(
            """
            <div style="display: flex; gap: 14px; font-size: 12.5px; color: #374151; align-items: center; justify-content: center; flex-wrap: wrap;">
                <span>🟢 <b>Score élevé</b></span>
                <span>🟡 <b>Score moyen</b></span>
                <span>🏛️ <b>Mairies</b></span>
                <span>🔴 <b>Top 5</b></span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    is_highlighted, highlighted_index = st.session_state.highlighted_result

    # 3. Floating Box 3: Volet de résultats (Top 5 + Accordéon à gauche)
    with st.container(key="results_floating_panel", border=False):
        if search_results and search_results.results:
            bg_res = ui_results.odis_get_bg_result(h) if h else None
            if bg_res:
                for c in search_results.results:
                    ui_results.sync_background_data(c, h)
                if search_results.commune_pressentie:
                    ui_results.sync_background_data(search_results.commune_pressentie, h)
                if "odis_brief" in bg_res and st.session_state.get("config"):
                    brief_val = bg_res["odis_brief"]
                    if brief_val and st.session_state.config.odis_brief != brief_val:
                        st.session_state.config.odis_brief = brief_val

            st.markdown("##### 🏆 Meilleurs Résultats")

            # A. Ville Souhaitée (if present)
            if search_results.commune_pressentie:
                p_commune = search_results.commune_pressentie
                is_active = is_highlighted and highlighted_index == -1
                btn_type = "primary" if is_active else "secondary"
                score_pct = f"{p_commune.global_score * 100:.0f}%"

                st.button(
                    f"⭐ {p_commune.name} — {score_pct}",
                    help=f"Ville Souhaitée : {p_commune.name}",
                    key="btn_top_pressentie",
                    type=btn_type,
                    width="stretch",
                    on_click=ui_results._result_highlight_callback,
                    args=(-1,),
                )
                if is_active:
                    with st.container(border=True):
                        ui_results._display_result_details(p_commune)
                    st.write("")

            # B. Top 5 Results (Vertical list)
            for i, c in enumerate(search_results.results[:5]):
                is_active = is_highlighted and highlighted_index == i
                btn_type = "primary" if is_active else "secondary"
                score_pct = f"{c.global_score * 100:.0f}%"

                st.button(
                    f"#{i+1} {c.name} — {score_pct}",
                    help=f"Top {i+1} : {c.name}",
                    key=f"btn_top_{i+1}",
                    type=btn_type,
                    width="stretch",
                    on_click=ui_results._result_highlight_callback,
                    args=(i,),
                )
                if is_active:
                    with st.container(border=True):
                        ui_results._display_result_details(c)
                    st.write("")

            if not is_highlighted:
                st.caption("💡 Cliquez sur une ville pour afficher l'analyse détaillée et le comparatif.")

    # 3. Main Full-Screen PyDeck Map (Background canvas)
    # Offset center slightly to the right to leave space for left overlay panel
    zoom_current = st.session_state.get("zoom", 6) or 6
    offset_lon = 1.1 * (2 ** max(0, 6 - zoom_current))

    deck = maps_deck.create_deck_map(
        gdf_scores=st.session_state.processed_gdf,
        center=st.session_state.get("center"),
        zoom=st.session_state.get("zoom"),
        search_results=search_results,
        config=config,
        pois_df=app_data.get("pois") if (app_data and not snapshot_mode) else None,
        selected_ids=selected_ids,
        highlighted_rank=highlighted_index if is_highlighted else None,
        show_top_5=show_top_5,
        current_map_context=current_map_context,
        center_offset_lon=offset_lon,
    )

    try:
        st.pydeck_chart(
            deck,
            width="stretch",
            height=1500,
            key="odis_main_pydeck_map",
        )
    except Exception as e:
        st.error(f"Erreur d'affichage de la carte: {e}")
        logger.error(f"❌ [MAP-ERROR] pydeck: {e}")
