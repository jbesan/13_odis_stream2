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
    
    assert data["odis"].empty
    assert data["pois"].empty


def test_get_app_data_uses_the_active_release_complete_bundle(monkeypatch):
    """All app data, including referentials, comes from one release key."""
    release_context = data_loader.ReleaseContext(
        bucket_name="odis-stream2-eu",
        datasets_prefix="datasets",
        version="run-123",
        artifacts=(),
    )
    complete_data = {
        "odis": pd.DataFrame({"libgeo": ["Bordeaux"]}),
        "pois": pd.DataFrame({"name": ["Mairie"]}),
        "waldec_index": pd.DataFrame({"count": [0]}),
    }
    monkeypatch.setattr(
        data_loader, "get_active_release_context", lambda: release_context
    )
    monkeypatch.setattr(
        data_loader, "_get_scoring_datasets_for_release", lambda _: complete_data
    )

    app_data = data_loader.get_app_data()
    assert not app_data["odis"].empty
    assert not app_data["pois"].empty
    assert "count" in app_data["waldec_index"].columns


def test_async_preload_does_not_resolve_gcs_before_its_thread_runs(monkeypatch):
    calls = []

    class DeferredThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            calls.append("thread_started")

    monkeypatch.setattr(data_loader.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        data_loader,
        "get_active_release_context",
        lambda: calls.append("gcs_resolved"),
    )
    monkeypatch.setitem(data_loader._SCORING_PRELOAD_STATUS, "in_progress", False)

    data_loader.preload_scoring_datasets_async()

    assert calls == ["thread_started"]


def test_release_context_is_a_stable_streamlit_cache_key(monkeypatch):
    """The complete bundle cache is keyed by immutable release metadata."""
    context = data_loader.ReleaseContext(
        bucket_name="odis-stream2-eu",
        datasets_prefix="datasets",
        version="run-cache-key",
        artifacts=(),
    )
    calls = []
    monkeypatch.setattr(
        data_loader,
        "load_referentiels_raw",
        lambda release_context: calls.append(release_context) or {},
    )
    data_loader.get_referentiels_data.clear()

    assert data_loader.get_referentiels_data(context) == {}
    assert data_loader.get_referentiels_data(context) == {}
    assert calls == [context]


def test_waldec_enrichment_supplies_zero_counts_without_association_data():
    raw_index = pd.DataFrame(
        {"label": ["Culture", "Sport"]}, index=pd.Index(["006001", "011002"])
    )

    enriched, top = data_loader._enrich_waldec_index(raw_index, pd.DataFrame())

    assert enriched["count"].to_dict() == {"006001": 0, "011002": 0}
    assert top.equals(enriched)
