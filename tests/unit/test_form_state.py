import pandas as pd

import config as cfg
from core.models import CriteriaItem, SearchCriterias
from ui.form_state import FormState, health_key, housing_key


def _app_data():
    return {
        "depcom_df": pd.DataFrame(
            {"dep_code": ["33"], "libgeo": ["Bordeaux"]}, index=["33063"]
        ),
        "rome_index": pd.DataFrame(
            {"label": ["Développeur"]}, index=["M1805"]
        ),
        "codformations_index": pd.DataFrame(
            {"label": ["Informatique"]}, index=["326"]
        ),
        "inclusion_services_index": pd.DataFrame(
            {"label": ["Français"]}, index=["fle"]
        ),
        "waldec_index": pd.DataFrame(
            {"label": ["Culture"]}, index=["006030"]
        ),
        "commune_names": {},
    }


def test_initialize_uses_widget_keys_without_unprefixed_mirrors():
    state = {}
    FormState(state).initialize(cfg.DEMO_DATA_DEFAULT)

    assert state["ui_departement"] == "33"
    assert state["ui_commune"] == "Bordeaux"
    assert "departement_actuel" not in state
    assert "commune_actuelle" not in state
    assert "demo_data" not in state
    assert state[housing_key("Location avec Intermédiation")] is True


def test_hydrate_and_collect_use_one_canonical_value_per_composite():
    state = {"org": None}
    form = FormState(state)
    form.initialize(cfg.DEMO_DATA_DEFAULT)
    form.hydrate(
        SearchCriterias(
            commune_actuelle=CriteriaItem(code="33063", label="Bordeaux"),
            loc_search_area="departement",
            loc_search_code=["33"],
            nb_adultes=1,
            codes_metiers=[
                [CriteriaItem(code="M1805", label="Développeur")]
            ],
            codes_formations=[
                [CriteriaItem(code="326", label="Informatique")]
            ],
            hebergement_cible=["Chez l'habitant"],
            besoin_sante=["Maternité"],
            inc_services_selection=[CriteriaItem(code="fle", label="Français")],
            inc_asso_add_selection=[CriteriaItem(code="006030", label="Culture")],
        ),
        app_data=_app_data(),
    )

    assert "ui_hebergement_cible" not in state
    assert "ui_besoin_sante" not in state
    assert "ui_inc_services_selection" not in state
    assert "ui_inc_asso_add_selection" not in state
    assert state[housing_key("Chez l'habitant")] is True
    assert state[health_key("Maternité")] is True

    criteria = form.collect(_app_data())
    assert criteria.hebergement_cible == ["Chez l'habitant"]
    assert criteria.besoin_sante == ["Maternité"]
    assert criteria.codes_metiers[0][0].code == "M1805"
    assert criteria.inc_services_selection[0].code == "fle"
    assert criteria.inc_asso_add_selection[0].code == "006030"


def test_named_weight_profile_is_derived_without_expert_flag():
    state = {}
    form = FormState(state)
    form.hydrate(
        {"weight_profile": "Famille"}, overwrite=True, exclude_unset=False
    )

    assert state["ui_weight_profile"] == "Famille"
    assert state["ui_poids_education"] == 1.0
    assert "ui_expert_weights" not in state


def test_prepare_editor_restores_active_commune_and_preserves_unsaved_draft():
    state = {"org": None}
    app_data = _app_data()
    app_data["depcom_df"] = pd.DataFrame(
        {
            "dep_code": ["33", "75"],
            "libgeo": ["Bordeaux", "Paris"],
        },
        index=["33063", "75056"],
    )
    criteria = SearchCriterias(
        commune_actuelle=CriteriaItem(code="75056", label="Paris"),
        loc_search_area="departement",
        loc_search_code=["75"],
        nb_adultes=2,
    )
    form = FormState(state)

    assert form.prepare_editor(
        criteria,
        source_hash=criteria.compute_hash(),
        app_data=app_data,
    )
    assert state["ui_departement"] == "75"
    assert state["ui_commune"] == "Paris"
    assert state["ui_nb_adultes"] == 2

    # A dialog may be closed and reopened before a new search is launched.
    # Keep that user draft instead of injecting the previous active criteria.
    state["ui_nb_adultes"] = 3
    assert not form.prepare_editor(
        criteria,
        source_hash=criteria.compute_hash(),
        app_data=app_data,
    )
    assert state["ui_nb_adultes"] == 3

    newer_criteria = criteria.model_copy(update={"nb_adultes": 1})
    assert form.prepare_editor(
        newer_criteria,
        source_hash=newer_criteria.compute_hash(),
        app_data=app_data,
    )
    assert state["ui_nb_adultes"] == 1
