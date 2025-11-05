import time
import copy
import streamlit as st
import config as cfg
import data_loader

print(f"--- App re-run at {time.ctime(time.time())} ---")

st.set_page_config(layout="wide", page_title='OD&IS: Recherche Inversée')

def session_states_init(defaults):
    """Initializes all necessary keys in Streamlit's session state."""
    if 'app_data' not in st.session_state:
        st.session_state['app_data'] = {}
    if 'config' not in st.session_state:
        st.session_state['config'] = None
    if "processed_gdf" not in st.session_state:
        st.session_state['processed_gdf'] = None
    if "selected_geo" not in st.session_state:
        st.session_state['selected_geo'] = None
    if "highlighted_result" not in st.session_state:
        st.session_state['highlighted_result'] = [False, None]
    if 'fg_dict_ref' not in st.session_state:
        st.session_state['fg_dict_ref'] = {}
    if 'fgs_to_show' not in st.session_state:
        st.session_state['fgs_to_show'] = set()
    if "zoom" not in st.session_state:
        st.session_state['zoom'] = 10
    if "center" not in st.session_state:
        st.session_state['center'] = cfg.DEFAULT_MAP_CENTER
    if 'demo_data' not in st.session_state:
        st.session_state['demo_data'] = defaults
    # if 'form_page' not in st.session_state:
    st.session_state['form_page'] = 'localisation'

    ui_keys_map = {
        'ui_departement': 'departement_actuel',
        'ui_commune': 'commune_actuelle',
        'ui_poids_education': 'poids_education',
        'ui_poids_emploi': 'poids_emploi',
        'ui_poids_logement': 'poids_logement',
        'ui_poids_inclusion': 'poids_inclusion',
        'ui_poids_mobilité': 'poids_mobilité',
        'ui_penalite_binome': ('binome_penalty', lambda x: int(x * 100)),
        'ui_pop_min': 'pop_min',
        'ui_nb_adultes': 'nb_adultes',
        'ui_nb_enfants': 'nb_enfants',
        'ui_loc_distance_km': 'loc_distance_km',
        'ui_hebergement': 'hebergement',
        'ui_logement': 'logement',
        'ui_besoin_sante': 'sante',
        'ui_besoins_autres': 'besoins_autres'
    }

    for ui_key, config_key in ui_keys_map.items():
        if ui_key not in st.session_state:
            if isinstance(config_key, tuple):
                base_key, transform = config_key
                st.session_state[ui_key] = transform(defaults[base_key])
            else:
                st.session_state[ui_key] = defaults[config_key]

    for i in range(2):
        if f'ui_metiers_adult_{i}' not in st.session_state:
            st.session_state[f'ui_metiers_adult_{i}'] = []
        if f'ui_formations_adult_{i}' not in st.session_state:
            st.session_state[f'ui_formations_adult_{i}'] = []
    for i in range(5):
        if f'ui_classe_enfant_{i}' not in st.session_state:
            st.session_state[f'ui_classe_enfant_{i}'] = 'Maternelle'



# Load Demo data
def load_demo_data(demo_data):
    # print("session_states_init")
    # """Loads demo data if a 'demo' query parameter is present in the URL."""
    if len(st.query_params) > 0:
        demo_id = st.query_params.get('demo')
        if demo_id in cfg.DEMO_SCENARIOS:
            print(f"--- Loading Demo Mode {demo_id} ---")
            demo_data.update(cfg.DEMO_SCENARIOS[demo_id])

        if st.sidebar.button('Quitter Mode Démo', key='quit_demo'):
            st.query_params.clear()
            st.rerun()
        st.markdown('<style> .st-emotion-cache-16txtl3 {position:relative; top:80vh}</style>', unsafe_allow_html=True)

    return demo_data

# --- Main App Execution ---
defaults = cfg.DEMO_DATA_DEFAULT
session_states_init(defaults)

# Load all datasets and cache them
st.session_state.app_data = data_loader.init_datasets()

# Handle demo data from URL query params
st.session_state['demo_data'] = load_demo_data(copy.deepcopy(cfg.DEMO_DATA_DEFAULT))

# Let's center all the text on this page
st.markdown(
    """
    <style>
    .stApp {
        text-align: center;
    }
    </style>
    """,
    unsafe_allow_html=True
)
# st.image('./images/logo-jaccueille-singa.png', width=250)

st.markdown("""
            <style>
                .st-key-header_accueil {background-color: #1B4429; color: white; padding:30px; border-radius:30px}
                .st-key-lancement_formulaire .stButton p {color: #1B4429; font-size:1.3rem;}
                .st-key-lancement_formulaire .stButton div {margin: 30px;}
            </style>
            """
        , unsafe_allow_html=True)


with st.container(width="stretch", key="header_accueil", horizontal_alignment="center"):
    st.image('./images/logo-jaccueille-singa.png', width=200)

    st.title("Bienvenue sur OD&IS")
    st.markdown("L'outil d'aide à la mobilité pour l'intégration des personnes réfugiées.")
    st.markdown("\n")

    with st.container(horizontal_alignment="center"):

        if st.button("Commencer le parcours de recherche", type="secondary", key="lancement_formulaire"):
            st.switch_page("pages/2_Formulaire.py")

        if st.button("Aller directement à la page résultats", type="tertiary"):
            st.switch_page("pages/3_Resultats.py")

st.markdown("\n")
st.image('./images/explications_acceuil.png', width="stretch")

st.markdown("\n")
st.markdown("L'outil respecte la conformité RGPD. Les données collectées sont utilisées uniquement pour fournir le résultat affiché à l'utilisateur")
