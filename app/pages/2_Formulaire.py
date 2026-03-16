import streamlit as st
import config as cfg

# --- Authentication ---
from utils import auth
if not auth.check_password():
    st.stop()

from ui import components as ui
from utils import data_loader

# Ensure app data and session state are initialized
data_loader.ensure_data_initialized()

# DO NOT REMOVE: This makes sure the ui_ form state persists as expected
for k, v in st.session_state.items():
    if str(k).startswith('ui_'):
        st.session_state[k] = v
app_data = st.session_state.app_data

import logging
from utils import common as utils

# Sidebar
with st.sidebar:
    logo_path = utils.get_asset_path('logo-jaccueille-singa.png')
    logo_b64 = utils.get_base64_image(logo_path)
    if logo_b64:
        st.markdown(f'<img src="data:image/png;base64,{logo_b64}" width="150" style="margin-bottom: 20px;">', unsafe_allow_html=True)
    else:
        st.error("Logo not found")

    st.text("")
    st.text("Remplissez ce formulaire afin de préciser le projet de vie de la ou des personnes accompagnées.")

    st.divider()



    ui.start_over()
    from ui import feedback
    feedback.render_feedback_button()

    st.divider()
    
    if st.button("Passer aux résultats", type='secondary', width="stretch"):
        st.switch_page("pages/3_Resultats.py") 

PAGES = {
    "localisation": "Localisation",
    "family": "Situation familiale",
    "education": "Éducation",
    "professional_project": "Projet professionnel",
    "housing": "Logement",
    "health": "Santé",
    "other_needs": "Inclusion",
    "profile": "Profil"
}
PAGES_LIST = list(PAGES.keys())

def get_person_accompanied_str():
    if st.session_state.get('ui_nom'):
        return f"de {st.session_state.ui_nom}"
    return "de la personne accompagnée"

# These functions now act as simple wrappers, calling the centralized UI components.
def display_localisation_actuelle_page():
    st.subheader(f"Localisation {get_person_accompanied_str()}")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Localisation actuelle**")
        ui.render_localisation_form()
    with col2:
        st.markdown("**Localisation souhaitée**")
        ui.render_mobility_form()

def display_family_situation_page():
    st.subheader(f"Composition du foyer {get_person_accompanied_str()}")
    ui.render_family_form()

def display_education_page():
    st.subheader(f"Niveau d'étude des enfants {get_person_accompanied_str()}")
    ui.render_education_form()

def display_professional_project_page():
    st.subheader(f"Métiers et formations {get_person_accompanied_str()}")
    ui.render_employment_form()

def display_housing_page():
    st.subheader(f"Logement et hébergement {get_person_accompanied_str()}")
    ui.render_housing_form()

def display_health_page():
    st.subheader(f"Besoin en santé {get_person_accompanied_str()}")
    ui.render_health_form()

def display_other_needs_page():
    st.subheader(f"Inclusion et vie sociale {get_person_accompanied_str()}")
    ui.render_other_needs_form()

def display_profile_page():
    st.subheader(f"Profil de pondération pour la recherche")
    ui.render_weight_profile_form()

if 'form_page' not in st.session_state:
    st.session_state.form_page = 'localisation'

current_page_index = PAGES_LIST.index(st.session_state.form_page)
st.progress((current_page_index) / (len(PAGES_LIST)-1), text=f"Étape {current_page_index + 1}/{len(PAGES_LIST)}: {PAGES[st.session_state.form_page]}")

page_function_map = {
    "localisation": display_localisation_actuelle_page,
    "family": display_family_situation_page,
    "education": display_education_page,
    "professional_project": display_professional_project_page,
    "housing": display_housing_page,
    "health": display_health_page,
    "other_needs": display_other_needs_page,
    "profile": display_profile_page,
}
page_function_map[st.session_state.form_page]()

st.divider()

col1, col2 = st.columns([1,1])

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
                st.session_state.form_page = PAGES_LIST[current_page_index + 1]
                st.rerun()
        else:
            if st.button("Voir les résultats", type="primary"):
                st.session_state['form_completed'] = True
                st.switch_page("pages/3_Resultats.py")