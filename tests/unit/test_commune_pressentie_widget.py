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
            {
                "population": [10000, 50000],
                "libgeo": ["Paris", "Lyon"],
                "dep_code": ["75", "69"],
            },
            index=["75056", "69123"],
        ),
    }

    session_state = SessionStateDict(
        {
            "ui_departement": "75",
            "ui_has_commune_pressentie": True,
            "ui_commune_pressentie": "69123",
            "ui_loc_search_area": "departement",
            "ui_mobility_region": ["11"],
            "ui_mobility_dept": ["75"],
            "ui_target_city_size_label": "Petite Ville (10k-50k)",
        }
    )

    def mock_checkbox(label, key=None, **kwargs):
        return session_state.get(key, False)

    def mock_selectbox(label, options=None, format_func=None, key=None, **kwargs):
        if key == "ui_commune_pressentie":
            assert "index" not in kwargs, (
                "selectbox for ui_commune_pressentie should not use index parameter"
            )
            assert format_func is not None, (
                "format_func must be provided for codgeo labels"
            )
            assert format_func("69123") == "Lyon (69)"
            assert format_func("75056") == "Paris (75)"
        val = session_state.get(key)
        if val is None and options:
            val = options[0]
        return val

    with (
        patch("app.ui.forms.st.session_state", session_state),
        patch("app.ui.forms.st.checkbox", side_effect=mock_checkbox),
        patch("app.ui.forms.st.selectbox", side_effect=mock_selectbox),
        patch("app.ui.forms.st.multiselect"),
        patch("app.ui.forms.st.radio"),
        patch(
            "app.ui.forms.st.select_slider", return_value=("🏡 Bourg", "🏘️ Petite Ville")
        ),
        patch("app.ui.forms.st.caption"),
        patch("app.ui.forms.st.markdown"),
        patch("app.ui.forms.st.divider"),
        patch("app.ui.forms.st.columns", return_value=[MagicMock(), MagicMock()]),
        patch("app.ui.forms.st.container", return_value=MagicMock()),
    ):
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

    with (
        patch("app.ui.forms.st.session_state", session_state),
        patch("app.ui.forms.st.columns", return_value=[MagicMock(), MagicMock()]),
        patch("app.ui.forms.st.multiselect", return_value=[]),
        patch("app.ui.forms.st.checkbox", return_value=False),
        patch("app.ui.forms.st.markdown"),
        patch("app.ui.forms.st.divider"),
        patch("app.ui.forms.st.container", return_value=MagicMock()),
        patch("app.ui.forms.st.radio"),
        patch(
            "app.ui.forms.st.select_slider", return_value=("🏡 Bourg", "🏘️ Petite Ville")
        ),
        patch("app.ui.forms.st.caption"),
    ):
        render_mobility_form(mock_app_data)

    assert session_state["ui_mobility_region"] == []


@pytest.mark.unit
def test_city_size_slider_hash_invalidation():
    """Test that changing ui_target_city_size_range instantly updates SearchCriterias target_population."""
    from app.ui.forms import create_search_criterias_from_inputs

    mock_app_data = {
        "coddep_set": ["75"],
        "dept_details": {"75": {"reg_code": "11"}},
        "depcom_df": pd.DataFrame(
            {"dep_code": ["75"], "libgeo": ["Paris"]}, index=["75056"]
        ),
        "odis": pd.DataFrame(),
    }

    session_state = SessionStateDict(
        {
            "ui_departement": "75",
            "ui_commune": "Paris",
            "ui_target_city_size_range": ("🏡 Bourg", "🏘️ Petite Ville"),
        }
    )

    with patch("app.ui.forms.st.session_state", session_state):
        criterias1 = create_search_criterias_from_inputs(mock_app_data)
        size1 = criterias1.target_city_size
        hash1 = criterias1.compute_hash()

        # Change city size slider selection
        session_state["ui_target_city_size_range"] = (
            "🏙️ Ville moyenne",
            "🏙️ Ville moyenne",
        )

        criterias2 = create_search_criterias_from_inputs(mock_app_data)
        size2 = criterias2.target_city_size
        hash2 = criterias2.compute_hash()

        assert size1 != size2, (
            f"Expected target city sizes to differ but got {size1} == {size2}"
        )
        assert hash1 != hash2, (
            "Expected search criteria hash to change when city size slider changes"
        )


@pytest.mark.unit
def test_render_mobility_form_city_size_select_slider_and_caption():
    """Verify that render_mobility_form renders city size select_slider with dynamic caption."""
    mock_app_data = {
        "dept_details": {"75": {"reg_code": "11", "label": "Paris"}},
        "regions_names": {},
    }
    session_state = SessionStateDict({"ui_departement": "75"})

    mock_slider = MagicMock(return_value=("🏡 Bourg", "🏘️ Petite Ville"))
    mock_caption = MagicMock()
    mock_markdown = MagicMock()

    with (
        patch("app.ui.forms.st.session_state", session_state),
        patch("app.ui.forms.st.columns", return_value=[MagicMock(), MagicMock()]),
        patch("app.ui.forms.st.multiselect", return_value=[]),
        patch("app.ui.forms.st.checkbox", return_value=False),
        patch("app.ui.forms.st.markdown", mock_markdown),
        patch("app.ui.forms.st.divider"),
        patch("app.ui.forms.st.container", return_value=MagicMock()),
        patch("app.ui.forms.st.caption", mock_caption),
        patch("app.ui.forms.st.select_slider", mock_slider),
    ):
        render_mobility_form(mock_app_data)

    mock_markdown.assert_any_call(
        "##### Taille de la ville recherchée (bassin de vie)",
        help="Le bassin de vie intègre la ville et ses banlieues",
    )
    mock_slider.assert_called_once()
    mock_caption.assert_called_once_with(
        'Bassin de vie ciblé idéalement entre **5 000** et **50 000** habitants', width='stretch', text_alignment='center'
    )


@pytest.mark.unit
def test_render_mobility_form_shows_zones_user_description_warning():
    """Verify that render_mobility_form displays st.warning when org has zones_user_description and filter is active."""
    from config import Org

    mock_app_data = {
        "dept_details": {"75": {"reg_code": "11", "label": "Paris"}},
        "regions_names": {},
    }
    org = Org(
        id="jaccueille",
        name="J'Accueille",
        zones_user_description="Seules les villes disposant d'une coordination départementale seront retenues.",
    )
    session_state = SessionStateDict(
        {"org": org, "ui_org_strategic_locations_filter": True}
    )
    mock_warning = MagicMock()

    with (
        patch("app.ui.forms.st.session_state", session_state),
        patch("app.ui.forms.st.segmented_control", return_value=None),
        patch("app.ui.forms.st.multiselect", return_value=[]),
        patch("app.ui.forms.st.checkbox", return_value=False),
        patch("app.ui.forms.st.markdown"),
        patch("app.ui.forms.st.divider"),
        patch("app.ui.forms.st.container", return_value=MagicMock()),
        patch("app.ui.forms.st.radio"),
        patch("app.ui.forms.st.warning", mock_warning),
    ):
        render_mobility_form(mock_app_data)

    mock_warning.assert_called_once_with(
        "Seules les villes disposant d'une coordination départementale seront retenues."
    )


@pytest.mark.unit
def test_render_mobility_form_hides_warning_when_filter_disabled():
    """Verify that render_mobility_form hides st.warning when ui_org_strategic_locations_filter is False."""
    from config import Org

    mock_app_data = {
        "dept_details": {"75": {"reg_code": "11", "label": "Paris"}},
        "regions_names": {},
    }
    org = Org(
        id="jaccueille",
        name="J'Accueille",
        zones_user_description="Seules les villes disposant d'une coordination départementale seront retenues.",
    )
    session_state = SessionStateDict(
        {"org": org, "ui_org_strategic_locations_filter": False}
    )
    mock_warning = MagicMock()

    with (
        patch("app.ui.forms.st.session_state", session_state),
        patch("app.ui.forms.st.segmented_control", return_value=None),
        patch("app.ui.forms.st.multiselect", return_value=[]),
        patch("app.ui.forms.st.checkbox", return_value=False),
        patch("app.ui.forms.st.markdown"),
        patch("app.ui.forms.st.divider"),
        patch("app.ui.forms.st.container", return_value=MagicMock()),
        patch("app.ui.forms.st.radio"),
        patch("app.ui.forms.st.warning", mock_warning),
    ):
        render_mobility_form(mock_app_data)

    mock_warning.assert_not_called()


@pytest.mark.unit
def test_render_mobility_form_no_warning_when_description_empty():
    """Verify that render_mobility_form does not show st.warning if org has no zones_user_description."""
    from config import Org

    mock_app_data = {
        "dept_details": {"75": {"reg_code": "11", "label": "Paris"}},
        "regions_names": {},
    }
    org = Org(
        id="autre_org",
        name="Autre",
        zones_user_description=None,
    )
    session_state = SessionStateDict(
        {"org": org, "ui_org_strategic_locations_filter": True}
    )
    mock_warning = MagicMock()

    with (
        patch("app.ui.forms.st.session_state", session_state),
        patch("app.ui.forms.st.segmented_control", return_value=None),
        patch("app.ui.forms.st.multiselect", return_value=[]),
        patch("app.ui.forms.st.checkbox", return_value=False),
        patch("app.ui.forms.st.markdown"),
        patch("app.ui.forms.st.divider"),
        patch("app.ui.forms.st.container", return_value=MagicMock()),
        patch("app.ui.forms.st.radio"),
        patch("app.ui.forms.st.warning", mock_warning),
    ):
        render_mobility_form(mock_app_data)

    mock_warning.assert_not_called()
