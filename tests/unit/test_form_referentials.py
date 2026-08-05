import pandas as pd

from ui.forms import get_commune_options_for_form


def test_commune_options_include_department_for_duplicate_names():
    app_data = {
        "depcom_df": pd.DataFrame(
            {
                "libgeo": ["Mérignac", "Mérignac"],
                "dep_code": ["33", "16"],
            },
            index=pd.Index(["33281", "16215"], name="codgeo"),
        )
    }

    assert get_commune_options_for_form(app_data) == {
        "33281": "Mérignac (33)",
        "16215": "Mérignac (16)",
    }


def test_commune_options_use_population_rank_from_the_complete_bundle():
    app_data = {
        "depcom_df": pd.DataFrame(),
        "odis": pd.DataFrame(
            {
                "libgeo": ["Petite ville", "Grande ville"],
                "dep_code": ["33", "69"],
                "population": [1_000, 100_000],
            },
            index=pd.Index(["33001", "69123"], name="codgeo"),
        ),
    }

    assert list(get_commune_options_for_form(app_data)) == ["69123", "33001"]
