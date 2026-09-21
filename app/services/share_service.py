import os
import json
import uuid
import logging
import base64
import math
import ast
import re
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Tuple, Optional, Any, Dict, List

import zoneinfo

import gzip
import pandas as pd

import streamlit as st
import config as cfg
from core.models import SearchCriterias, SearchResultsData
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
        return st.session_state.get(key, default)
    except (AttributeError, RuntimeError):
        return default
    except Exception as exc:
        logger.debug("Error retrieving session value for %s: %s", key, exc)
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
        config.model_dump(mode="json") if hasattr(config, "model_dump") else config
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
        "schema_version": SHARE_SCHEMA_VERSION,
        "app_version": cfg.ODIS_APP_VERSION,
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
        bucket_name = _get_shared_searches_bucket_name()
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(f"searches/{share_id}.json")
        blob.content_encoding = "gzip"
        blob.custom_time = datetime.now(timezone.utc)
        blob.upload_from_string(
            compressed_bytes,
            content_type="application/json",
            if_generation_match=0,
        )
        gcs_uri = f"gs://{bucket_name}/searches/{share_id}.json"
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

    # Log to BigQuery saved_searches registry
    try:
        from services import telemetry

        telemetry.log_saved_search_event(
            event_type="create",
            share_id=share_id,
            status="success",
            env=cfg.ODIS_DEPLOYMENT_ENV,
            gcs_uri=gcs_uri,
            search_hash=getattr(search_results, "search_hash", ""),
            app_version=cfg.ODIS_APP_VERSION,
            schema_version=SHARE_SCHEMA_VERSION,
            data_release=data_release or "unknown",
            username=username,
            org_id=org_id,
        )
    except Exception as e:
        logger.warning(f"⚠️ BQ telemetry log failed for shared search create: {e}")

    return share_id


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

    try:
        bucket = gcs_client.bucket(_get_shared_searches_bucket_name())
        blob = bucket.blob(f"searches/{share_id}.json")
        exists = blob.exists()
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
        telemetry.log_saved_search_event(
            event_type="view",
            share_id=share_id,
            status="success",
            env=cfg.ODIS_DEPLOYMENT_ENV,
            gcs_uri=f"gs://{bucket_name}/searches/{share_id}.json",
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
