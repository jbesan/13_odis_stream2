"""Unit tests for app data loader and session state cache."""

import pandas as pd
import config as cfg
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
    monkeypatch.setattr(data_loader, "_load_parquet", lambda *_, **__: refs)

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

    assert "commune_names" in data
    assert "regions_names" in data


def test_get_app_data_uses_the_active_release_complete_bundle(monkeypatch):
    """All app data, including referentials, comes from one release key."""
    complete_data = {
        "odis": pd.DataFrame({"libgeo": ["Bordeaux"]}),
        "pois": pd.DataFrame({"name": ["Mairie"]}),
        "waldec_index": pd.DataFrame({"count": [0]}),
    }
    monkeypatch.setattr(
        data_loader, "get_active_release_version", lambda: "run-123"
    )
    monkeypatch.setattr(
        data_loader, "get_scoring_datasets", lambda _: complete_data
    )

    app_data = data_loader.get_app_data()
    assert not app_data["odis"].empty
    assert not app_data["pois"].empty
    assert "count" in app_data["waldec_index"].columns


def test_release_version_is_a_stable_streamlit_cache_key(monkeypatch):
    """The complete bundle cache is keyed by immutable release version."""
    calls = []
    monkeypatch.setattr(
        data_loader,
        "load_app_data_raw",
        lambda version=None: calls.append(version) or {},
    )
    data_loader.get_scoring_datasets.clear()

    assert data_loader.get_scoring_datasets("run-cache-key") == {}
    assert data_loader.get_scoring_datasets("run-cache-key") == {}
    assert calls == ["run-cache-key"]


def test_waldec_enrichment_supplies_zero_counts_without_association_data():
    raw_index = pd.DataFrame(
        {"label": ["Culture", "Sport"]}, index=pd.Index(["006001", "011002"])
    )

    enriched, top = data_loader._enrich_waldec_index(raw_index, pd.DataFrame())

    assert enriched["count"].to_dict() == {"006001": 0, "011002": 0}
    assert top.equals(enriched)


def test_ensure_data_initialized_reuses_session_state(monkeypatch):
    """ensure_data_initialized reuses app_data in session_state without calling get_app_data."""
    import streamlit as st

    existing_data = {"existing": "bundle", "_release_id": "test-id"}
    st.session_state["app_data"] = existing_data

    calls = []
    monkeypatch.setattr(
        data_loader, "get_app_data", lambda: calls.append("get_app_data") or {}
    )

    # 1. Normal call reuses session state without fetching
    result = data_loader.ensure_data_initialized()
    assert result == existing_data
    assert calls == []

    # 2. force_reload=True bypasses the session cache
    reloaded = data_loader.ensure_data_initialized(force_reload=True)
    assert reloaded == {}
    assert calls == ["get_app_data"]


def test_load_app_data_raw_unifies_bundles(monkeypatch):
    """load_app_data_raw combines referentials and scoring datasets into a single bundle."""
    monkeypatch.setattr(
        data_loader, "load_referentiels_raw", lambda **_: {"ref_key": "val1"}
    )
    monkeypatch.setattr(
        data_loader,
        "load_scoring_datasets_raw",
        lambda refs, **_: {**refs, "scoring_key": "val2"},
    )

    data = data_loader.load_app_data_raw()
    assert data["ref_key"] == "val1"
    assert data["scoring_key"] == "val2"


def test_pois_loads_without_artificial_geometry(monkeypatch):
    """POIs dataset retains native float lat/lon without creating shapely Point objects."""
    pois = pd.DataFrame(
        {
            "category": ["education"],
            "name": ["Ecole Test"],
            "lat": [44.8378],
            "lon": [-0.5792],
            "codgeo": ["33063"],
        }
    )
    odis = pd.DataFrame(
        {
            "codgeo": ["33063"],
            "dep_code": ["33"],
            "population": [1000],
            "bassin_de_vie": ["33063"],
        }
    )

    def mock_load(path, *args, **kwargs):
        if cfg.POIS_FILE in path:
            return pois
        if cfg.ODIS_FILE in path:
            return odis
        return pd.DataFrame()

    monkeypatch.setattr(data_loader, "_load_parquet", mock_load)
    monkeypatch.setattr(
        data_loader, "get_salesforce_jaccueille_counts", lambda *_: pd.DataFrame()
    )

    data = data_loader.load_scoring_datasets_raw(refs_data={})
    pois_result = data["pois"]
    assert "lat" in pois_result.columns
    assert "lon" in pois_result.columns
    assert "geometry" not in pois_result.columns
