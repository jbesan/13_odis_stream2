import os
import json
import uuid
import logging
import sys
from datetime import datetime
from typing import Tuple, Optional, Any, Dict

if sys.version_info >= (3, 9):
    import zoneinfo
else:
    from backports import zoneinfo as zoneinfo  # type: ignore

import gzip

import streamlit as st
import config as cfg
from core.models import SearchCriterias, SearchResultsData
from google.cloud import storage, bigquery

logger = logging.getLogger("services.share_service")

# GCS & BQ Settings
GCS_BUCKET_NAME = os.getenv("GCS_SHARED_SEARCHES_BUCKET", "odis-stream2-eu")


def _get_gcs_client():
    """Attempts to initialize GCS client if GCP project is set."""
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        return None
    try:
        return storage.Client()
    except Exception as e:
        logger.warning(f"GCS client initialization failed: {e}")
        return None


def _get_bq_client():
    """Attempts to initialize BQ client if GCP project is set."""
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        return None
    try:
        return bigquery.Client()
    except Exception as e:
        logger.warning(f"BQ client initialization failed: {e}")
        return None


import ast


def _safe_json_format(obj: Any) -> Any:
    """Recursively converts sets to lists for JSON serialization."""
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _safe_json_format(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_json_format(i) for i in obj]
    return obj


def _clean_set_strings(obj: Any) -> Any:
    """Recursively converts string representations of sets '{'elem1', ...}' back into lists for Pydantic validation."""
    if isinstance(obj, str) and obj.startswith("{") and obj.endswith("}"):
        try:
            return list(ast.literal_eval(obj))
        except Exception:
            return obj
    if isinstance(obj, dict):
        return {k: _clean_set_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_set_strings(i) for i in obj]
    return obj


def _decompress_payload_bytes(data_bytes: bytes) -> Dict[str, Any]:
    """Decompresses gzipped bytes or parses raw JSON bytes transparently."""
    if data_bytes.startswith(b"\x1f\x8b"):
        data_bytes = gzip.decompress(data_bytes)
    return json.loads(data_bytes.decode("utf-8"))


def save_shared_search(
    config: SearchCriterias,
    search_results: SearchResultsData,
    username: Optional[str] = None,
    org_id: Optional[str] = None,
) -> str:
    """
    Serializes and saves a search results snapshot (config + search_results).
    Compresses payload with Gzip and uploads to GCS with content_encoding='gzip'.
    Logs metadata to BigQuery. Returns the 8-character share_id.
    """
    share_id = uuid.uuid4().hex[:8]

    # Resolve metadata
    if not username:
        try:
            username = st.session_state.get("username", "unknown")
        except Exception:
            username = "unknown"
    if not org_id:
        try:
            org = st.session_state.get("org")
            org_id = org.id if org and hasattr(org, "id") else "unknown"
        except Exception:
            org_id = "unknown"

    try:
        paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
        timestamp_str = datetime.now(paris_tz).isoformat()
    except Exception:
        timestamp_str = datetime.now().isoformat()

    config_dict = (
        config.model_dump(mode="json")
        if hasattr(config, "model_dump")
        else config
    )
    results_dict = (
        search_results.model_dump(mode="json")
        if hasattr(search_results, "model_dump")
        else search_results
    )

    payload: Dict[str, Any] = {
        "share_id": share_id,
        "version": "1.0",
        "created_at": timestamp_str,
        "username": username,
        "org_id": org_id,
        "search_hash": getattr(search_results, "search_hash", ""),
        "config": _safe_json_format(config_dict),
        "search_results": _safe_json_format(results_dict),
    }

    payload_json = json.dumps(payload, default=str, ensure_ascii=False)
    compressed_bytes = gzip.compress(payload_json.encode("utf-8"))

    # Upload to GCS (the only supported persistence backend).
    gcs_client = _get_gcs_client()
    if not gcs_client:
        raise RuntimeError("Le stockage GCS des recherches partagées est indisponible.")

    try:
        bucket = gcs_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"searches/{share_id}.json")
        blob.content_encoding = "gzip"
        blob.upload_from_string(
            compressed_bytes,
            content_type="application/json",
        )
        gcs_uri = f"gs://{GCS_BUCKET_NAME}/searches/{share_id}.json"
        logger.info(f"✅ Saved gzipped shared search snapshot to GCS at {gcs_uri} ({len(compressed_bytes)} bytes)")
    except Exception as e:
        logger.error(f"❌ GCS upload failed for {share_id}: {e}")
        raise RuntimeError("Impossible d'enregistrer la recherche partagée dans GCS.") from e

    # Log telemetry only after the durable snapshot has been stored.
    try:
        from services import telemetry
        telemetry.log_usage_event(
            "search_shared",
            {
                "share_id": share_id,
                "search_hash": getattr(search_results, "search_hash", ""),
                "gcs_uri": gcs_uri,
            },
            username=username,
            org_id=org_id,
        )
    except Exception as e:
        logger.warning(f"⚠️ BQ telemetry log failed for shared search: {e}")

    return share_id


def load_shared_search(
    share_id: str,
) -> Tuple[Optional[SearchCriterias], Optional[SearchResultsData]]:
    """
    Loads and deserializes a saved search snapshot by share_id.
    Loads the snapshot from GCS.
    Supports both gzipped and uncompressed JSON payloads.
    Returns (SearchCriterias, SearchResultsData) or (None, None) if not found.
    """
    if not share_id or not isinstance(share_id, str):
        return None, None

    # Sanitize share_id
    share_id = share_id.strip()

    payload_dict: Optional[Dict[str, Any]] = None

    gcs_client = _get_gcs_client()
    if not gcs_client:
        logger.error("❌ GCS client unavailable while loading shared search %s", share_id)
        return None, None

    try:
        bucket = gcs_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"searches/{share_id}.json")
        if blob.exists():
            data_bytes = blob.download_as_bytes()
            payload_dict = _decompress_payload_bytes(data_bytes)
            logger.info(f"✅ Loaded shared search snapshot from GCS for {share_id}")
    except Exception as e:
        logger.error(f"❌ Failed fetching GCS shared search {share_id}: {e}")

    if not payload_dict or "config" not in payload_dict or "search_results" not in payload_dict:
        logger.warning(f"⚠️ Shared search snapshot not found for ID: {share_id}")
        return None, None

    try:
        cfg_clean = _clean_set_strings(payload_dict["config"])
        res_clean = _clean_set_strings(payload_dict["search_results"])
        config = SearchCriterias.model_validate(cfg_clean)
        search_results = SearchResultsData.model_validate(res_clean)
        return config, search_results
    except Exception as e:
        logger.error(f"❌ Deserialization failed for shared search {share_id}: {e}", exc_info=True)
        return None, None


def restore_shared_search_to_session_state(
    config_obj: SearchCriterias, results_obj: SearchResultsData, share_id: str
):
    """
    Restores a shared search snapshot into Streamlit session state.
    Hydrates processed_gdf, scoring engine, and map boundaries.
    """
    import pandas as pd
    from utils import data_loader
    from core import scoring, maps
    from utils import common as utils

    data_loader.ensure_data_initialized(load_heavy=True)
    app_data = data_loader.get_app_data(load_heavy=True)

    df_all_communes = app_data["odis"]
    df_bv_geo = app_data["bv_geo"]

    engine = scoring.ScoringEngine(
        df_all_communes=df_all_communes,
        df_bv_geo=df_bv_geo,
        scores_cat=app_data["scores_cat"],
        incl_index=app_data["incl_index"],
        associations_data=app_data["associations_data"],
        formations_data=app_data["formations_data"],
        codformations_index=app_data["codformations_index"],
        waldec_index=app_data["waldec_index"],
        global_stats={},
        refugee_associations_data=app_data["refugee_associations_data"],
        live_jobs_data=app_data["live_jobs_data"],
        live_jobs_coverage=app_data.get("live_jobs_coverage", pd.DataFrame()),
        siae_jobs_data=app_data["siae_jobs_data"],
        siae_jobs_coverage=app_data.get("siae_jobs_coverage", pd.DataFrame()),
        annuaire_ecoles=app_data.get("annuaire_ecoles", pd.DataFrame()),
        annuaire_sante=app_data.get("annuaire_sante", pd.DataFrame()),
        annuaire_inclusion=app_data.get("annuaire_inclusion", pd.DataFrame()),
        inclusion_services_index=app_data.get(
            "inclusion_services_index", pd.DataFrame()
        ),
        rome_index=app_data.get("rome_index", pd.DataFrame()),
        bv_data=app_data.get("bv_data"),
    )

    # Hydrate processed_gdf
    _, processed_gdf = engine.run_optimized(config_obj, log_prefix="classic")

    odis_geo = app_data.get("odis_geo")
    if odis_geo is not None and not odis_geo.empty:
        processed_gdf = processed_gdf.join(odis_geo.rename("polygon"), how="left")

    st.session_state["config"] = config_obj
    st.session_state["search_results"] = results_obj
    st.session_state["processed_gdf"] = processed_gdf
    st.session_state["unaggregated_gdf"] = processed_gdf
    st.session_state["engine"] = engine
    st.session_state["active_search_hash"] = results_obj.search_hash
    st.session_state["active_share_id"] = share_id
    st.session_state["form_completed"] = False

    # Populate odis_bg_store so UI components recognize completed post-scoring tasks
    from agents.utils import get_odis_bg_store
    bg_store = get_odis_bg_store()
    h = results_obj.search_hash

    pitches_dict = {
        "global": results_obj.global_pitch or "",
        "pitches": {
            str(c.codgeo): getattr(c, "refiner_pitch", "") or ""
            for c in results_obj.results
        },
    }

    jobs_enrichment_dict = {
        str(c.codgeo): {
            "status": "done",
            "jobs": getattr(c, "siae_jobs", []) or [],
        }
        for c in results_obj.results
    }

    bg_store[h] = {
        "pitches": pitches_dict,
        "odis_brief": getattr(config_obj, "odis_brief", "") or "",
        "status_refiner": "done",
        "enrichment": {
            str(c.codgeo): {
                "siae_jobs": getattr(c, "siae_jobs", []),
                "associations": getattr(c, "associations_details", []),
                "inclusion_services": getattr(c, "inclusion_services_details", []),
            }
            for c in results_obj.results
        },
        "jobs_enrichment": jobs_enrichment_dict,
        "status_enrichment": "done",
        "status_jobs": "done",
    }

    # Pre-populate city analysis status for cities with existing syntheses/analyses
    for c in results_obj.results:
        city_code = str(c.codgeo)
        analysis_key = f"analysis_{h}_{city_code}"
        if getattr(c, "odis_synthesis", None) or getattr(c, "expert_analysis", None):
            bg_store[analysis_key] = {
                "status": "done",
                "result": results_obj,
            }

    # Centering map
    h = results_obj.search_hash
    top_5_results = results_obj.results[:5]
    if top_5_results:
        top_codgeos = [str(c.codgeo) for c in top_5_results]
        top_data = df_all_communes.loc[df_all_communes.index.isin(top_codgeos)]

        if not top_data.empty and "centroid_lon" in top_data.columns:
            avg_x = top_data["centroid_lon"].mean()
            avg_y = top_data["centroid_lat"].mean()
            lon, lat = utils.project_point(
                avg_x, avg_y, from_crs=cfg.PROJECTED_CRS, to_crs="EPSG:4326"
            )
            final_center_y, final_center_x = lat, lon
        else:
            final_center_y, final_center_x = cfg.DEFAULT_MAP_CENTER
    else:
        final_center_y, final_center_x = cfg.DEFAULT_MAP_CENTER

    st.session_state["center"] = [final_center_y, final_center_x]
    st.session_state["zoom"] = maps.get_map_zoom(config_obj.loc_search_area)
    st.session_state["last_centered_hash"] = h
    if (
        config_obj.commune_actuelle
        and config_obj.commune_actuelle.code in df_all_communes.index
    ):
        st.session_state["selected_geo"] = df_all_communes.loc[
            [config_obj.commune_actuelle.code]
        ].copy()
