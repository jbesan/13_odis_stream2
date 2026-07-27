import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from app.ui.forms import render_mobility_form


class SessionStateDict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError:
            raise AttributeError(key)


@pytest.mark.unit
def test_render_commune_pressentie_form_initialization():
    """Test that render_mobility_form initializes commune pressentie pair state without warnings."""
    mock_app_data = {
        "coddep_set": ["75", "69"],
        "dept_details": {"75": {"reg_code": "11"}},
        "regions_names": {"11": "Île-de-France"},
        "reg_details": {},
        "reg_dep_mapping": {"11": ["75"]},
        "odis": pd.DataFrame(
            {"population": [10000, 50000], "libgeo": ["Paris", "Lyon"], "dep_code": ["75", "69"]},
            index=["75056", "69123"],
        )
    }

    session_state = SessionStateDict({
        "ui_departement": "75",
        "ui_has_commune_pressentie": True,
        "ui_commune_pressentie": "69123",
        "ui_france_search": False,
        "ui_region_search": False,
        "ui_mobility_region": ["11"],
        "ui_mobility_dept": ["75"],
        "ui_target_city_size_label": "Petite Ville (10k-50k)",
    })

    def mock_checkbox(label, key=None, **kwargs):
        return session_state.get(key, False)

    def mock_selectbox(label, options=None, format_func=None, key=None, **kwargs):
        if key == "ui_commune_pressentie_pair":
            assert "index" not in kwargs, "selectbox for ui_commune_pressentie_pair should not use index parameter"
        val = session_state.get(key)
        if val is None and options:
            val = options[0]
        return val

    with patch("app.ui.forms.get_app_data", return_value=mock_app_data), \
         patch("app.ui.forms.st.session_state", session_state), \
         patch("app.ui.forms.st.checkbox", side_effect=mock_checkbox), \
         patch("app.ui.forms.st.selectbox", side_effect=mock_selectbox), \
         patch("app.ui.forms.st.multiselect"), \
         patch("app.ui.forms.st.radio"), \
         patch("app.ui.forms.st.markdown"), \
         patch("app.ui.forms.st.divider"), \
         patch("app.ui.forms.st.columns", return_value=[MagicMock(), MagicMock()]), \
         patch("app.ui.forms.st.container", return_value=MagicMock()):
        
        render_mobility_form()

        assert "ui_commune_pressentie_pair" in session_state
        assert session_state["ui_commune_pressentie_pair"][0] == "69123"
        assert session_state["ui_commune_pressentie"] == "69123"
