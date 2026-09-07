import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from unittest.mock import patch
from pathlib import Path
from core.models import User, SearchCriterias


@pytest.mark.e2e
@patch("ui.page_shell.inject_idle_disconnect")
@patch("core.postscoring.launch_post_scoring_tasks")
@patch("utils.data_loader.fetch_salesforce_jaccueille_bdv")
@patch("services.rna_rag.RNARagService")
def test_happy_path_end_to_end(
    mock_rna_rag,
    mock_fetch_salesforce,
    mock_launch_post_scoring_tasks,
    mock_inject_idle_disconnect,
):
    """E2E test using Streamlit AppTest framework.

    Steps:
    1. Simulates step-by-step form inputs via native widget interactions across all 9 wizard steps.
    2. Includes shortlisted city ('commune pressentie' Libourne 33243) and Data Inclusion service (FLE).
    3. Verifies 'Précédent' back-navigation and state retention.
    4. Submits via primary button 'Voir les résultats' and transitions to pages/3_Resultats.py.
    5. Exhaustively validates criteria capture, active criteria activation, results dataframe invariants,
       and granular category score breakdowns for Top 5 and the shortlisted city.
    """
    # 1. Setup mocks
    mock_fetch_salesforce.return_value = pd.DataFrame(
        columns=["bassin_de_vie", "contact_count", "lead_count"]
    )

    def fake_launch(engine, config, search_results, h):
        from agents.utils import get_odis_bg_store

        store = get_odis_bg_store()
        all_communes = list(search_results.results)
        if search_results.commune_pressentie:
            all_communes.append(search_results.commune_pressentie)

        store[h] = {
            "status_refiner": "done",
            "pitches": {c.codgeo: f"Mock pitch for {c.name}" for c in all_communes},
            "enrichment": {c.codgeo: {} for c in all_communes},
            "inclusion_services_enrichment": {
                c.codgeo: {
                    "Maitriser le français": [
                        {
                            "id": "mock_srv_fle_1",
                            "name": "Atelier FLE Municipal",
                            "description": "Cours de français langue étrangère",
                            "lien_source": "https://example.com/fle",
                            "source": "data-inclusion",
                        }
                    ]
                }
                for c in all_communes
            },
            "jobs": {c.codgeo: [] for c in all_communes},
            "odis_brief": "Mock situation brief",
        }

    mock_launch_post_scoring_tasks.side_effect = fake_launch

    # 2. Initialize AppTest at main.py (using absolute path from repo root)
    root_path = Path(__file__).resolve().parent.parent.parent
    at = AppTest.from_file(str(root_path / "app" / "main.py"), default_timeout=60)

    # Bypass authentication and pre-populate defaults
    at.session_state["password_correct"] = True
    at.session_state["auth_method"] = "local"
    at.session_state["username"] = "test"
    at.session_state["user"] = User(username="test")
    at.session_state["org"] = None
    at.session_state["demo_data"] = {}

    # Run the main.py redirect -> 1_Accueil.py
    at.run(timeout=60)
    assert len(at.exception) == 0

    # Switch page manually to Formulaire
    at.switch_page("pages/2_Formulaire.py").run(timeout=60)
    assert len(at.exception) == 0

    def click_next(app_tester):
        btn = next(b for b in app_tester.button if b.label == "Suivant")
        btn.click().run(timeout=60)
        assert len(app_tester.exception) == 0
        assert len(app_tester.error) == 0

    # --- PAGE 1: Localisation ---
    assert at.session_state.form_page == "localisation"

    at.selectbox(key="ui_commune").select("33063").run()
    at.selectbox(key="ui_freq_retour").select("1 fois/mois").run()
    at.multiselect(key="ui_mobility_dept").select("33").run()
    at.radio(key="ui_target_city_size_label").set_value("🏘️ Petite Ville").run()

    # Shortlisted city (Ville pressentie): Libourne (33243)
    at.checkbox(key="ui_has_commune_pressentie").check().run()
    at.selectbox(key="ui_commune_pressentie").select("33243").run()
    click_next(at)

    # --- PAGE 2: Situation familiale ---
    assert at.session_state.form_page == "family"
    at.radio(key="ui_nb_adultes").set_value(2).run()
    at.radio(key="ui_nb_enfants").set_value(1).run()
    click_next(at)

    # --- PAGE 3: Education ---
    assert at.session_state.form_page == "education"
    at.selectbox(key="ui_classe_enfant_0").select("Maternelle").run()
    click_next(at)

    # --- PAGE 4: Projet professionnel ---
    assert at.session_state.form_page == "professional_project"
    ms_rome = at.multiselect(key="ui_metiers_adult_0")
    assert len(ms_rome.options) > 0
    ms_rome.select(ms_rome.options[0]).run()

    ms_form = at.multiselect(key="ui_formations_adult_0")
    assert len(ms_form.options) > 0
    ms_form.select(ms_form.options[0]).run()
    click_next(at)

    # --- PAGE 5: Logement ---
    assert at.session_state.form_page == "housing"
    at.checkbox(key="ui_heb_cb_location_avec_intermédiation").check().run()
    at.checkbox(key="ui_logement_cb_location").check().run()
    at.selectbox(key="ui_type_logement").select("appt_t1_t2").run()
    click_next(at)

    # --- PAGE 6: Santé ---
    assert at.session_state.form_page == "health"
    at.checkbox(key="ui_sante_cb_hôpital").check().run()
    click_next(at)

    # --- PAGE 7: Inclusion ---
    assert at.session_state.form_page == "other_needs"
    ms_asso = at.multiselect(key="ui_inc_asso_add_selection_raw")
    assert len(ms_asso.options) > 0
    ms_asso.select(ms_asso.options[0]).run()

    # Native selection of official Data Inclusion FLE service
    ms_serv = at.multiselect(key="ui_inc_services_selection_raw")
    assert "Apprendre le français (FLE)" in ms_serv.options
    ms_serv.select("Apprendre le français (FLE)").run()
    click_next(at)

    # --- PAGE 8: Notes ---
    assert at.session_state.form_page == "notes"
    at.text_area(key="ui_notes_qualitatives").input("Famille motivée.").run()
    click_next(at)

    # --- PAGE 9: Profil ---
    assert at.session_state.form_page == "profile"
    at.selectbox(key="ui_weight_profile").select("Équilibré").run()

    # Verify 'Précédent' preserves state and navigates back
    prev_btn = next(b for b in at.button if b.label == "Précédent")
    prev_btn.click().run(timeout=60)
    assert at.session_state.form_page == "notes"
    assert at.text_area(key="ui_notes_qualitatives").value == "Famille motivée."
    click_next(at)
    assert at.session_state.form_page == "profile"

    # Submit the form using the wizard's final primary button
    submit_btn = next(b for b in at.button if b.label == "Voir les résultats")
    submit_btn.click().run(timeout=60)
    assert len(at.exception) == 0
    assert len(at.error) == 0

    # Ensure results page switched successfully
    assert "config" in at.session_state, "Search config was not generated"
    config: SearchCriterias = at.session_state["config"]

    # =========================================================================
    # EXHAUSTIVE VERIFICATION 1: Domain Model (SearchCriterias) Integrity
    # =========================================================================
    assert config.commune_actuelle.code == "33063"
    assert config.commune_actuelle.label == "Bordeaux"

    # Validate shortlisted city (Libourne) was captured
    assert config.commune_pressentie is not None
    assert config.commune_pressentie.code == "33243"
    assert config.commune_pressentie.label == "Libourne"

    assert config.nb_adultes == 2
    assert config.nb_enfants == 1
    assert config.classe_enfants == ["Maternelle"]
    assert "Location" in config.logement
    assert "Location avec Intermédiation" in config.hebergement_cible
    assert config.type_logement.code == "appt_t1_t2"
    assert config.besoin_sante == ["Hôpital"]

    # Validate inclusion services and associations
    inc_service_codes = [s.code for s in config.inc_services_selection]
    assert "lecture-ecriture-calcul--maitriser-le-francais" in inc_service_codes
    assert len(config.inc_asso_add_selection) > 0

    # Validate employment criteria
    assert len(config.codes_metiers[0]) > 0
    assert len(config.codes_formations[0]) > 0

    # =========================================================================
    # EXHAUSTIVE VERIFICATION 2: Active Criteria Activation
    # =========================================================================
    active_criteria = config.active_criteria
    assert active_criteria is not None, "Active criteria was not set"

    expected_mandatory_criteria = [
        "edu_maternelle_scaled",
        "log_loyer_moyen_appt_t1_t2_scaled",
        "log_vac_scaled",
        "heb_loc_iml_scaled",
        "sante_hopital_scaled",
        "inc_services_incl_scaled",
        "inc_asso_add_scaled",
        "inc_siae_density_scaled",
        "met_match_adult1_scaled",
        "form_match_adult1_scaled",
    ]
    for crit in expected_mandatory_criteria:
        assert crit in active_criteria, f"Criterion '{crit}' was expected to be active"

    # =========================================================================
    # EXHAUSTIVE VERIFICATION 3: Processed GeoDataFrame (Math & Columns)
    # =========================================================================
    assert "processed_gdf" in at.session_state, "Results dataframe was not generated"
    results_gdf = at.session_state["processed_gdf"]
    assert results_gdf is not None and not results_gdf.empty, "Results dataframe is empty"
    assert "weighted_score" in results_gdf.columns

    # All active criteria must exist as columns in processed_gdf and be bounded [0, 1]
    for crit in active_criteria:
        assert crit in results_gdf.columns, f"Active criterion column '{crit}' missing from processed_gdf"
        col = results_gdf[crit].dropna()
        if not col.empty:
            assert (col >= 0.0).all() and (col <= 1.0).all(), f"Criterion '{crit}' has out-of-bounds scores"

    # Data Inclusion FLE service must have positive calculated scores on real territorial data
    assert (results_gdf["inc_services_incl_scaled"] > 0.0).any(), (
        "inc_services_incl_scaled had no positive scores; Data Inclusion matching failed"
    )

    # Monotonic descending ranking
    scores = results_gdf["weighted_score"]
    assert scores.is_monotonic_decreasing, "Results are not sorted by score descending"
    assert (scores >= 0.0).all() and (scores <= 1.0).all()

    # =========================================================================
    # EXHAUSTIVE VERIFICATION 4: SearchResultsData Breakdown & Libourne Check
    # =========================================================================
    assert "search_results" in at.session_state, "SearchResultsData not found in session state"
    sr = at.session_state["search_results"]

    # Current location check
    assert sr.current_geo.codgeo == "33063"
    assert 0.0 <= sr.current_geo.global_score <= 1.0

    # Shortlisted city (Libourne) check
    assert sr.commune_pressentie is not None, "Commune pressentie was not evaluated"
    assert sr.commune_pressentie.codgeo == "33243"
    assert sr.commune_pressentie.name == "Libourne"
    assert 0.0 <= sr.commune_pressentie.global_score <= 1.0
    assert 0.0 <= sr.commune_pressentie.score_besoins <= 1.0

    # Top 5 ranked communes + Libourne category breakdown check
    assert len(sr.results) >= 5
    evaluated_communes = sr.results[:5] + [sr.commune_pressentie]

    expected_categories = {
        "education",
        "emploi",
        "inclusion",
        "logement",
        "mobilite",
        "sante",
        "territoire",
    }

    for commune in evaluated_communes:
        assert 0.0 <= commune.global_score <= 1.0
        # All thematic categories must be present
        commune_categories = set(commune.scores.keys())
        assert expected_categories.issubset(commune_categories), (
            f"Commune {commune.name} missing categories: {expected_categories - commune_categories}"
        )

        # Inclusion details must contain inc_services_incl_scaled
        incl_details = {d.score_id: d for d in commune.scores.get("inclusion", [])}
        assert "inc_services_incl_scaled" in incl_details, (
            f"inc_services_incl_scaled missing from inclusion scores for {commune.name}"
        )
        assert 0.0 <= incl_details["inc_services_incl_scaled"].score_normalise <= 1.0
        assert incl_details["inc_services_incl_scaled"].relative_weight > 0

        # Education details must contain edu_maternelle_scaled
        edu_details = {d.score_id: d for d in commune.scores.get("education", [])}
        assert "edu_maternelle_scaled" in edu_details
        assert 0.0 <= edu_details["edu_maternelle_scaled"].score_normalise <= 1.0

        # Sante details must contain sante_hopital_scaled
        sante_details = {d.score_id: d for d in commune.scores.get("sante", [])}
        assert "sante_hopital_scaled" in sante_details
        assert 0.0 <= sante_details["sante_hopital_scaled"].score_normalise <= 1.0
