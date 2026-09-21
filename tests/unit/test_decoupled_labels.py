"""Unit tests verifying decoupling of UI display labels from technical codes."""

from unittest.mock import patch, MagicMock
import config as cfg
from ui.form_state import FormState, long_term_housing_key, health_key
from ui.forms import render_housing_form, render_health_form


def test_referential_labels_coverage():
    """Verify all technical options have display label mappings."""
    for opt in cfg.LOGEMENT_OPTIONS:
        assert opt in cfg.LOGEMENT_LABELS, f"Option {opt} missing from LOGEMENT_LABELS"

    for opt in cfg.SANTE_OPTIONS:
        assert opt in cfg.SANTE_LABELS, f"Option {opt} missing from SANTE_LABELS"

    for opt in cfg.HEBERGEMENT_OPTIONS:
        assert opt in cfg.HEBERGEMENT_LABELS, f"Option {opt} missing from HEBERGEMENT_LABELS"


def test_target_label_mappings():
    """Verify specific target UI label renamings."""
    assert cfg.LOGEMENT_LABELS["Location"] == "Location parc privé"
    assert cfg.SANTE_LABELS["Hôpital"] == "Suivi à l'hôpital"
    assert cfg.SANTE_LABELS["Maternité"] == "Suivi dans une maternité"


def test_technical_state_and_keys_unchanged():
    """Verify session state keys and collected values remain technical codes."""
    # Key generation uses technical codes
    assert long_term_housing_key("Location") == "ui_logement_cb_location"
    assert health_key("Hôpital") == "ui_sante_cb_hôpital"
    assert health_key("Maternité") == "ui_sante_cb_maternité"

    # FormState collects technical codes
    state = {
        "ui_logement_cb_location": True,
        "ui_sante_cb_hôpital": True,
    }
    form = FormState(state)
    assert form.selected_long_term_housing() == ["Location"]
    assert form.selected_health() == ["Hôpital"]


def test_ui_checkboxes_render_friendly_labels():
    """Verify that Streamlit checkboxes receive decoupled friendly labels while keeping technical keys."""
    # Test housing form
    with patch("streamlit.session_state", {}), \
         patch("streamlit.columns", return_value=(MagicMock(), MagicMock())), \
         patch("streamlit.markdown"), \
         patch("streamlit.checkbox") as mock_checkbox, \
         patch("streamlit.selectbox"), \
         patch("streamlit.space"):
        render_housing_form()

        rendered_labels = [call.args[0] for call in mock_checkbox.call_args_list]
        rendered_keys = [call.kwargs.get("key") for call in mock_checkbox.call_args_list]

        assert "Location parc privé" in rendered_labels
        assert "ui_logement_cb_location" in rendered_keys

    # Test health form
    with patch("streamlit.session_state", {}), \
         patch("streamlit.markdown"), \
         patch("streamlit.checkbox") as mock_health_checkbox:
        render_health_form()

        rendered_health_labels = [call.args[0] for call in mock_health_checkbox.call_args_list]
        rendered_health_keys = [call.kwargs.get("key") for call in mock_health_checkbox.call_args_list]

        assert "Suivi à l'hôpital" in rendered_health_labels
        assert "Suivi dans une maternité" in rendered_health_labels
        assert "ui_sante_cb_hôpital" in rendered_health_keys
        assert "ui_sante_cb_maternité" in rendered_health_keys
