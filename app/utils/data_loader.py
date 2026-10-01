"""Unified dataset loader and session state initializer for ODIS.

Loads baked-in local parquet datasets from the container filesystem and provides
a single process-wide cached bundle via Streamlit's cache_resource.
"""

import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Set

from google.cloud import storage  # noqa: F401 - Kept for test conftest.py mock compatibility
import pandas as pd
import pyarrow.parquet as pq
import streamlit as st
import yaml

import config as cfg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Complete runtime dataset filenames contract
_RUNTIME_DATASET_FILENAMES = (
    cfg.REFERENTIELS_FILE,
    cfg.ODIS_FILE,
    cfg.BV_FILE,
    cfg.POIS_FILE,
    cfg.AGG_ASSOCIATIONS_FILE,
    cfg.AGG_FORMATIONS_FILE,
    cfg.CCAS_FILE,
    cfg.REFUGEE_ASSOCIATIONS_FILE,
    cfg.LIVE_JOBS_FILE,
    cfg.SIAE_JOBS_FILE,
    cfg.SALESFORCE_JACCUEILLE_BDV_FILE,
)


# =============================================================================
# 1. Session State & Form Defaults Initialization (re-exported from ui.form_state)
# =============================================================================

from ui.form_state import (
    apply_demo_data_if_present,  # noqa: F401
    apply_logged_in_org_defaults,  # noqa: F401
    apply_search_criteria_to_ui,  # noqa: F401
    initialize_session_state,
)


# =============================================================================
# 2. Local Dataset Path & Manifest Resolution
# =============================================================================


def _get_dataset_cache_base_dir() -> str:
    """Determine local dataset directory."""
    for env_var in ("ODIS_DATASETS_DIR", "ODIS_CACHE_DIR", "ODIS_DATA_CACHE_DIR"):
        if val := os.getenv(env_var):
            return val
    active = os.path.join(cfg.APP_DIR, "data", "datasets", "active")
    return (
        active
        if os.path.isdir(active)
        else os.path.join(tempfile.gettempdir(), "odis_data_cache")
    )


def resolve_dataset_path(filename_or_path: str) -> Optional[str]:
    """Resolve one dataset filename from the local datasets directory."""
    fname = os.path.basename(filename_or_path)
    base = _get_dataset_cache_base_dir()
    direct = os.path.join(base, fname)
    if os.path.isfile(direct):
        return direct
    active = os.path.join(cfg.APP_DIR, "data", "datasets", "active", fname)
    return active if os.path.isfile(active) else direct


@st.cache_data(show_spinner=False)
def load_active_data_manifest() -> Dict[str, Any]:
    """Load the provenance manifest of the active local dataset release."""
    base = _get_dataset_cache_base_dir()
    for p in (
        os.path.join(base, "data_manifest.json"),
        os.path.join(cfg.APP_DIR, "data", "datasets", "active", "data_manifest.json"),
    ):
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            v = (
                manifest.get("manifest_version")
                or manifest.get("pipeline_run_id")
                or manifest.get("active_release_version")
                or "local"
            )
            manifest["manifest_version"] = manifest["active_release_version"] = v
            manifest["pipeline_run_id"] = manifest.get("pipeline_run_id", v)
            return manifest
    return {
        "manifest_version": "local-baked",
        "pipeline_run_id": "local-baked",
        "active_release_version": "local-baked",
        "outputs": [
            {"name": f, "sha256": "0" * 64, "size_bytes": 0}
            for f in _RUNTIME_DATASET_FILENAMES
        ],
    }


def get_active_release_version() -> str:
    """Return the active dataset release version identifier."""
    manifest = load_active_data_manifest()
    return str(
        manifest.get("pipeline_run_id")
        or manifest.get("active_release_version")
        or manifest.get("manifest_version")
        or "local"
    )


def get_data_mtime() -> str:
    """Return the active immutable release ID used as the cache key."""
    return f"gcs:{get_active_release_version()}"


# =============================================================================
# 3. Parquet & Config Loaders
# =============================================================================


def _load_parquet(
    path: str,
    columns: Optional[List[str]] = None,
    error_list: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Internal loader for parquet datasets with error tracking."""
    resolved = resolve_dataset_path(path)
    if not resolved or not os.path.exists(resolved):
        logger.error("File not found: %s", path)
        if error_list is not None:
            error_list.append(os.path.basename(path))
        return pd.DataFrame()
    return pd.read_parquet(resolved, columns=columns)


def load_parquet(
    path: str,
    columns: Optional[List[str]] = None,
    error_list: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Public loader for parquet datasets."""
    return _load_parquet(path, columns=columns, error_list=error_list)


def load_scores_config_as_df(config_path: str) -> pd.DataFrame:
    """Loads the scores configuration YAML as a DataFrame."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    data = []
    for item in config.get("scores", []):
        d = item.get("display", {})
        data.append(
            {
                "cat": item.get("category"),
                "score": item.get("id"),
                "label": d.get("name", item.get("id")),
                "description": d.get("tooltip", ""),
                "weight": item.get("weight", 1.0),
                "min_bound": item.get("min_bound"),
                "max_bound": item.get("max_bound"),
                "score_affichage": d.get("strong_point_text", ""),
                "high_value_adjective": d.get("high_value_adjective", ""),
                "bdv_factor": item.get("bdv_factor", 0.0),
                "metric": item.get("source_metric"),
                "computation": item.get("computation", "live"),
                "display_factor": d.get("display_factor", 1.0),
                "unit": d.get("unit", ""),
                "baseline": item.get("baseline", False),
                "format": d.get("format"),
                "missing_strategy": item.get("missing_strategy", "exclude"),
                "show": d.get("show", True),
                "metric_type": d.get("metric_type", "continuous"),
                "discrete_mapping": d.get("discrete_mapping"),
            }
        )
    return pd.DataFrame(data)


def get_pois_by_category(pois_df: pd.DataFrame, category: str) -> pd.DataFrame:
    """Filters POIs by category and returns a copy."""
    return (
        pois_df[pois_df["category"] == category].copy()
        if not pois_df.empty
        else pd.DataFrame()
    )


# =============================================================================
# 4. Auxiliary Data & Score Enrichments
# =============================================================================


def fetch_salesforce_jaccueille_bdv() -> pd.DataFrame:
    """Loads the pre-aggregated Salesforce J'accueille BDV table."""
    df = _load_parquet(cfg.SALESFORCE_JACCUEILLE_BDV_FILE)
    if df.empty:
        logger.warning("⚠️ [SALESFORCE] J'accueille BDV dataset is missing or empty")
    return df


def get_salesforce_jaccueille_counts(
    source_data: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Return the single Salesforce-derived source of J'Accueille score inputs."""
    df = source_data if source_data is not None else fetch_salesforce_jaccueille_bdv()
    required = {"bassin_de_vie", "contact_count", "lead_count"}
    if df.empty or not required.issubset(df.columns):
        return pd.DataFrame(
            columns=["bassin_de_vie", "heb_accueillants_count", "prospects_count"]
        )
    counts = df[["bassin_de_vie", "contact_count", "lead_count"]].copy()
    counts["bassin_de_vie"] = counts["bassin_de_vie"].astype(str)
    counts["heb_accueillants_count"] = pd.to_numeric(
        counts["contact_count"], errors="coerce"
    ).fillna(0)
    counts["prospects_count"] = pd.to_numeric(
        counts["lead_count"], errors="coerce"
    ).fillna(0)
    return counts.groupby("bassin_de_vie", as_index=False)[
        ["heb_accueillants_count", "prospects_count"]
    ].sum()


def _enrich_with_salesforce_bdv(
    target_df: pd.DataFrame,
    df_jaccueille: pd.DataFrame,
    on_col: str,
    index_col: str,
) -> pd.DataFrame:
    """Enrich a dataframe with pre-aggregated Salesforce J'Accueille indicator counts."""
    if target_df.empty:
        return target_df
    df = target_df.reset_index()
    if not df_jaccueille.empty:
        df = df.merge(df_jaccueille, on=on_col, how="left")
    for col in ("heb_accueillants_count", "prospects_count"):
        df[col] = df.get(col, pd.Series(0.0, index=df.index)).fillna(0)
    df["heb_jaccueille_accueillants_score"] = (df["heb_accueillants_count"] > 0).astype(
        float
    )
    df["heb_jaccueille_prospects_score"] = (df["prospects_count"] > 0).astype(float)
    return df.set_index(index_col)


def _enrich_waldec_index(
    waldec_index: pd.DataFrame, associations_data: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Enriches the WALDEC index with association counts."""
    if waldec_index.empty:
        return waldec_index, waldec_index
    enriched = waldec_index.copy()
    if not associations_data.empty and {"id_waldec", "count"}.issubset(
        associations_data.columns
    ):
        enriched["count"] = enriched.index.map(
            associations_data.groupby("id_waldec")["count"].sum()
        )
    else:
        enriched["count"] = 0
    enriched["count"] = (
        pd.to_numeric(enriched["count"], errors="coerce").fillna(0).astype(int)
    )
    enriched = enriched.sort_values(by=["count", "label"], ascending=[False, True])
    return enriched, enriched.head(500)


def _enrich_rome_index(
    rome_index: pd.DataFrame, live_jobs_data: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Enriches the ROME index with job offer counts."""
    if rome_index.empty or live_jobs_data.empty:
        return rome_index, rome_index
    jobs_top = live_jobs_data.groupby("romeCode")["total_postes"].sum().to_frame()
    enriched = rome_index.join(jobs_top, how="left")
    enriched["total_postes"] = enriched["total_postes"].fillna(0)
    enriched = enriched.sort_values(
        by=["total_postes", "label"], ascending=[False, True]
    )
    return enriched, enriched


_ESSENTIAL_ODIS_COLS = {
    "codgeo",
    "polygon",
    "dep_code",
    "reg_code",
    "epci_code",
    "epci_nom",
    "population",
    "bassin_de_vie",
    "centroid_lon",
    "centroid_lat",
    "youth_growth_rate",
    "workclass_growth_rate",
    "count_hopital",
    "count_maternite",
    "count_psy",
    "edu_maternelle_ct",
    "edu_elementaire_ct",
    "edu_college_ct",
    "edu_lycee_ct",
    "log_priv_vacant_plus_2ans",
    "log_total",
    "nb_stops_bus",
    "nb_stops_tram",
    "nb_stops_metro",
    "nb_stops_train",
    "nb_stops_total",
    "maire_extreme_droite",
    "electoral_history",
}


def _select_odis_columns(all_cols: List[str]) -> Set[str]:
    """Identify columns required from the ODIS commune parquet."""
    cols = {
        c
        for c in all_cols
        if c in _ESSENTIAL_ODIS_COLS
        or c.endswith("_scaled")
        or c.startswith("inc_rna_")
        or c == "inc_asso_refug_count"
    }
    scores_path = os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE)
    if os.path.exists(scores_path):
        for m in load_scores_config_as_df(scores_path)["metric"].dropna().unique():
            if m in all_cols:
                cols.add(m)
    return cols


# =============================================================================
# 5. Core Dataset Loading Pipeline & Helpers
# =============================================================================


def _load_and_clean_odis(
    refs: Dict[str, Any],
    df_jaccueille: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, List[str]]:
    """Loads and normalizes the ODIS communes dataset."""
    resolved_odis = resolve_dataset_path(cfg.ODIS_FILE)
    odis_cols = None
    if resolved_odis and os.path.isfile(resolved_odis):
        try:
            odis_cols = list(_select_odis_columns(pq.read_schema(resolved_odis).names))
        except Exception:
            odis_cols = None
    odis = _load_parquet(cfg.ODIS_FILE, columns=odis_cols)

    odis_geo = pd.Series(dtype="object")
    if "polygon" in odis.columns:
        odis_geo = odis[["codgeo", "polygon"]].set_index("codgeo")["polygon"]
        odis.drop(columns=["polygon"], inplace=True)
    if "centroid" in odis.columns:
        odis.drop(columns=["centroid"], inplace=True)
    if "codgeo" in odis.columns:
        odis.set_index("codgeo", inplace=True)
    if "population" in odis.columns:
        odis["population"] = pd.to_numeric(odis["population"], errors="coerce").astype(
            "Int32"
        )

    for col in odis.columns:
        if "scaled" in col or "score" in col:
            odis[col] = odis[col].astype("float32")
        elif col in ("dep_code", "reg_code", "epci_code", "bassin_de_vie"):
            odis[col] = odis[col].astype(str)

    if "dep_code" in odis.columns and hasattr(cfg, "METROPOLITAN_DEPT_CODES_SET"):
        odis = odis[odis["dep_code"].isin(cfg.METROPOLITAN_DEPT_CODES_SET)]
        if not odis_geo.empty:
            odis_geo = odis_geo[odis_geo.index.isin(odis.index)]

    if "libgeo" not in odis.columns:
        odis["libgeo"] = odis.index.map(refs.get("commune_names", {}))
        odis["libgeo"] = odis["libgeo"].fillna(odis.index.to_series())
    if "bassin_de_vie" in odis.columns:
        odis["libelle_bassin_de_vie"] = (
            odis["bassin_de_vie"].astype(str).map(refs.get("bv_names", {}))
        )
        odis["libelle_bassin_de_vie"] = odis["libelle_bassin_de_vie"].fillna(
            odis["bassin_de_vie"]
        )

    depcom_cols = [c for c in ("libgeo", "dep_code") if c in odis.columns]
    depcom_df = odis[depcom_cols].copy()
    coddep_set = (
        sorted(odis["dep_code"].dropna().unique().tolist())
        if "dep_code" in odis.columns
        else refs.get("coddep_set", [])
    )
    odis = _enrich_with_salesforce_bdv(
        odis, df_jaccueille, on_col="bassin_de_vie", index_col="codgeo"
    )
    return odis, odis_geo, depcom_df, coddep_set


def _load_pois_and_indexes() -> tuple[
    pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame
]:
    """Loads POIs and extracts categorized directories."""
    pois_df = _load_parquet(cfg.POIS_FILE)
    ecoles = get_pois_by_category(pois_df, "education")
    sante = get_pois_by_category(pois_df, "sante")
    incl_df = get_pois_by_category(pois_df, "incl_services")

    if not incl_df.empty:
        incl_df = incl_df.rename(
            columns={"type": "categorie", "name": "label", "category": "service"}
        )
        incl_df["thematiques"] = incl_df.get("categorie", "")
        if "service" not in incl_df.columns:
            incl_df["service"] = "Service d'inclusion"
        incl_df["slug"] = incl_df["categorie"]
        incl_idx = (
            incl_df.groupby("codgeo", observed=False)["slug"]
            .apply(set)
            .rename("key")
            .to_frame()
        )
    else:
        incl_idx = pd.DataFrame()
    return pois_df, ecoles, sante, incl_df, incl_idx


def _load_auxiliary_datasets(
    refs: Dict[str, Any],
    load_errors: List[str],
) -> Dict[str, Any]:
    """Loads vertical employment, association, formation and CCAS datasets."""
    live_jobs = _load_parquet(
        getattr(cfg, "LIVE_JOPS_FILE", cfg.LIVE_JOBS_FILE),
        error_list=load_errors,
    )
    assos = _load_parquet(
        cfg.AGG_ASSOCIATIONS_FILE,
        error_list=load_errors,
    )
    formations = _load_parquet(
        cfg.AGG_FORMATIONS_FILE,
        error_list=load_errors,
    )
    if not formations.empty and "formation_code" in formations.columns:
        formations["formation_code"] = (
            formations["formation_code"]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
        )

    rome_idx, rome_top = _enrich_rome_index(
        refs.get("rome_index", pd.DataFrame(columns=["label"])), live_jobs
    )
    waldec_idx, waldec_top = _enrich_waldec_index(
        refs.get("waldec_index", pd.DataFrame(columns=["label"])), assos
    )

    return {
        "live_jobs_data": live_jobs,
        "associations_data": assos,
        "refugee_associations_data": _load_parquet(
            cfg.REFUGEE_ASSOCIATIONS_FILE,
            error_list=load_errors,
        ),
        "formations_data": formations,
        "structures_ccas": _load_parquet(
            cfg.CCAS_FILE,
            error_list=load_errors,
        ),
        "siae_jobs_data": _load_parquet(
            cfg.SIAE_JOBS_FILE,
            error_list=load_errors,
        ),
        "rome_index": rome_idx,
        "rome_top_index": rome_top,
        "waldec_index": waldec_idx,
        "waldec_top_index": waldec_top,
    }


def _load_and_enrich_bv_geo(
    df_jaccueille: pd.DataFrame,
    load_errors: List[str],
) -> pd.DataFrame:
    """Loads Bassins de Vie dataset and enriches it with Salesforce counts."""
    bv_geo = _load_parquet(
        cfg.BV_FILE,
        error_list=load_errors,
    )
    if not bv_geo.empty:
        key_col = getattr(cfg, "BV_CODE_COL", "bassin_de_vie")
        if key_col in bv_geo.columns:
            bv_geo.set_index(key_col, inplace=True)
            if key_col != "bassin_de_vie":
                bv_geo.index.name = key_col
        bv_geo = bv_geo.drop(
            columns=[
                c for c in ("polygon", "centroid", "libgeo") if c in bv_geo.columns
            ],
            errors="ignore",
        )
    return _enrich_with_salesforce_bdv(
        bv_geo, df_jaccueille, on_col="bassin_de_vie", index_col="bassin_de_vie"
    )


def load_referentiels_raw() -> Dict[str, Any]:
    """Build reference indices from referentiels.parquet."""
    refs_df = _load_parquet(cfg.REFERENTIELS_FILE)
    if refs_df.empty:
        raise RuntimeError("Active release has no usable referentials dataset")

    def _dict(key: str) -> Dict[str, str]:
        sub = refs_df[refs_df["key"] == key]
        return sub.set_index("code")["label"].to_dict() if not sub.empty else {}

    def _index_df(key: str) -> pd.DataFrame:
        sub = refs_df[refs_df["key"] == key]
        return (
            sub[["code", "label"]].drop_duplicates("code").set_index("code")
            if not sub.empty
            else pd.DataFrame(columns=["label"])
        )

    commune_names = _dict("communes")
    bv_names = _dict("bassins_de_vie")

    reg_sub = refs_df[refs_df["key"] == "regions"]
    if not reg_sub.empty and hasattr(cfg, "METROPOLITAN_REGION_CODES_SET"):
        reg_sub = reg_sub[
            reg_sub["code"].astype(str).isin(cfg.METROPOLITAN_REGION_CODES_SET)
        ]
    regions_names = (
        reg_sub.set_index("code")["label"].to_dict() if not reg_sub.empty else {}
    )

    dep_sub = refs_df[refs_df["key"] == "departements"]
    if not dep_sub.empty and hasattr(cfg, "METROPOLITAN_DEPT_CODES_SET"):
        dep_sub = dep_sub[
            dep_sub["code"].astype(str).isin(cfg.METROPOLITAN_DEPT_CODES_SET)
        ]
    departements_names = (
        dep_sub.set_index("code")["label"].to_dict() if not dep_sub.empty else {}
    )
    dept_cols = ["label"] + (["reg_code"] if "reg_code" in dep_sub.columns else [])
    dept_details = (
        dep_sub.set_index("code")[dept_cols].to_dict(orient="index")
        if not dep_sub.empty
        else {}
    )

    # depcom_df fallback from communes
    c_ref = refs_df[refs_df["key"] == "communes"]
    depcom_df = pd.DataFrame(columns=["libgeo", "dep_code"])
    coddep_set: List[str] = (
        sorted(dep_sub["code"].astype(str).unique().tolist())
        if not dep_sub.empty
        else []
    )
    if not c_ref.empty:
        codes = c_ref["code"].astype(str)
        deps = codes.apply(lambda c: c[:3] if c.startswith("97") else c[:2])
        mask = (
            deps.isin(cfg.METROPOLITAN_DEPT_CODES_SET)
            if hasattr(cfg, "METROPOLITAN_DEPT_CODES_SET")
            else pd.Series(True, index=c_ref.index)
        )
        depcom_df = pd.DataFrame(
            {"libgeo": c_ref["label"].values[mask], "dep_code": deps.values[mask]},
            index=pd.Index(codes.values[mask], name="codgeo"),
        )
        if not coddep_set:
            coddep_set = sorted(depcom_df["dep_code"].unique().tolist())

    rome_index = (
        _index_df("rome_codes").sort_values(by="label")
        if not refs_df[refs_df["key"] == "rome_codes"].empty
        else pd.DataFrame(columns=["label"])
    )
    scores_cat = load_scores_config_as_df(
        os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE)
    )

    return {
        "referentiels_raw": refs_df,
        "commune_names": commune_names,
        "bv_names": bv_names,
        "regions_names": regions_names,
        "departements_names": departements_names,
        "dept_details": dept_details,
        "depcom_df": depcom_df,
        "coddep_set": coddep_set,
        "scores_cat": scores_cat,
        "rome_index": rome_index,
        "rome_top_index": rome_index,
        "codformations_index": _index_df("formation_codes"),
        "inclusion_services_index": _index_df("inclusion_services"),
        "waldec_index": _index_df("waldec_codes"),
        "waldec_top_index": _index_df("waldec_codes").head(500),
    }


def load_scoring_datasets_raw(
    refs_data: Optional[Dict[str, Any]] = None,
    release_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Load scoring datasets and merge with referentials indices."""
    logger.info("Loading complete dataset bundle from local container filesystem")
    load_errors: List[str] = []
    refs = refs_data if refs_data is not None else load_referentiels_raw()

    sf_bdv = _load_parquet(
        cfg.SALESFORCE_JACCUEILLE_BDV_FILE,
        error_list=load_errors,
    )
    df_jaccueille = get_salesforce_jaccueille_counts(sf_bdv)
    if df_jaccueille.empty:
        logger.error("❌ [J'ACCUEILLE] Salesforce BDV data is missing or incomplete.")
        load_errors.append("J'Accueille Salesforce data missing")

    odis, odis_geo, depcom_df, coddep_set = _load_and_clean_odis(refs, df_jaccueille)
    pois_df, ecoles, sante, incl_df, incl_idx = _load_pois_and_indexes()
    aux = _load_auxiliary_datasets(refs, load_errors)
    bv_geo = _load_and_enrich_bv_geo(df_jaccueille, load_errors)

    result = dict(refs)
    result.update(
        {
            "depcom_df": depcom_df,
            "coddep_set": coddep_set,
            "odis": odis,
            "odis_geo": odis_geo,
            "annuaire_ecoles": ecoles,
            "annuaire_sante": sante,
            "annuaire_inclusion": incl_df,
            "incl_index": incl_idx,
            "bv_geo": bv_geo,
            "bv_data": bv_geo,
            "pois": pois_df,
            **aux,
            "_load_errors": load_errors,
        }
    )
    active_version = release_version or get_active_release_version()
    result["_release_id"] = f"gcs:{active_version}"
    return result


def load_app_data_raw(
    release_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Load the complete unified data bundle from local datasets."""
    refs = load_referentiels_raw()
    return load_scoring_datasets_raw(refs, release_version=release_version)


# =============================================================================
# 6. Streamlit Cache & Entry Points
# =============================================================================


@st.cache_resource(show_spinner=False)
def get_scoring_datasets(release_version: Optional[str] = None) -> Dict[str, Any]:
    """Cached complete scoring bundle for one immutable release version."""
    return load_app_data_raw(release_version)


def get_app_data() -> Dict[str, Any]:
    """Return the complete data bundle for the active release."""
    return get_scoring_datasets(get_active_release_version())


def ensure_data_initialized(*, force_reload: bool = False) -> Dict[str, Any]:
    """Initialize state and return the complete active data bundle."""
    initialize_session_state()
    if not force_reload and st.session_state.get("app_data"):
        app_data = st.session_state["app_data"]
    else:
        app_data = get_app_data()
        st.session_state["app_data"] = app_data

    from services.mcp_server import set_data_context

    set_data_context(app_data)

    if "heavy_data_toast_shown" not in st.session_state:
        if app_data.get("_load_errors"):
            st.toast(
                "Toutes les données n'ont pas pu être chargées, les résultats peuvent en être affectés",
                icon="⚠️",
            )
        st.session_state["heavy_data_toast_shown"] = True
    return app_data
