import streamlit as st
import pandas as pd
import logging
import config as cfg
from core.models import SearchCriterias, CriteriaItem
from utils.data_loader import get_app_data
from ui.components import inject_custom_css

# Configure Logging
logger = logging.getLogger("ui.forms")


def render_localisation_form() -> None:
    """Renders the UI for the 'Localisation Actuelle' form section."""
    app_data = get_app_data()
    dept_details = app_data.get("dept_details", {})
    options_dep = app_data["coddep_set"]

    departement_actuel = st.selectbox(
        "Département",
        options_dep,
        key="ui_departement",
        format_func=lambda x: (
            f"{x} - {dept_details.get(x, {}).get('label', x)}" if dept_details else x
        ),
    )

    communes = app_data["depcom_df"][
        app_data["depcom_df"].dep_code == departement_actuel
    ]["libgeo"].tolist()
    if st.session_state.get("ui_commune") not in communes:
        st.session_state["ui_commune"] = communes[0]
    st.selectbox("Commune", communes, key="ui_commune")

    st.markdown("---")

    force_skip = st.session_state.get("ui_france_search", False)

    if force_skip:
        freq_options = ["Pas d'attache particulière"]
        if (
            "ui_freq_retour" not in st.session_state
            or st.session_state["ui_freq_retour"] != "Pas d'attache particulière"
        ):
            st.session_state["ui_freq_retour"] = "Pas d'attache particulière"
        freq_disabled = True
    else:
        freq_options = [
            "1 fois/semaine",
            "1 fois/mois",
            "1 fois/an",
            "Pas d'attache particulière",
        ]
        freq_disabled = False

    st.markdown("##### Ancrage au lieu de vie actuel")
    st.selectbox(
        "A quelle fréquence pense-t-il/elle revenir dans son lieu de vie actuel ?",
        options=freq_options,
        key="ui_freq_retour",
        disabled=freq_disabled,
        help="Détermine l'importance de la proximité et des connexions selon le lieu actuel.",
    )


def render_family_form() -> None:
    """Renders the UI for the 'Situation familiale' form section."""
    col1, col2 = st.columns(2)
    with col1:
        st.radio(
            "Nombre d'adultes",
            cfg.NOMBRE_ADULTES_OPTIONS,
            horizontal=True,
            key="ui_nb_adultes",
        )
    with col2:
        st.radio(
            "Nombre d'enfants",
            cfg.NOMBRE_ENFANTS_OPTIONS,
            horizontal=True,
            key="ui_nb_enfants",
        )


def render_education_form() -> None:
    """Renders the UI for the 'Education' form section."""
    nb_enfants = st.session_state.get("ui_nb_enfants", 0)
    if nb_enfants == 0:
        st.info("Aucun enfant n'a été ajouté dans l'onglet 'Situation familiale'.")
    else:
        col1, col2 = st.columns(2)
        for i in range(nb_enfants):
            col = col1 if i % 2 == 0 else col2
            with col:
                options = cfg.CLASSES_SCOLAIRES
                key = f"ui_classe_enfant_{i}"
                st.selectbox(f"Niveau enfant {i + 1}", options, key=key)


def render_employment_form() -> None:
    """Renders the UI for the 'Emploi & Formation' form section."""
    inject_custom_css()
    app_data = get_app_data()
    col1, col2 = st.columns(2)
    rome_full_index = app_data["rome_index"]
    rome_top_index = app_data.get(
        "rome_top_index", rome_full_index
    )  # Fallback to full if missing
    codform_select = app_data["codformations_index"]

    for i in range(st.session_state.get("ui_nb_adultes", 1)):
        with col1:
            current_selection = st.session_state.get(f"ui_metiers_adult_{i}", [])

            available_options = list(rome_top_index.index)
            for code in current_selection:
                if code not in available_options and code in rome_full_index.index:
                    available_options.append(code)

            def format_rome_label(code):
                if code in rome_full_index.index:
                    row = rome_full_index.loc[code]
                    label = row["label"]
                    count = row.get("total_postes", 0)
                    count_str = f"{int(count):,}".replace(",", " ")
                    return f"{label} [{count_str} postes]"
                return str(code)

            st.multiselect(
                f"Métiers ciblés Adulte {i + 1}",
                available_options,
                format_func=format_rome_label,
                key=f"ui_metiers_adult_{i}",
                help="Recherchez par nom de métier (Référentiel ROME). La liste affiche les métiers les plus demandés en nombre de postes.",
            )
        with col2:
            st.multiselect(
                f"Formations recherchées Adulte {i + 1}",
                codform_select.index,
                format_func=lambda x: codform_select.loc[x, "label"],
                key=f"ui_formations_adult_{i}",
            )


def render_housing_form() -> None:
    """Renders the UI for the 'Logement' form section."""
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Hébergement cible à court terme")
        current_heb = st.session_state.get("ui_hebergement_cible", [])

        # Callback to compile selected checkboxes into ui_hebergement_cible list
        def on_heb_change():
            selected = []
            for opt in cfg.HEBERGEMENT_OPTIONS:
                cb_key = f"ui_heb_cb_{opt.replace(' ', '_').lower()}"
                if st.session_state.get(cb_key):
                    selected.append(opt)
            st.session_state["ui_hebergement_cible"] = selected

        for opt in cfg.HEBERGEMENT_OPTIONS:
            cb_key = f"ui_heb_cb_{opt.replace(' ', '_').lower()}"
            if cb_key not in st.session_state:
                st.session_state[cb_key] = opt in current_heb

            st.checkbox(opt, key=cb_key, on_change=on_heb_change)

    with col2:
        st.subheader("Logement cible à long terme")
        st.radio(
            "Logement",
            cfg.LOGEMENT_OPTIONS,
            key="ui_logement",
            label_visibility="hidden",
        )

    heb_sel = st.session_state.get("ui_hebergement_cible", [])
    if (
        "Location avec Intermédiation" in heb_sel
        or st.session_state.get("ui_logement") == "Location"
    ):
        housing_type_options = list(cfg.HOUSING_TYPE_OPTIONS.keys())
        if (
            "ui_type_logement" not in st.session_state
            or st.session_state["ui_type_logement"] not in housing_type_options
        ):
            st.session_state["ui_type_logement"] = "appt_all"

        st.space("small")
        st.selectbox(
            "Si location quel type de logement ?",
            options=housing_type_options,
            format_func=lambda x: cfg.HOUSING_TYPE_OPTIONS[x],
            width=300,
            key="ui_type_logement",
            help="Permet d'utiliser les loyers spécifiques au type de logement choisi (Source 2024)",
        )


def render_health_form() -> None:
    """Renders the UI for the 'Santé' form section."""
    st.subheader("Support médical à proximité")
    current_sante = st.session_state.get("ui_besoin_sante", [])

    # Callback to compile selected checkboxes into ui_besoin_sante list
    def on_sante_change():
        selected = []
        for opt in cfg.SANTE_OPTIONS:
            safe_opt = (
                opt.replace(" ", "_")
                .replace("'", "_")
                .replace("(", "")
                .replace(")", "")
                .lower()
            )
            cb_key = f"ui_sante_cb_{safe_opt}"
            if st.session_state.get(cb_key):
                selected.append(opt)
        st.session_state["ui_besoin_sante"] = selected

    for opt in cfg.SANTE_OPTIONS:
        safe_opt = (
            opt.replace(" ", "_")
            .replace("'", "_")
            .replace("(", "")
            .replace(")", "")
            .lower()
        )
        cb_key = f"ui_sante_cb_{safe_opt}"
        if cb_key not in st.session_state:
            st.session_state[cb_key] = opt in current_sante

        st.checkbox(opt, key=cb_key, on_change=on_sante_change)


def render_other_needs_form() -> None:
    """Renders the UI for the 'Autres Besoins' (Inclusion) section."""
    inject_custom_css()
    app_data = get_app_data()

    col1, col2 = st.columns(2)
    with col2:
        st.subheader("Associations Locales")
        st.text(
            "Sélectionnez vos centres d'intérêt pour identifier les territoires avec un tissu associatif correspondant (Solidarité, Loisirs, Culture...)."
        )

        if "waldec_index" in app_data:
            waldec_index = app_data["waldec_index"]
            prefixes = cfg.WALDEC_CATEGORIES
            mask = waldec_index.index.str[:3].isin(prefixes)
            loisirs_df = waldec_index[mask].copy()

            options_items = []
            item_map = {}
            for code, row in loisirs_df.iterrows():
                item = CriteriaItem(code=str(code), label=row["label"])
                options_items.append(item)
                count_str = f"{int(row['count']):,}".replace(",", " ")
                item_map[item.code] = f"{item.label.title()} [{count_str} assos]"

            if "ui_inc_asso_add_selection" not in st.session_state:
                st.session_state.ui_inc_asso_add_selection = st.session_state.get(
                    "demo_data", {}
                ).get("inc_asso_add_selection", [])

            selected_codes = st.multiselect(
                "Centres d'intérêt",
                options=[item.code for item in options_items],
                format_func=lambda x: item_map.get(x, x),
                key="ui_inc_asso_add_selection_raw",
                label_visibility="collapsed",
            )

            st.session_state.ui_inc_asso_add_selection = [
                next(item for item in options_items if item.code == code)
                for code in selected_codes
            ]
        else:
            st.warning("Référentiel WALDEC non chargé.")

    with col1:
        st.subheader("Services d'Inclusion")
        st.text(
            "Sélectionnez les services d'accompagnement social requis pour la personne ou la famille."
        )

        # Ensure raw selection list is initialized in session state
        if "ui_inc_services_selection_raw" not in st.session_state:
            current_selection = st.session_state.get("ui_inc_services_selection", [])
            raw_list = []
            for x in current_selection:
                raw_list.append(x.code if hasattr(x, "code") else str(x))
            st.session_state["ui_inc_services_selection_raw"] = raw_list

        # Build ordered options (common pinned at top, others alphabetical)
        common_slugs = list(cfg.INC_SERVICES_CHECKBOX_MAPPING.keys())
        inclusion_index = app_data.get("inclusion_services_index", pd.DataFrame())

        all_slugs = []
        if not inclusion_index.empty:
            all_slugs = list(inclusion_index.index)

        other_slugs = [slug for slug in all_slugs if slug not in common_slugs]
        if not inclusion_index.empty:
            other_slugs.sort(
                key=lambda s: str(
                    inclusion_index.loc[s, "label"] if s in inclusion_index.index else s
                )
            )

        options = common_slugs + other_slugs

        # Label formatter mapping
        def format_service_label(slug):
            if slug in cfg.INC_SERVICES_CHECKBOX_MAPPING:
                return cfg.INC_SERVICES_CHECKBOX_MAPPING[slug]
            if not inclusion_index.empty and slug in inclusion_index.index:
                val = inclusion_index.loc[slug, "label"]
                return str(val.iloc[0] if isinstance(val, pd.Series) else val)
            return slug

        selected_raw = st.multiselect(
            "Services d'inclusion requis",
            options=options,
            format_func=format_service_label,
            key="ui_inc_services_selection_raw",
            help="Sélectionnez un ou plusieurs services d'inclusion. Les services recommandés/courants sont placés en tête de liste.",
            label_visibility="collapsed",
        )

        # Keep ui_inc_services_selection in sync
        inc_services_mapped = []
        for code in selected_raw:
            if not inclusion_index.empty and code in inclusion_index.index:
                val = inclusion_index.loc[code, "label"]
                label = str(val.iloc[0] if isinstance(val, pd.Series) else val)
            else:
                label = str(code)
            inc_services_mapped.append(CriteriaItem(code=str(code), label=label))

        st.session_state.ui_inc_services_selection = inc_services_mapped


def render_other_notes_form() -> None:
    """Renders the UI for entering free-text qualitative notes (F-48 update)."""
    if "ui_notes_qualitatives" not in st.session_state:
        st.session_state.ui_notes_qualitatives = st.session_state.get(
            "demo_data", {}
        ).get("notes_qualitatives", "")

    st.text(
        "Précisez ici tout élément supplémentaire potentiellement utile pour la recherche (origine culturelle, contexte familial, passions, contraintes spécifiques, etc.)."
    )

    st.text_area(
        "Notes qualitatives",
        key="ui_notes_qualitatives",
        height=250,
        placeholder="Exemple : Famille sud-américaine parlant espagnol, souhaite une zone rurale avec accès à la nature...",
        label_visibility="collapsed",
    )


def render_mobility_form() -> None:
    """Renders the UI for the 'Mobilité' form section (Consolidated)."""
    app_data = get_app_data()
    dept_details = app_data.get("dept_details", {})
    regions_dict = app_data.get("regions_names", {})

    current_dept_code = st.session_state.get("ui_departement")
    current_reg_code = dept_details.get(current_dept_code, {}).get("reg_code")

    region_codes = sorted(regions_dict.keys())

    # A malformed/legacy release should not crash the form while the pipeline
    # is being repaired.  The build gate now rejects this state, but keeping
    # the widget defensive prevents an IndexError for already-deployed data.
    default_region = (
        [current_reg_code]
        if current_reg_code in region_codes
        else ([region_codes[0]] if region_codes else [])
    )

    if "ui_france_search" not in st.session_state:
        st.session_state["ui_france_search"] = False
    if "ui_region_search" not in st.session_state:
        st.session_state["ui_region_search"] = False
    if "ui_mobility_region" not in st.session_state:
        st.session_state["ui_mobility_region"] = default_region
    elif isinstance(st.session_state["ui_mobility_region"], str):
        st.session_state["ui_mobility_region"] = [
            st.session_state["ui_mobility_region"]
        ]

    # Ensure all elements in the list are valid region codes
    valid_selected_regions = [
        r for r in st.session_state["ui_mobility_region"] if r in region_codes
    ]
    if not valid_selected_regions:
        valid_selected_regions = default_region
    st.session_state["ui_mobility_region"] = valid_selected_regions

    col_reg_1, col_reg_2 = st.columns([3, 1])
    with col_reg_1:
        selected_regions = st.multiselect(
            "Régions",
            region_codes,
            format_func=lambda x: regions_dict.get(x, x),
            key="ui_mobility_region",
            disabled=st.session_state.ui_france_search,
            placeholder="Sélectionnez une ou plusieurs régions",
        )
    with col_reg_2:
        st.space(20)
        st.checkbox(
            "France Métro.",
            key="ui_france_search",
            help="Rechercher sur l'ensemble du territoire.",
        )

    depts_in_region = [
        code
        for code, details in dept_details.items()
        if details.get("reg_code") in selected_regions
    ]
    depts_in_region.sort()

    if "ui_mobility_dept" not in st.session_state:
        st.session_state["ui_mobility_dept"] = (
            [current_dept_code] if current_dept_code in depts_in_region else []
        )
    elif isinstance(st.session_state["ui_mobility_dept"], str):
        old_val = st.session_state["ui_mobility_dept"]
        st.session_state["ui_mobility_dept"] = (
            [old_val] if old_val in depts_in_region else []
        )
    else:
        st.session_state["ui_mobility_dept"] = [
            d for d in st.session_state["ui_mobility_dept"] if d in depts_in_region
        ]

    col_dept_1, col_dept_2 = st.columns([3, 1])
    with col_dept_2:
        st.space(20)
        st.checkbox(
            "Toutes les régions",
            key="ui_region_search",
            disabled=st.session_state.ui_france_search,
            help="Rechercher dans tous les départements des régions sélectionnées.",
        )

    with col_dept_1:
        st.multiselect(
            "Départements",
            depts_in_region,
            format_func=lambda x: f"{x} - {dept_details.get(x, {}).get('label', x)}",
            key="ui_mobility_dept",
            disabled=st.session_state.ui_france_search
            or st.session_state.ui_region_search,
            placeholder="Sélectionnez un ou plusieurs départements",
        )

    st.divider()

    target_options = list(cfg.CITY_SIZE_MAPPING.keys())
    if "ui_target_city_size_label" not in st.session_state:
        default_label = next(
            (l for l in target_options if "Petite Ville" in l), target_options[2]
        )
        st.session_state["ui_target_city_size_label"] = default_label

    st.markdown("##### Taille de la ville recherchée")
    with st.container(horizontal=True, width="stretch", horizontal_alignment="center"):
        st.radio(
            "Taille de la ville recherchée",
            options=target_options,
            key="ui_target_city_size_label",
            horizontal=True,
            help="Définit la taille idéale de la commune recherchée. Le score de population sera maximal pour cette catégorie.",
            label_visibility="collapsed",
        )

    st.divider()

    st.markdown("##### Une idée de ville en tête ?")
    if "ui_has_commune_pressentie" not in st.session_state:
        st.session_state["ui_has_commune_pressentie"] = False

    has_pressentie = st.checkbox(
        "Je souhaite comparer les résultats avec une ville déjà identifiée",
        key="ui_has_commune_pressentie",
        help="Permet d'évaluer et de comparer une ville spécifique en dehors du Top 5.",
    )

    if has_pressentie:
        # Get top 1000 communes by population or fallback to depcom_df
        odis_df = app_data.get("odis", pd.DataFrame())
        communes_dict = {}
        if not odis_df.empty and "population" in odis_df.columns:
            top_1000 = (
                odis_df.dropna(subset=["population", "libgeo"])
                .sort_values(by="population", ascending=False)
                .head(1000)
            )
            for codgeo, row in top_1000.iterrows():
                dep = row.get("dep_code", "")
                communes_dict[str(codgeo)] = f"{row['libgeo']} ({dep})"
        else:
            depcom = app_data.get("depcom_df", pd.DataFrame())
            if not depcom.empty:
                for codgeo, row in depcom.iterrows():
                    dep = row.get("dep_code", "")
                    communes_dict[str(codgeo)] = f"{row['libgeo']} ({dep})"

        # Ensure active/restored commune code is present in options if set
        current_val = st.session_state.get("ui_commune_pressentie")
        curr_code = (
            current_val.code if hasattr(current_val, "code") else current_val
        ) if current_val else None

        if curr_code and curr_code not in communes_dict:
            if not odis_df.empty and curr_code in odis_df.index:
                row = odis_df.loc[curr_code]
                lib = row.get("libgeo", curr_code)
                dep = row.get("dep_code", "")
                communes_dict[str(curr_code)] = f"{lib} ({dep})" if dep else str(lib)
            else:
                communes_dict[str(curr_code)] = str(curr_code)

        options = sorted(communes_dict.keys(), key=lambda c: communes_dict[c])

        if not curr_code or curr_code not in options:
            st.session_state["ui_commune_pressentie"] = options[0] if options else None

        st.selectbox(
            "Ville souhaitée",
            options=options,
            format_func=lambda code: communes_dict.get(code, code),
            key="ui_commune_pressentie",
            help="Sélectionnez la ville avec laquelle vous souhaitez comparer les résultats.",
        )
    else:
        st.session_state["ui_commune_pressentie"] = None


def render_org_profile_form() -> None:
    """Renders the organization-specific preamble component (F-54)."""
    org = st.session_state.get("org")
    if not org:
        st.info("Aucun profil d'organisation actif.")
        return

    st.subheader(
        f"Vous trouverez ci dessous les paramètres spécifiques pour {org.name} :"
    )

    # st.divider()

    # --- Strategic Locations Multi-select ---
    app_data = get_app_data(load_heavy=False)
    zone_type = org.zone_type

    if zone_type == "departement":
        label = "Départements"
        options = app_data["coddep_set"]
        dept_details = app_data.get("dept_details", {})

        def format_func(x):
            return f"{x} - {dept_details.get(x, {}).get('label', x)}"
    else:
        # Bassin de vie
        label = "Bassins de vie"
        bv_names = app_data.get("bv_names", {})
        options = sorted(list(bv_names.keys()))

        def format_func(x):
            return f"{x} - {bv_names.get(x, x)}"

    # Pre-fill with current session state or profile defaults
    current_selection = st.session_state.get(
        "ui_org_strategic_locations", org.default_zones
    )

    # Ensure all current selections are in options (safety)
    valid_selection = [x for x in current_selection if x in options]
    st.session_state["ui_org_strategic_locations"] = valid_selection

    st.write(f"**Zones d'intérêt stratégique ({label})**")

    st.multiselect(
        f"**Zones d'intérêt stratégique ({label})**",
        options=options,
        format_func=format_func,
        label_visibility="collapsed",
        key="ui_org_strategic_locations",
        help="Les communes situées dans ces zones recevront un bonus dans le score final.",
    )

    # Special filter checkbox for J'Accueille (only if org == "jaccueille")
    if org.id == "jaccueille":
        if "ui_org_strategic_locations_filter" not in st.session_state:
            st.session_state["ui_org_strategic_locations_filter"] = False

        st.checkbox(
            "Restreindre la recherche uniquement aux zones opérationnelles J'Accueille",
            key="ui_org_strategic_locations_filter",
            help="Si activé, la recherche ne renverra que des communes situées dans des bassins de vie disposant de coordinateurs locaux et d'au moins un accueillant ou prospect.",
        )

    # --- Criteria Boosts Sliders (F-54 Expansion) ---
    org_defaults = org.defaults
    if "org_boosts" in org_defaults:
        st.divider()
        st.write("**Boosts de critères spécifiques**")
        st.text(
            "Ajustez l'importance de certains critères clés pour votre organisation (multiplicateur x1 à x5)."
        )

        boost_config = org_defaults["org_boosts"]
        new_boosts = {}

        for criterion_id, default_val in boost_config.items():
            # Get label from config
            label = criterion_id
            score_row = app_data["scores_cat"][
                app_data["scores_cat"]["score"] == criterion_id
            ]
            if not score_row.empty:
                label = score_row.iloc[0]["label"]

            # Key for session state
            ui_key = f"ui_org_boost_{criterion_id}"
            slider_key = f"ui_org_boost_slider_{criterion_id}"

            # Ensure session state is initialized if not present
            if ui_key not in st.session_state:
                st.session_state[ui_key] = float(default_val)
            if slider_key not in st.session_state:
                st.session_state[slider_key] = int(st.session_state[ui_key])

            with st.container(horizontal=True):
                # col1, col2 = st.columns([1, 2])
                # with col1:
                st.space(size="medium")
                st.markdown(f"##### {label}")
                # with col2:
                val = st.slider(
                    f"Boost pour : {label}",
                    min_value=1,
                    max_value=5,
                    # DO NOT pass a default value parameter when key is already in session_state,
                    # to prevent Streamlit widget warning loops.
                    key=slider_key,
                    step=1,
                    format="x%d",
                    width=100,
                    label_visibility="collapsed",
                    on_change=lambda k=ui_key, sk=slider_key: st.session_state.update(
                        {k: float(st.session_state[sk])}
                    ),
                )
            new_boosts[criterion_id] = float(val)

        st.session_state["ui_org_boosts"] = new_boosts

    # st.markdown("---")
    # st.caption("Vous pouvez modifier ces paramètres manuellement dans les autres onglets si nécessaire.")


def render_weight_profile_form() -> None:
    """Renders the UI for selecting the weighting profile and expert weights adjustment."""

    def _update_weights_from_profile():
        profile = st.session_state.ui_weight_profile
        if profile == "Profil personnalisé":
            st.session_state.ui_expert_weights = True
        elif profile in cfg.WEIGHT_PROFILES:
            st.session_state.ui_expert_weights = False
            weights = cfg.WEIGHT_PROFILES[profile]
            for key, value in weights.items():
                st.session_state[f"ui_{key}"] = value

        st.session_state["processed_gdf"] = None
        st.session_state["search_results"] = None

    weight_profiles = list(cfg.WEIGHT_PROFILES.keys()) + ["Profil personnalisé"]
    if (
        "ui_weight_profile" not in st.session_state
        or st.session_state["ui_weight_profile"] not in weight_profiles
    ):
        st.session_state["ui_weight_profile"] = weight_profiles[0]

    if "ui_expert_weights" not in st.session_state:
        st.session_state.ui_expert_weights = (
            st.session_state["ui_weight_profile"] == "Profil personnalisé"
        )

    st.text(
        "Pour améliorer la pertinence des résultats de la recherche, vous pouvez ajuster les poids des différentes catégories de critères de recherche en utilisant soit un profil de pondération (recommandé) soit une pondération sur-mesure."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.selectbox(
            "Profil de pondération",
            options=weight_profiles,
            key="ui_weight_profile",
            on_change=_update_weights_from_profile,
        )

    def _invalidate_results():
        st.session_state["processed_gdf"] = None
        st.session_state["search_results"] = None

    weight_keys = [
        "ui_poids_education",
        "ui_poids_emploi",
        "ui_poids_logement",
        "ui_poids_inclusion",
        "ui_poids_sante",
        "ui_poids_mobilite",
    ]

    # Add Territory weight if org context is present
    org = st.session_state.get("org")
    if org:
        weight_keys.append("ui_poids_territoire")

    for p_key in weight_keys:
        if p_key not in st.session_state:
            st.session_state[p_key] = 1.0 if p_key == "ui_poids_territoire" else 0.5

    format_pct = lambda x: f"{int(x * 100)}%"

    sliders_disabled = not st.session_state.get("ui_expert_weights", False)

    labels_map = {
        "ui_poids_education": "Education",
        "ui_poids_emploi": "Projet Pro",
        "ui_poids_logement": "Logement",
        "ui_poids_inclusion": "Inclusion",
        "ui_poids_sante": "Santé",
        "ui_poids_mobilite": "Mobilité",
        "ui_poids_territoire": "Territoire",
    }

    # Render sliders inline alternately in col2 and col3 for a flat 3-column [1, 1, 1] layout
    displayed_weight_keys = [k for k in weight_keys if k != "ui_poids_territoire"]
    for idx, p_key in enumerate(displayed_weight_keys):
        target_col = col2 if idx % 2 == 0 else col3
        with target_col:
            # Sub-columns to make the label and the slider render inline
            lbl_col, sld_col = st.columns([1, 2.2], vertical_alignment="center")
            with lbl_col:
                st.markdown(f"**{labels_map.get(p_key, p_key)}**")
            with sld_col:
                st.select_slider(
                    labels_map.get(p_key, p_key),
                    options=cfg.POIDS_OPTIONS,
                    format_func=format_pct,
                    disabled=sliders_disabled,
                    key=p_key,
                    label_visibility="collapsed",
                    on_change=_invalidate_results,
                )


def display_input_tabs() -> None:
    """Displays the main tabs for user input, composed of modular rendering functions."""
    inject_custom_css()

    tab_names = [
        "Localisation",
        "Situation familiale",
        "Education",
        "Projet Professionnel",
        "Logement",
        "Santé",
        "Inclusion",
        "Autres",
        "Profil",
    ]

    org = st.session_state.get("org")
    if org:
        tab_names.insert(0, org.name)

    tabs = st.tabs(tab_names)

    current_tab_idx = 0
    if org:
        with tabs[current_tab_idx]:
            render_org_profile_form()
        current_tab_idx += 1

    with tabs[current_tab_idx]:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.markdown("##### Localisation actuelle")
            render_localisation_form()
        with col2:
            st.markdown("##### Zone de recherche")
            render_mobility_form()
    current_tab_idx += 1

    with tabs[current_tab_idx]:
        render_family_form()
    current_tab_idx += 1

    with tabs[current_tab_idx]:
        render_education_form()
    current_tab_idx += 1

    with tabs[current_tab_idx]:
        render_employment_form()
    current_tab_idx += 1

    with tabs[current_tab_idx]:
        render_housing_form()
    current_tab_idx += 1

    with tabs[current_tab_idx]:
        render_health_form()
    current_tab_idx += 1

    with tabs[current_tab_idx]:
        render_other_needs_form()
    current_tab_idx += 1

    with tabs[current_tab_idx]:
        render_other_notes_form()
    current_tab_idx += 1

    with tabs[current_tab_idx]:
        render_weight_profile_form()


def create_search_criterias_from_inputs() -> SearchCriterias:
    """Gathers all user inputs from session_state and creates a SearchCriterias object."""
    app_data = get_app_data()

    # Location
    dept_code = st.session_state.get(
        "ui_departement", cfg.DEMO_DATA_DEFAULT["departement_actuel"]
    )
    commune_lib = st.session_state.get(
        "ui_commune", cfg.DEMO_DATA_DEFAULT["commune_actuelle"]
    )

    commune_codgeo = app_data["depcom_df"][
        (app_data["depcom_df"].dep_code == dept_code)
        & (app_data["depcom_df"].libgeo == commune_lib)
    ].index[0]

    commune_actuelle = CriteriaItem(code=str(commune_codgeo), label=str(commune_lib))

    # Shortlisted city
    commune_pressentie = None
    if st.session_state.get("ui_has_commune_pressentie") and st.session_state.get(
        "ui_commune_pressentie"
    ):
        p_code = st.session_state.get("ui_commune_pressentie")
        commune_names = app_data.get("commune_names", {})
        odis = app_data.get("odis", pd.DataFrame())
        p_lib = (
            odis.loc[p_code, "libgeo"]
            if not odis.empty and p_code in odis.index
            else commune_names.get(p_code, p_code)
        )
        commune_pressentie = CriteriaItem(code=str(p_code), label=str(p_lib))

    # New Mobility Logic (F-53)
    if st.session_state.get("ui_france_search"):
        loc_search_area = "france"
        loc_search_code = []
    elif st.session_state.get("ui_region_search"):
        loc_search_area = "region"
        selected_regs = st.session_state.get("ui_mobility_region", [])
        loc_search_code = (
            selected_regs if isinstance(selected_regs, list) else [selected_regs]
        )
    else:
        loc_search_area = "departement"
        selected_depts = st.session_state.get("ui_mobility_dept", [])
        loc_search_code = (
            selected_depts if isinstance(selected_depts, list) else [selected_depts]
        )

    # Education
    nb_enfants = st.session_state.get("ui_nb_enfants", 0)
    classe_enfants = []
    for i in range(nb_enfants):
        val = st.session_state.get(f"ui_classe_enfant_{i}")
        if val is not None:
            classe_enfants.append(str(val))
        else:
            # Fallback to the first class option if not rendered yet
            classe_enfants.append(
                cfg.CLASSES_SCOLAIRES[0] if cfg.CLASSES_SCOLAIRES else ""
            )

    # Employment (Enrich with CriteriaItem)
    nb_adultes = st.session_state.get("ui_nb_adultes", 1)
    rome_index = app_data.get("rome_index", pd.DataFrame())
    codes_metiers = []
    for i in range(nb_adultes):
        raw_codes = st.session_state.get(f"ui_metiers_adult_{i}", [])
        if not isinstance(raw_codes, list):
            raw_codes = [raw_codes] if raw_codes else []
        enriched_list = []
        for code in raw_codes:
            label = (
                rome_index.loc[code, "label"]
                if not rome_index.empty and code in rome_index.index
                else str(code)
            )
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_metiers.append(enriched_list)

    form_index = app_data.get("codformations_index", pd.DataFrame())
    codes_formations = []
    for i in range(nb_adultes):
        raw_codes = st.session_state.get(f"ui_formations_adult_{i}", [])
        if not isinstance(raw_codes, list):
            raw_codes = [raw_codes] if raw_codes else []
        enriched_list = []
        for code in raw_codes:
            label = (
                form_index.loc[code, "label"]
                if not form_index.empty and code in form_index.index
                else str(code)
            )
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_formations.append(enriched_list)

    # F-48 Logic: Consolidate Inclusion Services (Checkbox + Multiselect)
    inc_index = app_data.get("inclusion_services_index", pd.DataFrame())

    # Read the raw multiselect state if present, fallback to ui_inc_services_selection
    raw_services = st.session_state.get("ui_inc_services_selection_raw")
    if raw_services is not None:
        all_inc_services = set(raw_services)
    else:
        # Fallback for API / tests / init
        inc_services = st.session_state.get("ui_inc_services_selection", [])
        all_inc_services = set()
        for x in inc_services:
            all_inc_services.add(x.code if hasattr(x, "code") else str(x))

    inc_services_mapped = []
    for code in sorted(list(all_inc_services)):
        # Recover label
        if not inc_index.empty and code in inc_index.index:
            val = inc_index.loc[code, "label"]
            label = str(val.iloc[0] if isinstance(val, pd.Series) else val)
        else:
            label = str(code)
        inc_services_mapped.append(CriteriaItem(code=str(code), label=label))

    # F-15: Compute Criteria Weights (Default empty dict, customized weights via org_boosts or API)
    criteria_weights = {}

    # Enrich Inclusion Associations (WALDEC Logic F-48)
    waldec_index = app_data.get("waldec_index", pd.DataFrame())
    inc_assos_mapped = []
    for item in st.session_state.get("ui_inc_asso_add_selection", []):
        if isinstance(item, CriteriaItem):
            inc_assos_mapped.append(item)
        elif isinstance(item, str):
            val = (
                waldec_index.loc[item, "label"]
                if not waldec_index.empty and item in waldec_index.index
                else item
            )
            label = str(val.iloc[0] if isinstance(val, pd.Series) else val)
            inc_assos_mapped.append(CriteriaItem(code=str(item), label=label))

    # Type Logement Enrich
    type_log = None
    ui_type_log = st.session_state.get("ui_type_logement", "appt_all")
    if ui_type_log in cfg.HOUSING_TYPE_OPTIONS:
        type_log = CriteriaItem(
            code=ui_type_log, label=cfg.HOUSING_TYPE_OPTIONS[ui_type_log]
        )

    # Weights & Profile
    profile = st.session_state.get("ui_weight_profile", "Équilibré")

    # Population mapping based on selected city size label
    selected_city_label = st.session_state.get("ui_target_city_size_label")
    mapping = cfg.CITY_SIZE_MAPPING.get(
        selected_city_label, {"mu": cfg.DEFAULT_MU, "sigma": cfg.DEFAULT_SIGMA}
    )
    target_pop = mapping["mu"]
    target_sigma = mapping["sigma"]
    # Hébergement & Fréquence
    heb_sel = st.session_state.get("ui_hebergement_cible", [])
    freq = st.session_state.get("ui_freq_retour", "Pas d'attache particulière")

    return SearchCriterias(
        weight_profile=profile,
        poids_emploi=st.session_state.get("ui_poids_emploi", 0.5),
        poids_logement=st.session_state.get("ui_poids_logement", 0.5),
        poids_education=st.session_state.get("ui_poids_education", 0.5),
        poids_inclusion=st.session_state.get("ui_poids_inclusion", 0.5),
        poids_sante=st.session_state.get("ui_poids_sante", 0.5),
        poids_mobilite=st.session_state.get("ui_poids_mobilite", 0.5),
        criteria_weights=criteria_weights,
        target_population=target_pop,
        target_population_sigma=target_sigma,
        commune_actuelle=commune_actuelle,
        commune_pressentie=commune_pressentie,
        loc_search_area=loc_search_area,
        loc_search_code=loc_search_code,
        nb_adultes=nb_adultes,
        nb_enfants=nb_enfants,
        hebergement_cible=heb_sel,
        logement=st.session_state.get("ui_logement", "Location"),
        type_logement=type_log,
        freq_retour=freq,
        codes_metiers=codes_metiers,
        codes_formations=codes_formations,
        classe_enfants=classe_enfants,
        besoin_sante=st.session_state.get("ui_besoin_sante", []),
        # Consolidated inclusion services field
        inc_services_selection=inc_services_mapped,
        inc_asso_add_selection=inc_assos_mapped,
        notes_qualitatives=[st.session_state.get("ui_notes_qualitatives", "")]
        if st.session_state.get("ui_notes_qualitatives")
        else [],
        # Org Specifics
        org_context=st.session_state.get("org").id
        if st.session_state.get("org")
        else None,
        org_strategic_locations=st.session_state.get("ui_org_strategic_locations", []),
        org_strategic_locations_type=st.session_state.get(
            "ui_org_strategic_locations_type", "departement"
        ),
        org_strategic_locations_filter=st.session_state.get(
            "ui_org_strategic_locations_filter", False
        ),
        org_boosts=st.session_state.get("ui_org_boosts", {}),
        poids_territoire=st.session_state.get("ui_poids_territoire", 1.0),
    )
