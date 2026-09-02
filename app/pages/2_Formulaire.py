import streamlit as st

st.set_page_config(page_title="OD&IS", page_icon="👋", layout="wide")


from ui import page_shell

page_shell.enter_page("Formulaire")

from ui import forms as ui_forms
from ui.form_state import FormState
from utils import data_loader

# The form owns a single complete data bundle. Home has already started a
# best-effort warm-up; on a cold instance this is the one honest wait point
# rather than letting individual form controls discover missing Tier-2 fields.
if "app_data" not in st.session_state or not st.session_state["app_data"]:
    with st.spinner("Initialisation des données territoriales..."):
        app_data = data_loader.ensure_data_initialized()
else:
    app_data = data_loader.ensure_data_initialized()

FormState(st.session_state).preserve_widgets_across_steps()
import logging

# Sidebar
with st.sidebar:
    page_shell.render_sidebar_logo()

    st.text(
        "Remplissez ce formulaire afin de préciser le projet de vie de la ou des personnes accompagnées."
    )

    st.divider()

    page_shell.render_primary_sidebar_actions(show_home=True, show_feedback=True)

    if st.button(
        "Passer aux résultats",
        type="primary",
        width="stretch",
        icon=":material/fast_forward:",
    ):
        errors = FormState(st.session_state).get_location_validation_errors()
        if errors:
            st.session_state["location_validation_warning"] = errors
            st.session_state.form_page = "localisation"
            st.rerun()
        else:
            st.session_state.pop("location_validation_warning", None)
            st.session_state["form_completed"] = True
            st.switch_page("pages/3_Resultats.py")

    page_shell.render_account_sidebar_actions()

org = st.session_state.get("org")

PAGES = {}
# Note: Org settings step is hidden from the form wizard flow by default, but preserved.
# if org:
#     PAGES["org"] = org.name

PAGES.update(
    {
        "localisation": "Localisation",
        "family": "Situation familiale",
        "education": "Éducation",
        "professional_project": "Projet professionnel",
        "housing": "Logement",
        "health": "Santé",
        "other_needs": "Inclusion",
        "notes": "Autres",
        "profile": "Profil",
    }
)
PAGES_LIST = list(PAGES.keys())


# These functions now act as simple wrappers, calling the centralized UI components.
def display_localisation_actuelle_page():
    errors = FormState(st.session_state).get_location_validation_errors()
    if not errors and "location_validation_warning" in st.session_state:
        del st.session_state["location_validation_warning"]
    elif st.session_state.get("location_validation_warning"):
        ui_forms.render_location_validation_warning(errors)

    st.subheader("Localisation de la personne accompagnée")
    col1, col2, col3 = st.columns([3, 1, 5])
    with col1:
        st.markdown("**Localisation actuelle**")
        ui_forms.render_localisation_form(app_data)
    with col2:
        st.space("large")
        st.header(":material/arrow_forward_ios:", text_alignment="center")
    with col3:
        st.markdown("**Zone de recherche**")
        ui_forms.render_mobility_form(app_data)


def display_family_situation_page():
    st.subheader("Composition du foyer de la personne accompagnée")
    ui_forms.render_family_form()


def display_education_page():
    st.subheader("Niveau d'étude des enfants de la personne accompagnée")
    ui_forms.render_education_form()


def display_professional_project_page():
    st.subheader("Métiers et formations de la personne accompagnée")
    ui_forms.render_employment_form(app_data)


def display_housing_page():
    st.subheader("Logement et hébergement de la personne accompagnée")
    ui_forms.render_housing_form()


def display_health_page():
    st.subheader("Besoin(s) en santé de la personne accompagnée")
    ui_forms.render_health_form()


def display_other_needs_page():
    st.subheader("Inclusion et vie sociale de la personne accompagnée")
    ui_forms.render_other_needs_form(app_data)


def display_other_notes_page():
    st.subheader("Autres informations de la personne accompagnée")
    ui_forms.render_other_notes_form()


def display_profile_page():
    st.subheader("Profil des priorités pour la recherche")
    ui_forms.render_weight_profile_form()


def display_org_page():
    # Title is handled by render_org_profile_form but we can add context here if needed
    ui_forms.render_org_profile_form(app_data)


if "form_page" not in st.session_state:
    st.session_state.form_page = PAGES_LIST[0]

current_page_index = PAGES_LIST.index(st.session_state.form_page)
st.progress(
    (current_page_index) / (len(PAGES_LIST) - 1),
    text=f"Étape {current_page_index + 1}/{len(PAGES_LIST)}: {PAGES[st.session_state.form_page]}",
)

page_function_map = {
    "org": display_org_page,
    "localisation": display_localisation_actuelle_page,
    "family": display_family_situation_page,
    "education": display_education_page,
    "professional_project": display_professional_project_page,
    "housing": display_housing_page,
    "health": display_health_page,
    "other_needs": display_other_needs_page,
    "notes": display_other_notes_page,
    "profile": display_profile_page,
}
page_function_map[st.session_state.form_page]()

st.divider()

col1, col2 = st.columns([1, 1])

with col1:
    with st.container(horizontal_alignment="left"):
        if current_page_index > 0:
            if st.button("Précédent"):
                st.session_state.form_page = PAGES_LIST[current_page_index - 1]
                logging.info(st.session_state.ui_nb_enfants)
                st.rerun()

with col2:
    with st.container(horizontal_alignment="right"):
        if current_page_index < len(PAGES_LIST) - 1:
            if st.button("Suivant"):
                if st.session_state.form_page == "localisation":
                    errors = FormState(st.session_state).get_location_validation_errors()
                    if errors:
                        st.session_state["location_validation_warning"] = errors
                        st.rerun()
                st.session_state.pop("location_validation_warning", None)
                st.session_state.form_page = PAGES_LIST[current_page_index + 1]
                st.rerun()
        else:
            if st.button("Voir les résultats", type="primary"):
                errors = FormState(st.session_state).get_location_validation_errors()
                if errors:
                    st.session_state["location_validation_warning"] = errors
                    st.session_state.form_page = "localisation"
                    st.rerun()
                else:
                    st.session_state.pop("location_validation_warning", None)
                    st.session_state["form_completed"] = True
                    st.switch_page("pages/3_Resultats.py")
