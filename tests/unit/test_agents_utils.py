import pandas as pd
from unittest.mock import patch, MagicMock
from app.agents.utils import (
    sanitize_llm_markdown,
    map_ui_config_to_search_criterias,
    run_async_safe,
    run_autodetect_safe,
)


def test_sanitize_llm_markdown():
    # Test double escaped newlines and windows carriage returns
    assert sanitize_llm_markdown("Hello\\nWorld") == "Hello\nWorld"
    assert sanitize_llm_markdown("Hello\\r\\nWorld") == "Hello\nWorld"
    # Test escaped quotes
    assert sanitize_llm_markdown('Hello \\"World\\"') == 'Hello "World"'
    # Test empty string
    assert sanitize_llm_markdown("") == ""
    assert sanitize_llm_markdown(None) == ""


def test_map_ui_config_to_search_criterias():
    # Prepare mock app_data with index
    app_data = {
        "odis": pd.DataFrame({"libgeo": ["Paris"]}, index=["75056"]),
        "rome_index": pd.DataFrame({"label": ["Boulanger"]}, index=["D1102"]),
        "codformations_index": pd.DataFrame({"label": ["FLE"]}, index=["F123"]),
        "inclusion_services_index": pd.DataFrame(
            {"label": ["FLE Service"]}, index=["S123"]
        ),
        "waldec_index": pd.DataFrame({"label": ["Football"]}, index=["W123"]),
    }

    # Create a custom config class to represent the raw UI values
    class MockConfig:
        commune_actuelle = "75056"
        loc_search_area = "departement"
        loc_search_code = ["75"]
        nb_adultes = 1
        nb_enfants = 0
        classe_enfants = []
        codes_metiers = [["D1102"]]
        codes_formations = [["F123"]]
        inc_services_selection = ["S123"]
        inc_asso_add_selection = ["Football"]
        type_logement = "appt_all"
        logement = "Location"
        besoin_sante = "Aucun"
        weight_profile = "Équilibré"
        criteria_weights = {}
        org_context = None
        org_strategic_locations = []
        org_strategic_locations_type = "departement"
        poids_territoire = 1.0
        hebergement_cible = []

    config = MockConfig()
    mapped = map_ui_config_to_search_criterias(config, app_data)

    # Assertions
    assert mapped.commune_actuelle.code == "75056"
    assert mapped.commune_actuelle.label == "Paris"
    assert mapped.codes_metiers[0][0].code == "D1102"
    assert mapped.codes_metiers[0][0].label == "Boulanger"
    assert mapped.codes_formations[0][0].code == "F123"
    assert mapped.codes_formations[0][0].label == "FLE"
    assert mapped.inc_services_selection[0].code == "S123"
    assert mapped.inc_services_selection[0].label == "FLE Service"
    assert mapped.inc_asso_add_selection[0].code == "W123"
    assert mapped.inc_asso_add_selection[0].label == "Football"


@patch("app.agents.utils.run_logic")
@patch("streamlit.session_state")
def test_run_async_safe(mock_ss, mock_run_logic):
    mock_ss.get.side_effect = lambda k, d=None: "user1" if k == "username" else d
    mock_run_logic.return_value = {"search_results": "ok"}

    input_data = {}
    res = run_async_safe(input_data)

    assert res == {"search_results": "ok"}
    assert input_data["username"] == "user1"


@patch("agents.interviewer.interviewer_agent.run")
@patch("app.agents.agent_config.get_gemini_client")
def test_run_autodetect_safe(mock_get_client, mock_agent_run):
    mock_agent_run.return_value = MagicMock(output="detected_intent")
    res = run_autodetect_safe("help me find a job")
    assert res == "detected_intent"
