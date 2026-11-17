import os
import json
import uuid
import logging
import base64
import math
import ast
import re
import copy
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Tuple, Optional, Any, Dict, List

import zoneinfo

import gzip
import pandas as pd
from shapely import wkb
from shapely.geometry import Point

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

import config as cfg
from agents.utils import get_odis_bg_store
from core.models import SearchCriterias, SearchResultsData
from core.postscoring import sync_search_results_data
from services.app_session import AppSession
from services.search_controller import SearchController
from services.service_outcomes import OutcomeStatus, ServiceOutcome
from google.cloud import storage
from google.api_core import exceptions as google_exceptions
from pydantic import ValidationError

logger = logging.getLogger("services.share_service")

SHARE_SCHEMA_VERSION = "1.0"
_SHARE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{4,64}$")


def is_valid_share_id(share_id: str) -> bool:
    """Validate that a share identifier is well-formed and safe."""
    return bool(
        isinstance(share_id, str) and _SHARE_ID_PATTERN.fullmatch(share_id.strip())
    )


def _get_shared_searches_bucket_name() -> str:
    bucket_name = os.getenv("GCS_SHARED_SEARCHES_BUCKET")
    if not bucket_name:
        raise RuntimeError("GCS_SHARED_SEARCHES_BUCKET must be configured")
    return bucket_name


@dataclass(frozen=True)
class SharedSearchSnapshot:
    """A durable, immutable shared-search payload.

    `search_results` remains the authority for displayed recommendations.  The
    map context is stored alongside it so restoration never has to rescore with
    whichever data release happens to be active later.
    """

    share_id: str
    created_at: Optional[str]
    data_release: Optional[str]
    config: SearchCriterias
    search_results: SearchResultsData
    map_context: List[Dict[str, Any]]
    current_map_context: List[Dict[str, Any]]
    map_view: Dict[str, Any]
    org_id: Optional[str] = None
    username: Optional[str] = None
    schema_version: str = SHARE_SCHEMA_VERSION
    app_version: Optional[str] = None
    pois_context: List[Dict[str, Any]] = field(default_factory=list)
    ccas_context: List[Dict[str, Any]] = field(default_factory=list)
    gcs_path: Optional[str] = None

    @property
    def has_map_context(self) -> bool:
        return bool(self.map_context)


def _get_gcs_client():
    """Attempts to initialize GCS client if GCP project is set."""
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        return None
    try:
        return storage.Client()
    except Exception:
        logger.error(
            "Shared-search GCS client initialization failed",
            extra={"extra_data": {"error_code": "SHARE-GCS-UNAVAILABLE"}},
            exc_info=True,
        )
        return None


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
        except (ValueError, SyntaxError) as exc:
            logger.debug("String '%s' is not an evaluatable set literal: %s", obj, exc)
            return obj
    if isinstance(obj, dict):
        return {k: _clean_set_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_set_strings(i) for i in obj]
    return obj


def _migrate_payload_dict(payload_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Normalizes legacy or incomplete shared search payloads before Pydantic validation.

    Guarantees structural backward compatibility across snapshot schema versions.
    """
    if not isinstance(payload_dict, dict):
        return {}

    migrated = copy.deepcopy(payload_dict)

    # 1. Normalize schema_version
    schema_version_str = str(
        migrated.get("schema_version") or SHARE_SCHEMA_VERSION
    )
    migrated["schema_version"] = schema_version_str
    migrated.setdefault("app_version", None)

    # 2. Ensure config and search_results dicts exist
    config_data = migrated.get("config")
    if not isinstance(config_data, dict):
        config_data = {}
        migrated["config"] = config_data

    results_data = migrated.get("search_results")
    if not isinstance(results_data, dict):
        results_data = {}
        migrated["search_results"] = results_data

    # 3. Clean string sets from config and search_results
    config_data = _clean_set_strings(config_data)
    results_data = _clean_set_strings(results_data)

    # 4. In search_results, ensure results is a list of valid commune dicts
    raw_results = results_data.get("results")
    if not isinstance(raw_results, list):
        results_data["results"] = []
    else:
        clean_communes = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            if not item.get("codgeo") or not item.get("name"):
                continue
            clean_communes.append(item)
        results_data["results"] = clean_communes

    # 5. In search_results, ensure current_geo and commune_pressentie are None if not dict
    if "current_geo" in results_data and not isinstance(
        results_data["current_geo"], dict
    ):
        results_data["current_geo"] = None
    if "commune_pressentie" in results_data and not isinstance(
        results_data["commune_pressentie"], dict
    ):
        results_data["commune_pressentie"] = None

    # 6. If search_hash is missing, fallback to root or empty string
    if not results_data.get("search_hash"):
        results_data["search_hash"] = str(migrated.get("search_hash") or "")

    # 7. Normalize legacy config criteria (e.g. legacy 'sante' -> 'besoin_sante')
    if "sante" in config_data and "besoin_sante" not in config_data:
        val = config_data["sante"]
        config_data["besoin_sante"] = (
            val if isinstance(val, list) else [val] if val else []
        )

    # If legacy 'target_population' is present without trapezoid bounds
    if (
        config_data.get("target_population") is not None
        and "target_population_a" not in config_data
    ):
        pop = config_data["target_population"]
        if isinstance(pop, (int, float)):
            if pop <= 10000:
                bounds = cfg.CITY_SIZE_MAPPING["🚜 Commune rurale"]
            elif pop <= 30000:
                bounds = cfg.CITY_SIZE_MAPPING["🏡 Bourg"]
            elif pop <= 80000:
                bounds = cfg.CITY_SIZE_MAPPING["🏘️ Petite Ville"]
            else:
                bounds = cfg.CITY_SIZE_MAPPING["🏙️ Ville moyenne"]
            config_data.setdefault("target_population_a", bounds["a"])
            config_data.setdefault("target_population_b", bounds["b"])
            config_data.setdefault("target_population_c", bounds["c"])
            config_data.setdefault("target_population_d", bounds["d"])

    migrated["config"] = config_data
    migrated["search_results"] = results_data
    return migrated


def _decompress_payload_bytes(data_bytes: bytes) -> Dict[str, Any]:
    """Decompresses gzipped bytes or parses raw JSON bytes transparently."""
    if data_bytes.startswith(b"\x1f\x8b"):
        data_bytes = gzip.decompress(data_bytes)
    return json.loads(data_bytes.decode("utf-8"))


def _session_value(key: str, default: Any = None) -> Any:
    """Read optional Streamlit state without making serialization context-bound."""
    try:
        state = st.session_state
        if isinstance(state, dict):
            return state.get(key, default)
        if get_script_run_ctx(suppress_warning=True) is None:
            return default
        return state.get(key, default)
    except (AttributeError, RuntimeError):
        return default
    except Exception as exc:
        logger.debug("Error retrieving session value for %s: %s", key, exc)
        return default


def should_schedule_auto_persist() -> bool:
    """Return True when background auto-snapshot uploads should be scheduled."""
    return bool(os.getenv("GCS_SHARED_SEARCHES_BUCKET")) and (
        cfg.is_auto_persist_searches_enabled()
    )


def get_snapshot_blob_path(share_id: str, env: Optional[str] = None) -> str:
    """Return the environment-compartmentalized GCS object path for a snapshot."""
    resolved_env = (env or cfg.get_deployment_env() or "local").strip().lower() or "local"
    return f"{resolved_env}/searches/{share_id}.json"


def _extract_centroid_lat_lon(row: Any) -> Optional[Tuple[float, float]]:
    """Extract WGS84 (lat, lon) centroid from a map row without shipping polygons."""
    centroid_val = row.get("centroid")
    if centroid_val is not None and not (
        isinstance(centroid_val, float) and math.isnan(centroid_val)
    ):
        try:
            geom = (
                wkb.loads(bytes(centroid_val))
                if isinstance(centroid_val, (bytes, bytearray, memoryview))
                else centroid_val
            )
            if hasattr(geom, "x") and hasattr(geom, "y"):
                lat, lon = float(geom.y), float(geom.x)
                if math.isfinite(lat) and math.isfinite(lon):
                    return lat, lon
        except Exception:
            pass

    poly_val = row.get("polygon")
    if poly_val is None:
        poly_val = row.get("geometry")
    if poly_val is not None and not (
        isinstance(poly_val, float) and math.isnan(poly_val)
    ):
        try:
            geom = (
                wkb.loads(bytes(poly_val))
                if isinstance(poly_val, (bytes, bytearray, memoryview))
                else poly_val
            )
            if geom is not None:
                pt = geom if hasattr(geom, "x") and hasattr(geom, "y") else geom.centroid
                lat, lon = float(pt.y), float(pt.x)
                if math.isfinite(lat) and math.isfinite(lon):
                    return lat, lon
        except Exception:
            pass

    return None


def _serialize_map_context(
    frame: Any,
    *,
    require_weighted_score: bool = True,
    centroid_codgeos: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """Persist lightweight choropleth scores and centroids only for marker communes."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return []

    records: List[Dict[str, Any]] = []
    for index, row in frame.iterrows():
        codgeo = row.get("codgeo", index)
        if codgeo is None:
            continue
        codgeo_str = str(codgeo).strip()
        if not codgeo_str:
            continue

        try:
            weighted_score = float(row.get("weighted_score"))
        except (TypeError, ValueError):
            if require_weighted_score:
                continue
            weighted_score = 0.0
        if require_weighted_score and not math.isfinite(weighted_score):
            continue

        record: Dict[str, Any] = {
            "codgeo": codgeo_str,
            "libgeo": "" if pd.isna(row.get("libgeo")) else str(row.get("libgeo", "")),
            "weighted_score": weighted_score,
        }
        if centroid_codgeos is None or codgeo_str in centroid_codgeos:
            coords = _extract_centroid_lat_lon(row)
            if coords is not None:
                record["centroid_lat"] = coords[0]
                record["centroid_lon"] = coords[1]

        records.append(record)
    return records


def _deserialize_map_context(records: Any) -> pd.DataFrame:
    """Rehydrate saved score map context and marker centroids (backward-compatible with WKB)."""
    if not isinstance(records, list):
        records = []

    rows: List[Dict[str, Any]] = []
    has_legacy_polygon = False
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            weighted_score = float(record.get("weighted_score", 0.0))
            codgeo = str(record["codgeo"])
        except (KeyError, TypeError, ValueError):
            continue

        centroid_pt: Optional[Point] = None
        if "centroid_lat" in record and "centroid_lon" in record:
            try:
                lat = float(record["centroid_lat"])
                lon = float(record["centroid_lon"])
                if math.isfinite(lat) and math.isfinite(lon):
                    centroid_pt = Point(lon, lat)
            except (TypeError, ValueError):
                centroid_pt = None

        row_dict: Dict[str, Any] = {
            "codgeo": codgeo,
            "libgeo": str(record.get("libgeo", "")),
            "weighted_score": weighted_score,
            "centroid": centroid_pt,
        }

        raw_poly_b64 = record.get("polygon_wkb_b64")
        if isinstance(raw_poly_b64, str) and raw_poly_b64:
            try:
                polygon = base64.b64decode(raw_poly_b64, validate=True)
            except (TypeError, ValueError, base64.binascii.Error):
                polygon = None
            if polygon is not None:
                row_dict["polygon"] = polygon
                has_legacy_polygon = True
                if centroid_pt is None:
                    try:
                        geom = wkb.loads(polygon)
                        pt = (
                            geom
                            if hasattr(geom, "x") and hasattr(geom, "y")
                            else geom.centroid
                        )
                        row_dict["centroid"] = Point(float(pt.x), float(pt.y))
                    except Exception:
                        pass

        rows.append(row_dict)

    columns = ["libgeo", "weighted_score", "centroid"]
    if has_legacy_polygon:
        columns.append("polygon")
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rows)
    df["centroid"] = df["centroid"].astype(object)
    return df.set_index("codgeo")


def _target_codgeos(search_results: SearchResultsData) -> set[str]:
    """Return normalized 5-digit codgeos for Top results and optional shortlisted city."""
    codes = {
        str(c.codgeo).strip().zfill(5)
        for c in getattr(search_results, "results", []) or []
        if getattr(c, "codgeo", None)
    }
    pressentie = getattr(search_results, "commune_pressentie", None)
    if pressentie is not None and getattr(pressentie, "codgeo", None):
        codes.add(str(pressentie.codgeo).strip().zfill(5))
    return codes


def _serialize_pois_context(
    pois_df: Any,
    search_results: SearchResultsData,
    inclusion_services_index: Any = None,
) -> List[Dict[str, Any]]:
    """Persist POI markers scoped to Top results and shortlisted city."""
    if (
        pois_df is None
        or not isinstance(pois_df, pd.DataFrame)
        or pois_df.empty
        or "codgeo" not in pois_df.columns
    ):
        return []

    target_codes = _target_codgeos(search_results)
    if not target_codes:
        return []

    pois_work = pois_df.copy()
    pois_work["_codgeo_norm"] = (
        pois_work["codgeo"].astype(str).str.strip().str.zfill(5)
    )
    filtered = pois_work[pois_work["_codgeo_norm"].isin(target_codes)]
    if filtered.empty:
        return []

    has_incl_index = (
        inclusion_services_index is not None
        and isinstance(inclusion_services_index, pd.DataFrame)
        and not inclusion_services_index.empty
    )

    records: List[Dict[str, Any]] = []
    for _, row in filtered.iterrows():
        geom = row.get("geometry")
        lat_val = geom.y if hasattr(geom, "y") else row.get("lat")
        lon_val = geom.x if hasattr(geom, "x") else row.get("lon")
        try:
            lat = float(lat_val)
            lon = float(lon_val)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue

        category = str(row.get("category", ""))
        raw_type_val = row.get("type")
        raw_type = "" if pd.isna(raw_type_val) else str(raw_type_val)
        type_label: Optional[str] = None
        if (
            category in {"incl_services", "inclusion"}
            and raw_type
            and has_incl_index
            and raw_type in inclusion_services_index.index
        ):
            val = inclusion_services_index.loc[raw_type, "label"]
            type_label = str(val if isinstance(val, str) else val.iloc[0])

        record: Dict[str, Any] = {
            "codgeo": str(row["_codgeo_norm"]),
            "category": category,
            "name": "" if pd.isna(row.get("name")) else str(row.get("name")),
            "type": raw_type,
            "lat": lat,
            "lon": lon,
        }
        if type_label:
            record["type_label"] = type_label
        records.append(record)
    return records


def _deserialize_pois_context(records: Any) -> pd.DataFrame:
    """Rehydrate saved POI markers for map_vector without loading full national POIs."""
    columns = ["codgeo", "category", "name", "type", "type_label", "lat", "lon"]
    if not isinstance(records, list):
        return pd.DataFrame(columns=columns)

    rows: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            lat = float(record["lat"])
            lon = float(record["lon"])
            codgeo = str(record["codgeo"]).strip().zfill(5)
            category = str(record["category"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        rows.append(
            {
                "codgeo": codgeo,
                "category": category,
                "name": str(record.get("name", "")),
                "type": str(record.get("type", "")),
                "type_label": record.get("type_label"),
                "lat": lat,
                "lon": lon,
            }
        )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _serialize_ccas_context(
    structures_df: Any,
    odis_df: Any,
    search_results: SearchResultsData,
) -> List[Dict[str, Any]]:
    """Persist CCAS/CIAS contact structures scoped to Top results and shortlisted city."""
    if (
        structures_df is None
        or not isinstance(structures_df, pd.DataFrame)
        or structures_df.empty
        or "codgeo" not in structures_df.columns
    ):
        return []

    raw_codes: set[str] = set()
    for c in getattr(search_results, "results", []) or []:
        if getattr(c, "codgeo", None):
            raw_codes.add(str(c.codgeo).strip())
    pressentie = getattr(search_results, "commune_pressentie", None)
    if pressentie is not None and getattr(pressentie, "codgeo", None):
        raw_codes.add(str(pressentie.codgeo).strip())

    target_codes = set(raw_codes)
    if (
        odis_df is not None
        and isinstance(odis_df, pd.DataFrame)
        and not odis_df.empty
    ):
        for code in raw_codes:
            if code in odis_df.index:
                row = odis_df.loc[code]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                if (
                    bool(row.get("binome"))
                    and "codgeo_binome" in row
                    and pd.notna(row.get("codgeo_binome"))
                ):
                    target_codes.add(str(row.get("codgeo_binome")).strip())

    if not target_codes:
        return []

    filtered = structures_df[
        structures_df["codgeo"].astype(str).str.strip().isin(target_codes)
    ]
    if filtered.empty:
        return []

    records: List[Dict[str, Any]] = []
    for _, row in filtered.iterrows():
        records.append(
            {
                "codgeo": str(row.get("codgeo", "")).strip(),
                "commune": None
                if pd.isna(row.get("commune"))
                else str(row.get("commune")),
                "nom": "" if pd.isna(row.get("nom")) else str(row.get("nom")),
                "adresse": None
                if pd.isna(row.get("adresse"))
                else str(row.get("adresse")),
                "telephone": None
                if pd.isna(row.get("telephone"))
                else str(row.get("telephone")),
                "courriel": None
                if pd.isna(row.get("courriel"))
                else str(row.get("courriel")),
                "site_web": None
                if pd.isna(row.get("site_web"))
                else str(row.get("site_web")),
            }
        )
    return records


def _deserialize_ccas_context(records: Any) -> pd.DataFrame:
    """Rehydrate saved CCAS/CIAS contact rows without loading national tables."""
    columns = [
        "codgeo",
        "commune",
        "nom",
        "adresse",
        "telephone",
        "courriel",
        "site_web",
    ]
    if not isinstance(records, list):
        return pd.DataFrame(columns=columns)

    rows: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or not record.get("codgeo"):
            continue
        rows.append(
            {
                "codgeo": str(record["codgeo"]).strip(),
                "commune": record.get("commune"),
                "nom": str(record.get("nom", "")),
                "adresse": record.get("adresse"),
                "telephone": record.get("telephone"),
                "courriel": record.get("courriel"),
                "site_web": record.get("site_web"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def save_shared_search(
    config: SearchCriterias,
    search_results: SearchResultsData,
    username: Optional[str] = None,
    org_id: Optional[str] = None,
    processed_gdf: Any = None,
    selected_geo: Any = None,
    data_release: Optional[str] = None,
    map_center: Optional[List[float]] = None,
    map_zoom: Optional[int] = None,
    pois_df: Any = None,
    structures_ccas_df: Any = None,
    odis_df: Any = None,
    inclusion_services_index: Any = None,
    share_id: Optional[str] = None,
    allow_overwrite: bool = False,
) -> str:
    """
    Serializes and saves a search results snapshot (config + search_results).
    Compresses payload with Gzip and uploads to GCS with content_encoding='gzip'.
    Logs metadata to BigQuery. Returns the 8-character share_id.
    """
    if share_id and is_valid_share_id(share_id):
        share_id = share_id.strip()
    else:
        share_id = uuid.uuid4().hex[:8]

    # Resolve metadata
    if not username:
        username = _session_value("username", "unknown") or "unknown"
    if not org_id:
        org = _session_value("org")
        org_id = org.id if org and hasattr(org, "id") else "unknown"

    try:
        paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
        timestamp_str = datetime.now(paris_tz).isoformat()
    except Exception:
        timestamp_str = datetime.now().isoformat()

    config_for_save = (
        config.model_copy(deep=True) if hasattr(config, "model_copy") else config
    )
    results_for_save = (
        search_results.model_copy(deep=True)
        if hasattr(search_results, "model_copy")
        else search_results
    )
    bg_key = getattr(results_for_save, "background_key", None)
    if bg_key:
        bg_store = get_odis_bg_store()
        bg_entry = bg_store.get(bg_key) if isinstance(bg_store, dict) else None
        sync_search_results_data(
            results_for_save,
            bg_entry if isinstance(bg_entry, dict) else None,
            config=config_for_save
            if isinstance(config_for_save, SearchCriterias)
            else None,
        )

    config_dict = (
        config_for_save.model_dump(mode="json")
        if hasattr(config_for_save, "model_dump")
        else config_for_save
    )
    results_dict = (
        results_for_save.model_dump(mode="json")
        if hasattr(results_for_save, "model_dump")
        else results_for_save
    )

    if processed_gdf is None:
        processed_gdf = _session_value("processed_gdf")
    if selected_geo is None:
        selected_geo = _session_value("selected_geo")
    if data_release is None:
        data_release = _session_value("active_data_release")
    if map_center is None:
        map_center = _session_value("center")
    if map_zoom is None:
        map_zoom = _session_value("zoom")

    app_data = _session_value("app_data")
    if not isinstance(app_data, dict):
        app_data = {}
    if pois_df is None:
        pois_df = app_data.get("pois")
    if structures_ccas_df is None:
        structures_ccas_df = app_data.get("structures_ccas")
    if odis_df is None:
        odis_df = app_data.get("odis")
    if inclusion_services_index is None:
        inclusion_services_index = app_data.get("inclusion_services_index")

    marker_codgeos = _target_codgeos(results_for_save)
    current_geo = getattr(results_for_save, "current_geo", None)
    if current_geo is not None and getattr(current_geo, "codgeo", None):
        marker_codgeos.add(str(current_geo.codgeo).strip().zfill(5))

    payload: Dict[str, Any] = {
        "share_id": share_id,
        "schema_version": SHARE_SCHEMA_VERSION,
        "app_version": cfg.ODIS_APP_VERSION,
        "created_at": timestamp_str,
        "username": username,
        "org_id": org_id,
        "search_hash": getattr(results_for_save, "search_hash", ""),
        "config": _safe_json_format(config_dict),
        "search_results": _safe_json_format(results_dict),
        "snapshot": {
            "policy": "immutable",
            "data_release": data_release or "unknown",
            "map_context": _serialize_map_context(
                processed_gdf, centroid_codgeos=marker_codgeos
            ),
            "current_map_context": _serialize_map_context(
                selected_geo,
                require_weighted_score=False,
                centroid_codgeos=None,
            ),
            "pois_context": _serialize_pois_context(
                pois_df,
                results_for_save,
                inclusion_services_index=inclusion_services_index,
            ),
            "ccas_context": _serialize_ccas_context(
                structures_ccas_df,
                odis_df,
                results_for_save,
            ),
            "map_view": {
                "center": map_center if isinstance(map_center, list) else None,
                "zoom": map_zoom if isinstance(map_zoom, int) else None,
            },
        },
    }

    payload_json = json.dumps(payload, default=str, ensure_ascii=False)
    compressed_bytes = gzip.compress(payload_json.encode("utf-8"))

    # Upload to GCS (the only supported persistence backend).
    gcs_client = _get_gcs_client()
    if not gcs_client:
        raise RuntimeError("Le stockage GCS des recherches partagées est indisponible.")

    deployment_env = cfg.get_deployment_env()
    blob_path = get_snapshot_blob_path(share_id, env=deployment_env)
    try:
        bucket_name = _get_shared_searches_bucket_name()
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.content_encoding = "gzip"
        blob.custom_time = datetime.now(timezone.utc)
        blob.upload_from_string(
            compressed_bytes,
            content_type="application/json",
            if_generation_match=None if allow_overwrite else 0,
        )
        gcs_uri = f"gs://{bucket_name}/{blob_path}"
        logger.info(
            f"✅ Saved gzipped shared search snapshot to GCS at {gcs_uri} ({len(compressed_bytes)} bytes)"
        )
    except google_exceptions.PreconditionFailed as e:
        logger.error(f"❌ Shared search collision for {share_id}: {e}")
        raise RuntimeError(
            "Un identifiant de recherche partagée identique existe déjà. Veuillez réessayer."
        ) from e
    except Exception as e:
        logger.error(f"❌ GCS upload failed for {share_id}: {e}")
        raise RuntimeError(
            "Impossible d'enregistrer la recherche partagée dans GCS."
        ) from e

    # Log to BigQuery saved_searches registry (append-only: 'create' vs 'update')
    try:
        from services import telemetry

        telemetry.log_saved_search_event(
            event_type="update" if allow_overwrite else "create",
            share_id=share_id,
            status="success",
            env=deployment_env,
            gcs_uri=gcs_uri,
            search_hash=getattr(results_for_save, "search_hash", ""),
            app_version=cfg.ODIS_APP_VERSION,
            schema_version=SHARE_SCHEMA_VERSION,
            data_release=data_release or "unknown",
            username=username,
            org_id=org_id,
        )
    except Exception as e:
        logger.warning(f"⚠️ BQ telemetry log failed for shared search save: {e}")

    return share_id


def auto_persist_search_snapshot(
    h: str, *, is_initial: bool = False
) -> Optional[str]:
    """Persist or refresh an active search snapshot in GCS from background metadata."""
    if not h or not os.getenv("GCS_SHARED_SEARCHES_BUCKET"):
        return None

    store = get_odis_bg_store()
    entry = store.get(h) if isinstance(store, dict) else None
    meta = entry.get("snapshot_meta") if isinstance(entry, dict) else None
    if not isinstance(meta, dict) or not meta.get("share_id"):
        return None

    if not _get_gcs_client():
        return None

    lock = meta.get("lock")
    ctx = lock if lock is not None else nullcontext()
    with ctx:
        already_created = bool(meta.get("created", False))
        allow_overwrite = already_created or not is_initial
        save_kwargs: Dict[str, Any] = {
            "config": meta["config"],
            "search_results": meta["search_results"],
            "username": meta.get("username"),
            "org_id": meta.get("org_id"),
            "processed_gdf": meta.get("processed_gdf"),
            "selected_geo": meta.get("selected_geo"),
            "data_release": meta.get("data_release"),
            "map_center": meta.get("center"),
            "map_zoom": meta.get("zoom"),
            "pois_df": meta.get("pois_df"),
            "structures_ccas_df": meta.get("structures_ccas_df"),
            "odis_df": meta.get("odis_df"),
            "inclusion_services_index": meta.get("inclusion_services_index"),
            "share_id": meta["share_id"],
        }
        try:
            saved_id = save_shared_search(**save_kwargs, allow_overwrite=allow_overwrite)
        except Exception as exc:
            if not allow_overwrite and "existe déjà" in str(exc):
                fresh_share_id = uuid.uuid4().hex[:8]
                meta["share_id"] = fresh_share_id
                save_kwargs["share_id"] = fresh_share_id
                try:
                    saved_id = save_shared_search(**save_kwargs, allow_overwrite=False)
                except Exception as retry_exc:
                    logger.warning(
                        "Auto-persist snapshot failed for %s (%s): %s",
                        h,
                        fresh_share_id,
                        retry_exc,
                    )
                    return None
            else:
                logger.warning(
                    "Auto-persist snapshot failed for %s (%s): %s",
                    h,
                    meta.get("share_id"),
                    exc,
                )
                return None

        meta["created"] = True
        return saved_id


def _share_load_failure(
    status: OutcomeStatus,
    error_code: str,
    share_id: str,
    *,
    exc_info: bool = False,
) -> ServiceOutcome[SharedSearchSnapshot]:
    """Record one classified shared-search failure at the GCS boundary."""
    logger.error(
        "Shared search load failed: status=%s code=%s share_id=%s",
        status.value,
        error_code,
        share_id,
        extra={
            "extra_data": {
                "operation": "load_shared_search",
                "outcome": status.value,
                "error_code": error_code,
                "share_id": share_id,
            }
        },
        exc_info=exc_info,
    )
    return ServiceOutcome(status=status, error_code=error_code)


def _resolve_caller_org_id() -> Optional[str]:
    """Extract authenticated org_id from Streamlit session state if present."""
    try:
        org = st.session_state.get("org")
        if org and hasattr(org, "id") and org.id:
            return str(org.id)
        user = st.session_state.get("user")
        if user and hasattr(user, "org_id") and user.org_id:
            return str(user.org_id)
    except (AttributeError, RuntimeError) as exc:
        logger.debug(
            "st.session_state is unavailable in _resolve_caller_org_id: %s", exc
        )
    except Exception as exc:
        logger.warning("Error resolving caller org_id from session state: %s", exc)
    return None


def load_shared_search_snapshot_outcome(
    share_id: str,
    *,
    caller_org_id: Optional[str] = None,
) -> ServiceOutcome[SharedSearchSnapshot]:
    """Load a shared snapshot without conflating absence, authorization, and system failures."""
    if not share_id or not is_valid_share_id(share_id):
        return ServiceOutcome(
            status=OutcomeStatus.NOT_FOUND,
            error_code="SHARE-NOT-FOUND",
        )

    share_id = share_id.strip()
    active_caller_org = caller_org_id or _resolve_caller_org_id()

    gcs_client = _get_gcs_client()
    if not gcs_client:
        return _share_load_failure(
            OutcomeStatus.UNAVAILABLE, "SHARE-GCS-UNAVAILABLE", share_id
        )

    deployment_env = cfg.get_deployment_env()
    blob_path = get_snapshot_blob_path(share_id, env=deployment_env)
    try:
        bucket_name = _get_shared_searches_bucket_name()
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        exists = blob.exists()
        if not exists:
            legacy_path = f"searches/{share_id}.json"
            legacy_blob = bucket.blob(legacy_path)
            if legacy_blob.exists():
                blob = legacy_blob
                blob_path = legacy_path
                exists = True
    except google_exceptions.NotFound:
        exists = False
    except (google_exceptions.Forbidden, google_exceptions.Unauthorized):
        return _share_load_failure(
            OutcomeStatus.UNAUTHORIZED,
            "SHARE-GCS-UNAUTHORIZED",
            share_id,
            exc_info=True,
        )
    except google_exceptions.GoogleAPICallError:
        return _share_load_failure(
            OutcomeStatus.UNAVAILABLE, "SHARE-GCS-UNAVAILABLE", share_id, exc_info=True
        )
    except Exception:
        return _share_load_failure(
            OutcomeStatus.UNAVAILABLE, "SHARE-GCS-UNAVAILABLE", share_id, exc_info=True
        )

    if not exists:
        logger.info("Shared search snapshot not found: share_id=%s", share_id)
        return ServiceOutcome(
            status=OutcomeStatus.NOT_FOUND,
            error_code="SHARE-NOT-FOUND",
        )

    try:
        data_bytes = blob.download_as_bytes()
    except (google_exceptions.Forbidden, google_exceptions.Unauthorized):
        return _share_load_failure(
            OutcomeStatus.UNAUTHORIZED,
            "SHARE-GCS-UNAUTHORIZED",
            share_id,
            exc_info=True,
        )
    except google_exceptions.GoogleAPICallError:
        return _share_load_failure(
            OutcomeStatus.UNAVAILABLE, "SHARE-GCS-UNAVAILABLE", share_id, exc_info=True
        )
    except Exception:
        return _share_load_failure(
            OutcomeStatus.UNAVAILABLE, "SHARE-GCS-UNAVAILABLE", share_id, exc_info=True
        )

    try:
        payload_dict = _decompress_payload_bytes(data_bytes)
    except (gzip.BadGzipFile, UnicodeDecodeError, json.JSONDecodeError):
        return _share_load_failure(
            OutcomeStatus.INVALID_PAYLOAD,
            "SHARE-PAYLOAD-INVALID",
            share_id,
            exc_info=True,
        )
    except Exception:
        return _share_load_failure(
            OutcomeStatus.INVALID_PAYLOAD,
            "SHARE-PAYLOAD-INVALID",
            share_id,
            exc_info=True,
        )

    if (
        not isinstance(payload_dict, dict)
        or {
            "config",
            "search_results",
        }
        - payload_dict.keys()
    ):
        return _share_load_failure(
            OutcomeStatus.INVALID_PAYLOAD, "SHARE-PAYLOAD-INVALID", share_id
        )

    # Check caller authentication & organization membership
    if not active_caller_org:
        return _share_load_failure(
            OutcomeStatus.UNAUTHORIZED,
            "SHARE-UNAUTHENTICATED",
            share_id,
        )

    snapshot_org_id = payload_dict.get("org_id")
    if not snapshot_org_id or snapshot_org_id != active_caller_org:
        logger.warning(
            "Cross-organization shared search access denied: share_id=%s snapshot_org=%s caller_org=%s",
            share_id,
            snapshot_org_id,
            active_caller_org,
        )
        return _share_load_failure(
            OutcomeStatus.UNAUTHORIZED,
            "SHARE-ORG-MISMATCH",
            share_id,
        )

    try:
        migrated_payload = _migrate_payload_dict(payload_dict)
        config = SearchCriterias.model_validate(migrated_payload["config"])
        search_results = SearchResultsData.model_validate(
            migrated_payload["search_results"]
        )
        snapshot_data = migrated_payload.get("snapshot")
        if not isinstance(snapshot_data, dict):
            snapshot_data = {}
        map_view = snapshot_data.get("map_view")
        snapshot = SharedSearchSnapshot(
            share_id=share_id,
            created_at=migrated_payload.get("created_at"),
            data_release=snapshot_data.get("data_release"),
            config=config,
            search_results=search_results,
            map_context=snapshot_data.get("map_context", []),
            current_map_context=snapshot_data.get("current_map_context", []),
            map_view=map_view if isinstance(map_view, dict) else {},
            org_id=snapshot_org_id,
            username=migrated_payload.get("username"),
            schema_version=str(
                migrated_payload.get("schema_version", SHARE_SCHEMA_VERSION)
            ),
            app_version=migrated_payload.get("app_version"),
            pois_context=snapshot_data.get("pois_context", [])
            if isinstance(snapshot_data.get("pois_context"), list)
            else [],
            ccas_context=snapshot_data.get("ccas_context", [])
            if isinstance(snapshot_data.get("ccas_context"), list)
            else [],
            gcs_path=blob_path,
        )
    except (ValidationError, TypeError, ValueError, KeyError):
        return _share_load_failure(
            OutcomeStatus.INVALID_PAYLOAD,
            "SHARE-PAYLOAD-INVALID",
            share_id,
            exc_info=True,
        )
    except Exception:
        return _share_load_failure(
            OutcomeStatus.INVALID_PAYLOAD,
            "SHARE-PAYLOAD-INVALID",
            share_id,
            exc_info=True,
        )

    # Refresh sliding retention TTL (1 year) in GCS via Custom-Time
    if hasattr(blob, "patch"):
        try:
            blob.custom_time = datetime.now(timezone.utc)
            blob.patch()
        except Exception as exc:
            logger.warning(
                "Failed to refresh custom_time for shared search %s: %s",
                share_id,
                exc,
            )

    logger.info("Loaded shared search snapshot from GCS: share_id=%s", share_id)
    return ServiceOutcome(status=OutcomeStatus.SUCCESS, value=snapshot)


def load_shared_search_snapshot(
    share_id: str, *, caller_org_id: Optional[str] = None
) -> Optional[SharedSearchSnapshot]:
    """Compatibility wrapper returning only a successfully loaded snapshot."""
    return load_shared_search_snapshot_outcome(
        share_id, caller_org_id=caller_org_id
    ).value


def load_shared_search(
    share_id: str, *, caller_org_id: Optional[str] = None
) -> Tuple[Optional[SearchCriterias], Optional[SearchResultsData]]:
    """Compatibility wrapper for callers that need only config and results."""
    snapshot = load_shared_search_snapshot(share_id, caller_org_id=caller_org_id)
    if snapshot is None:
        return None, None
    return snapshot.config, snapshot.search_results


def restore_shared_search_to_session_state(
    config_obj: SearchCriterias,
    results_obj: SearchResultsData,
    share_id: str,
    snapshot: Optional[SharedSearchSnapshot] = None,
) -> None:
    """
    Restores a shared search snapshot into Streamlit session state.
    Restores the saved display state without recomputing against the active data
    release. Older v1 shares remain viewable, but do not get a fabricated map.
    """
    from utils import data_loader
    from core import maps_deck

    if snapshot is None:
        snapshot = SharedSearchSnapshot(
            share_id=share_id,
            created_at=None,
            data_release=None,
            config=config_obj,
            search_results=results_obj,
            map_context=[],
            current_map_context=[],
            map_view={},
            schema_version=SHARE_SCHEMA_VERSION,
        )

    # An immutable snapshot contains all data needed for display. It must not
    # download a special reference bundle before the user explicitly forks it.
    data_loader.initialize_session_state()
    data_loader.apply_search_criteria_to_ui(config_obj)
    processed_gdf = _deserialize_map_context(snapshot.map_context)
    current_map_context = _deserialize_map_context(snapshot.current_map_context)
    pois_df = (
        _deserialize_pois_context(snapshot.pois_context)
        if snapshot.pois_context
        else None
    )
    ccas_df = (
        _deserialize_ccas_context(snapshot.ccas_context)
        if snapshot.ccas_context
        else None
    )

    map_view = snapshot.map_view
    center = map_view.get("center")
    if not isinstance(center, list) or len(center) != 2:
        center = cfg.DEFAULT_MAP_CENTER
    zoom = map_view.get("zoom")
    if not isinstance(zoom, int):
        zoom = maps_deck.get_map_zoom(config_obj.loc_search_area)
    SearchController(AppSession(st.session_state)).restore_snapshot(
        config=config_obj,
        search_results=results_obj,
        share_id=share_id,
        processed_gdf=processed_gdf,
        current_map_context=current_map_context,
        pois_df=pois_df,
        ccas_df=ccas_df,
        version=snapshot.schema_version,
        data_release=snapshot.data_release,
        created_at=snapshot.created_at,
        has_map=snapshot.has_map_context,
        center=center,
        zoom=zoom,
    )


def restore_shared_search_from_query_params() -> bool:
    """Restore a shared snapshot once per query parameter, after authentication."""
    share_id = st.query_params.get("search") if "search" in st.query_params else None
    if not share_id or st.session_state.get("active_share_id") == share_id:
        return False

    caller_org_id = _resolve_caller_org_id()
    outcome = load_shared_search_snapshot_outcome(share_id, caller_org_id=caller_org_id)
    if not outcome.is_success or outcome.value is None:
        if outcome.status == OutcomeStatus.UNAUTHORIZED:
            if outcome.error_code == "SHARE-ORG-MISMATCH":
                user_msg = (
                    "Cette recherche partagée n'est pas accessible pour votre organisation "
                    "(code : SHARE-FORBIDDEN)."
                )
            elif outcome.error_code == "SHARE-UNAUTHENTICATED":
                user_msg = (
                    "Vous devez être connecté pour accéder à cette recherche partagée "
                    "(code : SHARE-UNAUTHORIZED)."
                )
            else:
                user_msg = (
                    "Le service des recherches partagées est indisponible. "
                    "Réessayez plus tard (code : SHARE-GCS-UNAUTHORIZED)."
                )
        elif outcome.status == OutcomeStatus.NOT_FOUND:
            user_msg = (
                f"La recherche partagée '{share_id}' est introuvable ou a expiré."
            )
        elif outcome.status == OutcomeStatus.UNAVAILABLE:
            user_msg = (
                "Le service des recherches partagées est temporairement indisponible. "
                "Réessayez dans quelques instants (code : SHARE-GCS-UNAVAILABLE)."
            )
        elif outcome.status == OutcomeStatus.INVALID_PAYLOAD:
            user_msg = (
                "Cette recherche partagée est invalide ou endommagée. "
                "Contactez le support avec le code SHARE-PAYLOAD-INVALID."
            )
        else:
            user_msg = (
                "Impossible de charger cette recherche partagée. Réessayez plus tard."
            )

        st.session_state["share_error"] = user_msg
        return False

    restore_shared_search_to_session_state(
        outcome.value.config, outcome.value.search_results, share_id, outcome.value
    )

    # Log successful view event to BigQuery saved_searches registry
    try:
        from services import telemetry

        bucket_name = os.getenv("GCS_SHARED_SEARCHES_BUCKET", "unknown")
        deployment_env = cfg.get_deployment_env()
        resolved_path = outcome.value.gcs_path or get_snapshot_blob_path(
            share_id, env=deployment_env
        )
        telemetry.log_saved_search_event(
            event_type="view",
            share_id=share_id,
            status="success",
            env=deployment_env,
            gcs_uri=f"gs://{bucket_name}/{resolved_path}",
            search_hash=getattr(outcome.value.search_results, "search_hash", ""),
            app_version=cfg.ODIS_APP_VERSION,
            schema_version=outcome.value.schema_version,
            data_release=outcome.value.data_release,
            username=outcome.value.username or _resolve_caller_org_id(),
            org_id=outcome.value.org_id or caller_org_id,
        )
    except Exception as e:
        logger.warning(f"⚠️ BQ telemetry log failed for shared search view: {e}")

    return True
