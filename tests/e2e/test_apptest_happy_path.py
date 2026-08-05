import pandas as pd
from streamlit.testing.v1 import AppTest
from unittest.mock import patch
from core.models import SearchCriterias


# Mocking custom components that require JS/HTML environments or external API hits
@patch("ui.page_shell.inject_idle_disconnect")
@patch("streamlit_folium.st_folium")
@patch("core.postscoring.launch_post_scoring_tasks")
@patch("utils.data_loader.fetch_salesforce_jaccueille_bdv")
@patch("services.rna_rag.RNARagService")
def test_happy_path_end_to_end(
    mock_rna_rag,
    mock_fetch_salesforce,
    mock_launch_post_scoring_tasks,
    mock_st_folium,
    mock_inject_idle_disconnect,
):
    """
    E2E test using Streamlit AppTest framework.
    Steps:
    1. Simulates step-by-step form inputs on pages/2_Formulaire.py.
    2. Switches/submits to pages/3_Resultats.py.
    3. Runs the scoring engine and asserts that search criteria, active criteria,
       and results dataframe are all correctly constructed and match invariants.
    """
    # 1. Setup mocks
    mock_fetch_salesforce.return_value = pd.DataFrame(
        columns=["bassin_de_vie", "contact_count", "lead_count"]
    )

    def fake_launch(engine, config, search_results, h):
        from agents.utils import get_odis_bg_store

        store = get_odis_bg_store()
        store[h] = {
            "status_refiner": "done",
            "pitches": {
                c.codgeo: f"Mock pitch for {c.name}" for c in search_results.results
            },
            "enrichment": {c.codgeo: {} for c in search_results.results},
            "inclusion_services_enrichment": {
                c.codgeo: {
                    "Maitriser le français": [
                        {
                            "id": "mock_srv_1",
                            "name": "Mock French Service",
                            "description": "Mock description",
                            "lien_source": "https://example.com",
                            "source": "soliguide",
                        }
                    ]
                }
                for c in search_results.results
            },
            "jobs": {c.codgeo: [] for c in search_results.results},
            "odis_brief": "Mock situation brief",
        }

    mock_launch_post_scoring_tasks.side_effect = fake_launch

    # 2. Initialize AppTest at main.py (so page paths resolve correctly relative to root)
    at = AppTest.from_file("app/main.py", default_timeout=30)

    # Bypass authentication and pre-populate defaults
    at.session_state["password_correct"] = True
    at.session_state["username"] = "test"
    at.session_state["demo_data"] = {}

    # Run the main.py redirect -> 1_Accueil.py
    at.run(timeout=30)
    assert len(at.exception) == 0

    # Switch page manually to Formulaire
    at.switch_page("pages/2_Formulaire.py").run(timeout=10)
    assert len(at.exception) == 0

    # --- PAGE 1: Localisation ---
    at.session_state.form_page = "localisation"
    at.run(timeout=10)
    assert len(at.exception) == 0

    # Fill in localisation widgets
    at.selectbox(key="ui_departement").select("33").run()
    at.selectbox(key="ui_commune").select("Bordeaux").run()
    at.selectbox(key="ui_freq_retour").select("1 fois/mois").run()
    at.checkbox(key="ui_france_search").uncheck().run()
    at.checkbox(key="ui_region_search").uncheck().run()
    at.multiselect(key="ui_mobility_region").select("75").run()  # region code
    at.multiselect(key="ui_mobility_dept").select("33").run()
    at.radio(key="ui_target_city_size_label").set_value("🏘️ Petite Ville").run()

    # --- PAGE 2: Situation familiale ---
    at.session_state.form_page = "family"
    at.run(timeout=10)
    assert len(at.exception) == 0
    at.radio(key="ui_nb_adultes").set_value(2).run()
    at.radio(key="ui_nb_enfants").set_value(1).run()

    # --- PAGE 3: Education ---
    at.session_state.form_page = "education"
    at.run(timeout=10)
    assert len(at.exception) == 0
    at.selectbox(key="ui_classe_enfant_0").select("Maternelle").run()

    # --- PAGE 4: Projet professionnel ---
    at.session_state.form_page = "professional_project"
    # Seed multiselect session state keys directly to bypass AppTest's format_func select bug
    at.session_state["ui_metiers_adult_0"] = ["M1805"]
    at.session_state["ui_formations_adult_0"] = ["114"]
    at.run(timeout=10)
    assert len(at.exception) == 0

    # --- PAGE 5: Logement ---
    at.session_state.form_page = "housing"
    at.run(timeout=10)
    assert len(at.exception) == 0
    at.checkbox(key="ui_heb_cb_location_avec_intermédiation").check().run()
    at.radio(key="ui_logement").set_value("Location").run()
    at.selectbox(key="ui_type_logement").select("appt_t1_t2").run()

    # --- PAGE 6: Santé ---
    at.session_state.form_page = "health"
    at.run(timeout=10)
    assert len(at.exception) == 0
    at.checkbox(key="ui_sante_cb_hôpital").check().run()

    # --- PAGE 7: Inclusion ---
    at.session_state.form_page = "other_needs"
    # Seed multiselect session state keys directly to bypass AppTest's format_func select bug
    at.session_state["ui_inc_asso_add_selection_raw"] = ["W332020211"]
    at.session_state["ui_inc_services_selection_raw"] = ["siae"]
    at.run(timeout=10)
    assert len(at.exception) == 0

    # --- PAGE 8: Notes ---
    at.session_state.form_page = "notes"
    at.run(timeout=10)
    assert len(at.exception) == 0
    at.text_area(key="ui_notes_qualitatives").input("Famille motivée.").run()

    # --- PAGE 9: Profil ---
    at.session_state.form_page = "profile"
    at.run(timeout=10)
    assert len(at.exception) == 0
    at.selectbox(key="ui_weight_profile").select("Équilibré").run()

    # The sidebar action submits the form and launches the deterministic search.
    btn = next(b for b in at.button if b.label == "Passer aux résultats")
    btn.click().run(timeout=20)
    assert len(at.exception) == 0

    # Ensure results page switched page successfully
    # Check that search results and config were created in the session state
    assert "config" in at.session_state, "Search config was not generated"
    config: SearchCriterias = at.session_state["config"]

    # 3. Assert search criteria properties match inputs
    assert config.commune_actuelle.label == "Bordeaux"
    assert config.nb_adultes == 2
    assert config.nb_enfants == 1
    assert config.classe_enfants == ["Maternelle"]
    assert config.logement == "Location"
    assert config.besoin_sante == ["Hôpital"]
    assert "Location avec Intermédiation" in config.hebergement_cible

    # 4. Assert active criteria contains expected keys
    active_criteria = config.active_criteria
    assert active_criteria is not None, "Active criteria was not set"

    # Education must be active because nb_enfants > 0 and 'Maternelle' level selected
    assert "edu_maternelle_scaled" in active_criteria

    # Rent and housing vacancy must be active because "Location" selected
    assert "log_vac_scaled" in active_criteria
    assert "log_loyer_moyen_appt_t1_t2_scaled" in active_criteria

    # Santé (hopital) must be active because besoin_sante == "Hôpital"
    assert "sante_hopital_scaled" in active_criteria

    # 5. Assert results match invariants
    assert "processed_gdf" in at.session_state, "Results dataframe was not generated"
    results_gdf = at.session_state["processed_gdf"]
    assert results_gdf is not None, "Results dataframe was not generated"
    assert not results_gdf.empty, "Results dataframe is empty"
    assert "weighted_score" in results_gdf.columns

    # Scores must be descending
    scores = results_gdf["weighted_score"]
    assert scores.is_monotonic_decreasing, "Results are not sorted by score descending"

    # Scores must be bounded [0, 1]
    assert (scores >= 0.0).all() and (scores <= 1.0).all()
