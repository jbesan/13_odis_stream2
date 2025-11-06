import streamlit as st
import config as cfg



# DO NOT REMOVE: This makes sure the session_states persist as expected
for k, v in st.session_state.items():
    st.session_state[k] = v
app_data = st.session_state.app_data

# Sidebar
with st.sidebar:
    st.image('./images/logo-jaccueille-singa.png', width=150)

    st.text("")
    st.text("Remplissez ce formulaire afin de préciser le projet de vie de la ou des personnes accompagnées.")

    st.divider()

    if st.button("Passer aux résultats", type='tertiary'):
        st.switch_page("pages/3_Resultats.py") 

    if st.button("Revenir à la page d'accueil", type='tertiary'):
        st.switch_page("./1_Accueil.py") 


PAGES = {
    "localisation": "Localisation",
    "family": "Situation familiale",
    "education": "Éducation",
    "professional_project": "Projet professionnel",
    "housing": "Logement",
    "health": "Santé",
    "other_needs": "Autres besoins",
    "mobility": "Mobilité"
}
PAGES_LIST = list(PAGES.keys())

def get_person_accompanied_str():
    if st.session_state.get('ui_nom'):
        return f"de {st.session_state.ui_nom}"
    return "de la personne accompagnée"

def display_localisation_actuelle_page():
    st.subheader(f"Localisation actuelle {get_person_accompanied_str()}")
    col1, col2 = st.columns(2)
    with col1:
        # Using explicit index to set default value, as confirmed by user test
        departements = app_data['coddep_set']
        st.selectbox("Département", departements, index=departements.index(st.session_state.ui_departement) if st.session_state.ui_departement in departements else 0, key="ui_departement")

    with col2:  
        communes = app_data['depcom_df'][app_data['depcom_df'].dep_code == st.session_state.ui_departement]['libgeo']
        communes_list = communes.tolist()
        
        if st.session_state.get('ui_commune') not in communes_list:
            st.session_state.ui_commune = communes_list[0] if communes_list else None
        st.selectbox("Commune", communes_list, index=communes_list.index(st.session_state.ui_commune) if st.session_state.ui_commune in communes_list else 0, key="ui_commune")

def display_family_situation_page():
    st.subheader(f"Composition du foyer {get_person_accompanied_str()}")
    col1, col2 = st.columns(2)
    with col1:
        st.number_input("Nombre d'adultes", min_value=1, max_value=2, key="ui_nb_adultes", step=1, value=st.session_state.get('ui_nb_adultes', 1))
    with col2:
        st.number_input("Nombre d'enfants", min_value=0, max_value=5, key="ui_nb_enfants", step=1, value=st.session_state.get('ui_nb_enfants', 0))

def display_education_page():
    st.subheader(f"Niveau d'étude des enfants {get_person_accompanied_str()}")
    nb_enfants = st.session_state.get('ui_nb_enfants', 0)
    if nb_enfants > 0:
        for i in range(nb_enfants):
            key = f'ui_classe_enfant_{i}'
            options = cfg.CLASSES_SCOLAIRES
            index = options.index(st.session_state.get(key)) if st.session_state.get(key) in options else 0
            st.selectbox(f'Classe de l’enfant {i+1}', options, index=index, key=key)
    else:
        st.info("Aucun enfant n'a été déclaré dans la situation familiale.")

def display_professional_project_page():
    st.subheader(f"Métiers et formations {get_person_accompanied_str()}")

    codfap_select = app_data['codfap_index'][['Code FAP 341', 'Intitulé FAP 341']].set_index('Code FAP 341')
    codform_select = app_data['codformations_index']

    for i in range(st.session_state.get('ui_nb_adultes', 1)):
        st.subheader(f"Adulte {i+1}")
        metiers_key = f'ui_metiers_adult_{i}'

        st.multiselect(
            f"Métiers ciblés par Adulte {i+1}", 
            options=codfap_select.index, 
            format_func=lambda x: codfap_select.loc[x].item(), 
            key=metiers_key
            )

        formations_key = f'ui_formations_adult_{i}'
        st.multiselect(
            f"Formations recherchées Adulte {i+1}", 
            options=codform_select.index, 
            format_func=lambda x: codform_select.loc[x].item(), 
            key=formations_key
            )

def display_housing_page():
    st.subheader(f"Logement et hébergement {get_person_accompanied_str()}")
    col1, col2 = st.columns(2)
    with col1:
        options = cfg.HEBERGEMENT_OPTIONS
        index = options.index(st.session_state.get('ui_hebergement')) if st.session_state.get('ui_hebergement') in options else 0
        st.selectbox("Solution d'hébergement temporaire", options, index=index, key="ui_hebergement")
    with col2:
        options = cfg.LOGEMENT_OPTIONS
        index = options.index(st.session_state.get('ui_logement')) if st.session_state.get('ui_logement') in options else 0
        st.selectbox("Type de logement recherché", options, index=index, key='ui_logement')

def display_health_page():
    st.subheader(f"Besoin en santé {get_person_accompanied_str()}")
    options = cfg.SANTE_OPTIONS
    index = options.index(st.session_state.get('ui_besoin_sante')) if st.session_state.get('ui_besoin_sante') in options else 0
    st.selectbox("Prise en charge spécifique", options, index=index, key='ui_besoin_sante')

def display_other_needs_page():
    st.subheader(f"Inclusion et vie sociale {get_person_accompanied_str()}")
    st.text("Sélectionnez d'autres besoins:")
    col1, col2 = st.columns(2)
    with col1:
        annuaire_inclusion = app_data['annuaire_inclusion']
        cat = st.selectbox('Catégorie', sorted(set(annuaire_inclusion.categorie)), format_func=lambda x: x.replace('-', ' ').capitalize(), index=2)
        service = st.selectbox('Service', sorted(set(annuaire_inclusion[annuaire_inclusion.categorie == cat].service)), format_func=lambda x: x.replace('-', ' ').capitalize(), index=0)
        if st.button('Ajouter'):
            st.session_state.ui_besoins_autres.setdefault(cat, []).append(service)
            st.session_state.ui_besoins_autres[cat] = sorted(list(set(st.session_state.ui_besoins_autres[cat])))
            st.rerun()
    with col2:
        st.text('Besoins ajoutés:')
        if not st.session_state.get('ui_besoins_autres', {}):
            st.info('Aucun')
        else:
            for key, values in st.session_state.ui_besoins_autres.items():
                st.markdown(f"**{key.replace('-', ' ').capitalize()}**")
                for value in values:
                    st.markdown(f"&nbsp;&nbsp;&nbsp;- {value.replace('-', ' ').capitalize()}")
        if st.button('Vider', width='stretch'):
            st.session_state.ui_besoins_autres = {}
            st.rerun()

def display_mobility_page():
    st.subheader(f"Mobilité {get_person_accompanied_str()}")
    options = cfg.MOBILITE_OPTIONS
    st.radio(
        'Attachement au lieu de vie actuel :', 
        options=options.keys(), 
        format_func=options.get, 
        key="ui_loc_distance_km"
        )

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
    "mobility": display_mobility_page,
}
page_function_map[st.session_state.form_page]()

st.divider()

col1, col2 = st.columns([1,1])

with col1:
    with st.container(horizontal_alignment="left"):
        if current_page_index > 0:
            if st.button("Précédent"):
                st.session_state.form_page = PAGES_LIST[current_page_index - 1]
                print(st.session_state.ui_nb_enfants)
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