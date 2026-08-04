import pandas as pd
import pytest
from pathlib import Path

from pipeline.build import (
    PLM_FAMILIES,
    consolidate_plm_communes,
    consolidate_plm_detail_list,
    consolidate_plm_vertical,
)

OUTPUT_DIR = Path("pipeline/cache/output")


@pytest.fixture
def plm_mapping():
    return {parent: list(children) for parent, children in PLM_FAMILIES.items()}


@pytest.fixture
def plm_communes_fixture():
    """All PLM representations, with exact expected child-only fallbacks."""
    rows = []
    expected = {}
    for family_index, (parent, children) in enumerate(PLM_FAMILIES.items(), start=1):
        child_population = list(range(10, 10 + len(children)))
        child_rent = [20.0 + family_index, 30.0 + family_index] * (len(children) // 2)
        if len(child_rent) < len(children):
            child_rent.append(20.0 + family_index)
        expected_population = sum(child_population)
        expected_rent = (
            sum(
                value * population
                for value, population in zip(child_rent, child_population)
            )
            / expected_population
        )
        expected[parent] = {
            "population": expected_population,
            "log_soc_total": 0.0,
            "loyer_app_m2": expected_rent,
            "has_gare": 1.0,
        }
        rows.append(
            {
                "codgeo": parent,
                "population": float("nan"),
                "log_soc_total": float("nan"),
                "loyer_app_m2": float("nan"),
                "has_gare": float("nan"),
                "pol_num": float("nan"),
            }
        )
        for code, population, rent in zip(children, child_population, child_rent):
            rows.append(
                {
                    "codgeo": code,
                    "population": population,
                    "log_soc_total": 0.0,
                    "loyer_app_m2": rent,
                    "has_gare": 1.0 if code == children[0] else 0.0,
                    "pol_num": 99.0,
                }
            )
    return pd.DataFrame(rows), expected


def test_plm_contract_uses_exact_child_fallbacks_and_preserves_zero(
    plm_communes_fixture,
):
    source, expected = plm_communes_fixture

    result = consolidate_plm_communes(source)

    assert (
        not result["codgeo"]
        .isin([child for family in PLM_FAMILIES.values() for child in family])
        .any()
    )
    for parent, values in expected.items():
        row = result.loc[result["codgeo"] == parent].iloc[0]
        assert row["population"] == values["population"]
        assert row["log_soc_total"] == values["log_soc_total"]
        assert row["loyer_app_m2"] == pytest.approx(values["loyer_app_m2"])
        assert row["has_gare"] == values["has_gare"]
        assert pd.isna(row["pol_num"]), (
            "parent-only metrics must not be inferred from children"
        )


def test_plm_contract_never_adds_parent_and_child_totals():
    children = PLM_FAMILIES["75056"]
    source = pd.DataFrame(
        [
            {
                "codgeo": "75056",
                "population": 100.0,
                "log_soc_total": 0.0,
                "loyer_app_m2": 0.0,
            },
            *[
                {
                    "codgeo": code,
                    "population": 5.0,
                    "log_soc_total": 1.0,
                    "loyer_app_m2": 30.0,
                }
                for code in children
            ],
        ]
    )

    result = consolidate_plm_communes(source)
    parent = result.loc[result["codgeo"] == "75056"].iloc[0]

    assert parent["population"] == 100.0
    assert parent["log_soc_total"] == 0.0
    assert parent["loyer_app_m2"] == 0.0


def test_plm_contract_rejects_incomplete_children_without_parent_value():
    source = pd.DataFrame(
        [
            {"codgeo": "75056", "population": float("nan")},
            {"codgeo": "75101", "population": 10.0},
        ]
    )

    with pytest.raises(ValueError, match="only 1/20 child rows"):
        consolidate_plm_communes(source)


def test_plm_contract_rejects_unclassified_numeric_metric():
    source = pd.DataFrame(
        [{"codgeo": "75056", "population": 1.0, "new_unclassified_metric": 2.0}]
    )

    with pytest.raises(ValueError, match="new_unclassified_metric"):
        consolidate_plm_communes(source)


def test_plm_vertical_contract_preserves_existing_parent_group():
    source = pd.DataFrame(
        [
            {"codgeo": "75056", "id_waldec": "parent", "count": 7},
            {"codgeo": "75101", "id_waldec": "parent", "count": 3},
            {"codgeo": "75102", "id_waldec": "parent", "count": 4},
            {"codgeo": "75101", "id_waldec": "children-only", "count": 2},
            {"codgeo": "75102", "id_waldec": "children-only", "count": 5},
        ]
    )

    result = consolidate_plm_vertical(source, "codgeo", ["id_waldec"], "count")
    result = result[~result["codgeo"].isin(PLM_FAMILIES["75056"])]

    assert result.loc[result["id_waldec"] == "parent", "count"].tolist() == [7]
    assert result.loc[result["id_waldec"] == "children-only", "count"].tolist() == [7]


def test_plm_detail_contract_preserves_existing_parent_id():
    source = pd.DataFrame(
        [
            {"codgeo": "75056", "id": "shared", "name": "parent"},
            {"codgeo": "75101", "id": "shared", "name": "child duplicate"},
            {"codgeo": "75102", "id": "child-only", "name": "child"},
        ]
    )

    result = consolidate_plm_detail_list(source, "codgeo")
    result = result[~result["codgeo"].isin(PLM_FAMILIES["75056"])]

    assert result.loc[result["id"] == "shared", "name"].tolist() == ["parent"]
    assert result.loc[result["id"] == "child-only", "codgeo"].tolist() == ["75056"]


def test_communes_plm_consolidation(plm_mapping):
    """Verify that the published PLM population follows its source contract."""
    communes_path = OUTPUT_DIR / "odis_communes.parquet"
    population_path = Path("pipeline/cache/clean/population.parquet")
    if not communes_path.exists():
        pytest.skip("odis_communes.parquet does not exist yet. Run the pipeline first.")
    if not population_path.exists():
        pytest.skip("population.parquet does not exist yet. Run the pipeline first.")

    df = pd.read_parquet(communes_path, engine="fastparquet")
    population = pd.read_parquet(population_path, engine="fastparquet")
    population["codgeo"] = population["codgeo"].astype(str)

    # Assert 'codgeo' exists
    assert "codgeo" in df.columns

    # Verify no child arrondissements exist in communes
    all_children = []
    for children in plm_mapping.values():
        all_children.extend(children)

    arrondissements_found = df[df["codgeo"].isin(all_children)]
    assert arrondissements_found.empty, (
        f"Found individual PLM arrondissements in communes dataset: {arrondissements_found['codgeo'].unique()}"
    )

    # Verify parents exist
    for parent in plm_mapping.keys():
        parent_rows = df[df["codgeo"] == parent]
        assert not parent_rows.empty, (
            f"Parent code {parent} is missing from communes dataset"
        )

        row = parent_rows.iloc[0]

        parent_population = population.loc[
            population["codgeo"] == parent, "population"
        ].iloc[0]
        child_population = population.loc[
            population["codgeo"].isin(plm_mapping[parent]), "population"
        ].sum(min_count=1)
        expected_population = (
            parent_population if pd.notna(parent_population) else child_population
        )
        assert row["population"] == expected_population, (
            f"Parent {parent} population must be the authoritative parent value "
            f"or, if unavailable, the child total; got {row['population']}"
        )


def test_vertical_tables_plm_consolidation(plm_mapping):
    """Verify that vertical tables contain parent data and no arrondissement data."""
    all_children = []
    for children in plm_mapping.values():
        all_children.extend(children)

    vertical_files = [
        ("odis_associations_agg.parquet", "codgeo"),
        ("odis_formations_agg.parquet", "codgeo"),
        ("odis_ccas.parquet", "codgeo"),
        ("odis_refugee_associations.parquet", "codgeo"),
        ("odis_pois.parquet", "codgeo"),
    ]

    for filename, codgeo_col in vertical_files:
        filepath = OUTPUT_DIR / filename
        if not filepath.exists():
            continue

        df = pd.read_parquet(filepath, engine="fastparquet")
        df[codgeo_col] = df[codgeo_col].astype(str)

        # Assert no child arrondissements exist in vertical table
        arr_rows = df[df[codgeo_col].isin(all_children)]
        assert arr_rows.empty, (
            f"Found child arrondissements in {filename}: {arr_rows[codgeo_col].unique()}"
        )

        # Assert parent codes exist in vertical table
        for parent in plm_mapping.keys():
            parent_rows = df[df[codgeo_col] == parent]
            assert not parent_rows.empty, (
                f"Parent code {parent} is missing from {filename}"
            )

            # Additional validation: count or records should be positive
            if filename == "odis_associations_agg.parquet":
                assert parent_rows["count"].sum() > 0, (
                    f"Parent {parent} has 0 associations in {filename}"
                )
            elif filename == "odis_formations_agg.parquet":
                assert parent_rows["count"].sum() > 0, (
                    f"Parent {parent} has 0 formations in {filename}"
                )
            elif filename == "odis_pois.parquet":
                assert len(parent_rows) > 0, f"Parent {parent} has 0 POIs in {filename}"
