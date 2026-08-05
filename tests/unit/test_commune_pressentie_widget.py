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
    """Test that render_mobility_form initializes commune pressentie directly using Streamlit native key binding."""
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
        if key == "ui_commune_pressentie":
            assert "index" not in kwargs, "selectbox for ui_commune_pressentie should not use index parameter"
            assert format_func is not None, "format_func must be provided for codgeo labels"
            assert format_func("69123") == "Lyon (69)"
            assert format_func("75056") == "Paris (75)"
        val = session_state.get(key)
        if val is None and options:
            val = options[0]
        return val

    with patch("app.ui.forms.st.session_state", session_state), \
         patch("app.ui.forms.st.checkbox", side_effect=mock_checkbox), \
         patch("app.ui.forms.st.selectbox", side_effect=mock_selectbox), \
         patch("app.ui.forms.st.multiselect"), \
         patch("app.ui.forms.st.radio"), \
         patch("app.ui.forms.st.markdown"), \
         patch("app.ui.forms.st.divider"), \
         patch("app.ui.forms.st.columns", return_value=[MagicMock(), MagicMock()]), \
         patch("app.ui.forms.st.container", return_value=MagicMock()):
        
        render_mobility_form(mock_app_data)

        assert "ui_commune_pressentie" in session_state
        assert session_state["ui_commune_pressentie"] == "69123"


@pytest.mark.unit
def test_render_mobility_form_handles_missing_regions():
    """A malformed legacy referential must not crash on region defaulting."""
    mock_app_data = {
        "dept_details": {"75": {"reg_code": "11", "label": "Paris"}},
        "regions_names": {},
    }
    session_state = SessionStateDict({"ui_departement": "75"})

    with patch("app.ui.forms.st.session_state", session_state), \
         patch("app.ui.forms.st.columns", return_value=[MagicMock(), MagicMock()]), \
         patch("app.ui.forms.st.multiselect", return_value=[]), \
         patch("app.ui.forms.st.checkbox", return_value=False), \
         patch("app.ui.forms.st.markdown"), \
         patch("app.ui.forms.st.divider"), \
         patch("app.ui.forms.st.container", return_value=MagicMock()), \
         patch("app.ui.forms.st.radio"):
        render_mobility_form(mock_app_data)

    assert session_state["ui_mobility_region"] == []


@pytest.mark.unit
def test_city_size_radio_hash_invalidation():
    """Test that changing ui_target_city_size_label instantly updates SearchCriterias target_population."""
    from app.ui.forms import create_search_criterias_from_inputs

    mock_app_data = {
        "coddep_set": ["75"],
        "dept_details": {"75": {"reg_code": "11"}},
        "depcom_df": pd.DataFrame({"dep_code": ["75"], "libgeo": ["Paris"]}, index=["75056"]),
        "odis": pd.DataFrame(),
    }

    session_state = SessionStateDict({
        "ui_departement": "75",
        "ui_commune": "Paris",
        "ui_target_city_size_label": "🏘️ Petite Ville",
    })

    with patch("app.ui.forms.st.session_state", session_state):
        
        criterias1 = create_search_criterias_from_inputs(mock_app_data)
        pop1 = criterias1.target_population
        hash1 = criterias1.compute_hash()

        # Change city size radio selection
        session_state["ui_target_city_size_label"] = "🏙️ Ville moyenne"

        criterias2 = create_search_criterias_from_inputs(mock_app_data)
        pop2 = criterias2.target_population
        hash2 = criterias2.compute_hash()

        assert pop1 != pop2, f"Expected target populations to differ but got {pop1} == {pop2}"
        assert hash1 != hash2, "Expected search criteria hash to change when city size radio changes"
