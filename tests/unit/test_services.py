import pytest
from unittest.mock import MagicMock, patch
from services import telemetry, bq_logger
from ui import feedback


@pytest.fixture
def mock_session_state():
    """Mock Streamlit session state with support for attribute and item access."""
    state = MagicMock()
    # Mock __contains__ so 'in st.session_state' works
    state.__contains__.side_effect = lambda k: k in state.__dict__
    return state


def test_interaction_id_persistence(mock_session_state):
    """Test that interaction_id is generated and remains stable."""
    with patch("streamlit.session_state", mock_session_state):
        # First call should generate it
        first_id = telemetry.get_interaction_id()
        assert len(first_id) == 8

        # Second call should return the same ID
        second_id = telemetry.get_interaction_id()
        assert second_id == first_id

        # Reset should change it
        reset_id = telemetry.reset_interaction_id()
        assert reset_id != first_id
        assert len(reset_id) == 8


def test_interaction_id_logic():
    """Test that interaction_id is generated and remains stable."""
    with patch("streamlit.session_state", MagicMock()) as mock_ss:
        # Mocking the attribute behavior
        mock_ss.interaction_id = "test-id"
        mock_ss.__contains__.side_effect = lambda k: k == "interaction_id"

        val = telemetry.get_interaction_id()
        assert val == "test-id"


@patch("services.bq_logger.bigquery.Client")
def test_log_agent_state_to_bq(mock_client_class):
    """Test that agent state is correctly formatted for BQ."""
    mock_client = mock_client_class.return_value
    mock_client.project = "test-project"
    mock_client.insert_rows_json.return_value = []

    with patch("streamlit.session_state", MagicMock()) as mock_ss:
        mock_ss.get.side_effect = lambda k, d=None: (
            "test_user" if k == "username" else d
        )
        mock_ss.interaction_id = "test-id"
        mock_ss.__contains__.side_effect = lambda k: k == "interaction_id"

        with patch("os.getenv", return_value="test-project"):
            agent_state = {
                "messages": [{"role": "user", "content": "hello"}],
                "usage": {"cost_usd": 0.01},
            }
            bq_logger.log_agent_state_to_bq("hello", agent_state)

            assert mock_client.insert_rows_json.called
            args, _ = mock_client.insert_rows_json.call_args
            row = args[1][0]  # client.insert_rows_json(table_ref, [row])
            assert row["username"] == "test_user"
            assert row["last_user_message"] == "hello"


@patch("ui.feedback.bigquery.Client")
def test_feedback_submission(mock_client_class):
    """Test that feedback is sent to the correct BQ table."""
    mock_client = mock_client_class.return_value
    mock_client.project = "test-project"
    mock_client.insert_rows_json.return_value = []

    with patch("streamlit.session_state", MagicMock()) as mock_ss:
        mock_ss.get.side_effect = lambda k, d=None: (
            "test_user" if k == "username" else d
        )
        mock_ss.interaction_id = "test-id"
        mock_ss.__contains__.side_effect = lambda k: k == "interaction_id"

        with patch("os.getenv", return_value="test-project"):
            success = feedback._submit_to_bq("Bug", "It's broken")
            assert success is True
            assert mock_client.insert_rows_json.called
            args, _ = mock_client.insert_rows_json.call_args
            row = args[1][0]
            assert row["feedback_type"] == "Bug"
            assert row["comment"] == "It's broken"


@patch("services.telemetry.bigquery.Client")
def test_log_search_complete(mock_client_class):
    """Test that log_search_complete formats and logs data correctly to BQ."""
    import json

    mock_client = mock_client_class.return_value
    mock_client.project = "test-project"
    mock_client.insert_rows_json.return_value = []

    from core.models import SearchCriterias, SearchResultsData, CommuneResult

    config = SearchCriterias(
        commune_actuelle="33063",
        loc_search_area="departement",
        loc_search_code=["33"],
        nb_adultes=1,
        nb_enfants=0,
    )

    commune = CommuneResult(
        codgeo="33063", name="Bordeaux", population=250000, global_score=0.9
    )
    commune.scores = {"logement": []}

    search_results = SearchResultsData(
        search_hash="hash123", results=[commune], current_geo=commune
    )

    with patch("streamlit.session_state", MagicMock()) as mock_ss:
        mock_ss.get.side_effect = lambda k, d=None: (
            "test_user" if k == "username" else d
        )
        mock_ss.interaction_id = "test-id"
        mock_ss.__contains__.side_effect = lambda k: k == "interaction_id"

        with patch("os.getenv", return_value="test-project"):
            telemetry.log_search_complete(
                config=config,
                search_results=search_results,
                source_flow="classic",
                interaction_id="test-id",
                username="test_user",
            )

            assert mock_client.insert_rows_json.called
            args, _ = mock_client.insert_rows_json.call_args
            row = args[1][0]
            assert row["interaction_id"] == "test-id"
            assert row["username"] == "test_user"
            assert row["source_flow"] == "classic"
            assert "search_hash" in row
            assert "org_id" in row

            criteria_loaded = json.loads(row["search_criteria"])
            assert criteria_loaded["commune_actuelle"]["code"] == "33063"


def test_is_admin_check(monkeypatch):
    """Test admin check helper."""
    import config as cfg
    from utils import auth

    monkeypatch.setattr(cfg, "ADMIN_USERS", {"admin@example.com", "jacques-local"})
    assert auth.is_admin("admin@example.com") is True
    assert auth.is_admin("jacques-local") is True
    assert auth.is_admin("random_user") is False
    assert auth.is_admin(None) is False


@patch("services.telemetry.bigquery.Client")
def test_log_usage_event(mock_client_class):
    """Test log_usage_event inserts structured rows to BQ."""
    mock_client = mock_client_class.return_value
    mock_client.project = "test-project"
    mock_client.insert_rows_json.return_value = []

    with patch("streamlit.session_state", MagicMock()) as mock_ss:
        mock_ss.get.side_effect = lambda k, d=None: (
            "test_user" if k == "username" else ("jaccueille" if k == "org" else d)
        )
        mock_ss.interaction_id = "test-id"
        mock_ss.__contains__.side_effect = lambda k: k in ("interaction_id", "username")

        with patch("os.getenv", return_value="test-project"):
            telemetry.log_usage_event("click_button", {"button": "en_savoir_plus"})
            assert mock_client.insert_rows_json.called
            args, _ = mock_client.insert_rows_json.call_args
            row = args[1][0]
            assert row["event_name"] == "click_button"
            assert "en_savoir_plus" in row["payload"]


def test_log_page_view_deduplication():
    """Test that log_page_view deduplicates consecutive re-runs on the same page."""
    fake_state = {}

    def get_item(k, default=None):
        return fake_state.get(k, default)

    def set_item(k, v):
        fake_state[k] = v

    mock_ss = MagicMock()
    mock_ss.get.side_effect = get_item
    mock_ss.__setitem__.side_effect = set_item

    with patch("streamlit.session_state", mock_ss), patch("services.telemetry.log_usage_event") as mock_log_usage:
        # First visit to Accueil
        telemetry.log_page_view("Accueil")
        assert fake_state.get("current_page") == "Accueil"
        assert mock_log_usage.called
        assert mock_log_usage.call_args[0][0] == "page_view"
        assert mock_log_usage.call_args[0][1]["page"] == "Accueil"

        mock_log_usage.reset_mock()

        # Second re-run on Accueil (should NOT log again)
        telemetry.log_page_view("Accueil")
        assert not mock_log_usage.called

        # Navigate to Formulaire (should log with origin=Accueil)
        telemetry.log_page_view("Formulaire")
        assert fake_state.get("current_page") == "Formulaire"
        assert fake_state.get("previous_page") == "Accueil"
        assert mock_log_usage.called
        assert mock_log_usage.call_args[0][1]["origin"] == "Accueil"

