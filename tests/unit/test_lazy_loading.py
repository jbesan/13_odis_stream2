import pandas as pd
from utils import data_loader


def test_load_referentiels_raw_builds_lightweight_form_indices(monkeypatch):
    """The form referentials are built from the active-release parquet only."""
    refs = pd.DataFrame(
        {
            "key": [
                "communes",
                "departements",
                "regions",
                "rome_codes",
                "formation_codes",
                "inclusion_services",
                "waldec_codes",
            ],
            "code": ["33063", "33", "75", "M1805", "114", "francais", "W1"],
            "label": [
                "Bordeaux",
                "Gironde",
                "Île-de-France",
                "Développement",
                "Formation",
                "Français",
                "Association",
            ],
            "reg_code": [None, "75", None, None, None, None, None],
        }
    )
    monkeypatch.setattr(data_loader, "_load_parquet", lambda _: refs)

    data = data_loader.load_referentiels_raw()

    assert "referentiels_raw" in data
    assert "depcom_df" in data
    assert "coddep_set" in data
    assert "scores_cat" in data
    assert "rome_index" in data
    assert len(data["rome_top_index"]) == len(data["rome_index"])
    assert "codformations_index" in data
    assert "inclusion_services_index" in data
    assert "waldec_index" in data
    
    depcom_df = data["depcom_df"]
    assert isinstance(depcom_df, pd.DataFrame)
    assert not depcom_df.empty
    assert "libgeo" in depcom_df.columns
    assert "dep_code" in depcom_df.columns
    
    assert data["odis"].empty
    assert data["pois"].empty


def test_get_app_data_uses_one_release_key_for_light_and_complete_bundles(monkeypatch):
    """Both bundle types derive from the same active GCS release identity."""
    tier1_data = {"odis": pd.DataFrame(), "depcom_df": pd.DataFrame({"libgeo": ["Bordeaux"]})}
    tier2_data = {
        "odis": pd.DataFrame({"libgeo": ["Bordeaux"]}),
        "pois": pd.DataFrame({"name": ["Mairie"]}),
        "waldec_index": pd.DataFrame({"count": [0]}),
    }
    monkeypatch.setattr(data_loader, "get_data_mtime", lambda: "gcs:run-123")
    monkeypatch.setattr(data_loader, "get_referentiels_data", lambda _: tier1_data)
    monkeypatch.setattr(
        data_loader, "_get_scoring_datasets_for_release", lambda _: tier2_data
    )

    tier1_data = data_loader.get_app_data(load_heavy=False)
    assert tier1_data["odis"].empty
    assert not tier1_data["depcom_df"].empty

    tier2_data = data_loader.get_app_data(load_heavy=True)
    assert not tier2_data["odis"].empty
    assert not tier2_data["pois"].empty
    assert "count" in tier2_data["waldec_index"].columns


def test_waldec_enrichment_supplies_zero_counts_without_association_data():
    raw_index = pd.DataFrame(
        {"label": ["Culture", "Sport"]}, index=pd.Index(["006001", "011002"])
    )

    enriched, top = data_loader._enrich_waldec_index(raw_index, pd.DataFrame())

    assert enriched["count"].to_dict() == {"006001": 0, "011002": 0}
    assert top.equals(enriched)
