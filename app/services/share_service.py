import os
import json
import uuid
import logging
import sys
import base64
import math
import ast
from dataclasses import dataclass
from datetime import datetime
from typing import Tuple, Optional, Any, Dict, List

if sys.version_info >= (3, 9):
    import zoneinfo
else:
    from backports import zoneinfo as zoneinfo  # type: ignore

import gzip
import pandas as pd

import streamlit as st
import config as cfg
from core.models import SearchCriterias, SearchResultsData
from services.app_session import AppSession
from services.search_controller import SearchController
from google.cloud import storage, bigquery

logger = logging.getLogger("services.share_service")

# GCS & BQ Settings
GCS_BUCKET_NAME = os.getenv("GCS_SHARED_SEARCHES_BUCKET", "odis-stream2-eu")
SHARE_SNAPSHOT_VERSION = "2.0"


@dataclass(frozen=True)
class SharedSearchSnapshot:
    """A durable, immutable shared-search payload.

    `search_results` remains the authority for displayed recommendations.  The
    map context is stored alongside it so restoration never has to rescore with
    whichever data release happens to be active later.
    """

    share_id: str
    version: str
    created_at: Optional[str]
    data_release: Optional[str]
    config: SearchCriterias
    search_results: SearchResultsData
    map_context: List[Dict[str, Any]]
    current_map_context: List[Dict[str, Any]]
    map_view: Dict[str, Any]

    @property
    def has_map_context(self) -> bool:
        return bool(self.map_context)


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


def _session_value(key: str, default: Any = None) -> Any:
    """Read optional Streamlit state without making serialization context-bound."""
    try:
        return st.session_state.get(key, default)
    except Exception:
        return default


def _encode_wkb(value: Any) -> Optional[str]:
    """Encode a geometry/WKB value in a JSON-safe, deterministic form."""
    if value is None:
        return None
    if hasattr(value, "wkb"):
        value = value.wkb
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, (bytes, bytearray)):
        return None
    return base64.b64encode(bytes(value)).decode("ascii")


def _serialize_map_context(
    frame: Any, *, require_weighted_score: bool = True
) -> List[Dict[str, Any]]:
    """Persist only the immutable fields required to render the score map."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return []

    records: List[Dict[str, Any]] = []
    for index, row in frame.iterrows():
        codgeo = row.get("codgeo", index)
        polygon = _encode_wkb(row.get("polygon"))
        if codgeo is None or polygon is None:
            continue

        try:
            weighted_score = float(row.get("weighted_score"))
        except (TypeError, ValueError):
            if require_weighted_score:
                continue
            weighted_score = 0.0
        if require_weighted_score and not math.isfinite(weighted_score):
            continue

        records.append(
            {
                "codgeo": str(codgeo),
                "libgeo": str(row.get("libgeo", "")),
                "weighted_score": weighted_score,
                "polygon_wkb_b64": polygon,
            }
        )
    return records


def _deserialize_map_context(records: Any) -> pd.DataFrame:
    """Rehydrate saved WKB map context without consulting current datasets."""
    if not isinstance(records, list):
        records = []

    rows: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            polygon = base64.b64decode(record["polygon_wkb_b64"], validate=True)
            weighted_score = float(record["weighted_score"])
            codgeo = str(record["codgeo"])
        except (KeyError, TypeError, ValueError, base64.binascii.Error):
            continue
        rows.append(
            {
                "codgeo": codgeo,
                "libgeo": str(record.get("libgeo", "")),
                "weighted_score": weighted_score,
                "polygon": polygon,
            }
        )

    if not rows:
        return pd.DataFrame(columns=["libgeo", "weighted_score", "polygon"])
    return pd.DataFrame(rows).set_index("codgeo")


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

    payload: Dict[str, Any] = {
        "share_id": share_id,
        "version": SHARE_SNAPSHOT_VERSION,
        "created_at": timestamp_str,
        "username": username,
        "org_id": org_id,
        "search_hash": getattr(search_results, "search_hash", ""),
        "config": _safe_json_format(config_dict),
        "search_results": _safe_json_format(results_dict),
        "snapshot": {
            "policy": "immutable",
            "data_release": data_release or "unknown",
            "map_context": _serialize_map_context(processed_gdf),
            "current_map_context": _serialize_map_context(
                selected_geo, require_weighted_score=False
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
                "data_release": data_release or "unknown",
            },
            username=username,
            org_id=org_id,
        )
    except Exception as e:
        logger.warning(f"⚠️ BQ telemetry log failed for shared search: {e}")

    return share_id


def load_shared_search_snapshot(share_id: str) -> Optional[SharedSearchSnapshot]:
    """
    Loads and deserializes a saved search snapshot by share_id.
    Loads the snapshot from GCS.
    Supports both gzipped and uncompressed JSON payloads.
    Returns a versioned immutable snapshot, or ``None`` when it cannot be
    loaded. The compatibility wrapper below keeps the original tuple API for
    callers that only need models.
    """
    if not share_id or not isinstance(share_id, str):
        return None

    # Sanitize share_id
    share_id = share_id.strip()

    payload_dict: Optional[Dict[str, Any]] = None

    gcs_client = _get_gcs_client()
    if not gcs_client:
        logger.error("❌ GCS client unavailable while loading shared search %s", share_id)
        return None

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
        return None

    try:
        cfg_clean = _clean_set_strings(payload_dict["config"])
        res_clean = _clean_set_strings(payload_dict["search_results"])
        config = SearchCriterias.model_validate(cfg_clean)
        search_results = SearchResultsData.model_validate(res_clean)
        snapshot_data = payload_dict.get("snapshot")
        if not isinstance(snapshot_data, dict):
            snapshot_data = {}
        map_view = snapshot_data.get("map_view")
        return SharedSearchSnapshot(
            share_id=share_id,
            version=str(payload_dict.get("version", "1.0")),
            created_at=payload_dict.get("created_at"),
            data_release=snapshot_data.get("data_release"),
            config=config,
            search_results=search_results,
            map_context=snapshot_data.get("map_context", []),
            current_map_context=snapshot_data.get("current_map_context", []),
            map_view=map_view if isinstance(map_view, dict) else {},
        )
    except Exception as e:
        logger.error(f"❌ Deserialization failed for shared search {share_id}: {e}", exc_info=True)
        return None


def load_shared_search(
    share_id: str,
) -> Tuple[Optional[SearchCriterias], Optional[SearchResultsData]]:
    """Compatibility wrapper for callers that need only config and results."""
    snapshot = load_shared_search_snapshot(share_id)
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
    from core import maps

    if snapshot is None:
        snapshot = SharedSearchSnapshot(
            share_id=share_id,
            version="1.0",
            created_at=None,
            data_release=None,
            config=config_obj,
            search_results=results_obj,
            map_context=[],
            current_map_context=[],
            map_view={},
        )

    # An immutable snapshot contains all data needed for display. It must not
    # download a special reference bundle before the user explicitly forks it.
    data_loader.initialize_session_state()
    data_loader.apply_search_criteria_to_ui(config_obj)
    processed_gdf = _deserialize_map_context(snapshot.map_context)
    current_map_context = _deserialize_map_context(snapshot.current_map_context)

    map_view = snapshot.map_view
    center = map_view.get("center")
    if not isinstance(center, list) or len(center) != 2:
        center = cfg.DEFAULT_MAP_CENTER
    zoom = map_view.get("zoom")
    if not isinstance(zoom, int):
        zoom = maps.get_map_zoom(config_obj.loc_search_area)
    SearchController(AppSession(st.session_state)).restore_snapshot(
        config=config_obj,
        search_results=results_obj,
        share_id=share_id,
        processed_gdf=processed_gdf,
        current_map_context=current_map_context,
        version=snapshot.version,
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

    snapshot = load_shared_search_snapshot(share_id)
    if snapshot is None:
        st.session_state["share_error"] = (
            f"La recherche partagée '{share_id}' est introuvable ou a expiré."
        )
        return False

    restore_shared_search_to_session_state(
        snapshot.config, snapshot.search_results, share_id, snapshot
    )
    return True
