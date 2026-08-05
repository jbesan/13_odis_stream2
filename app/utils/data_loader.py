import streamlit as st
import pandas as pd
import geopandas as gpd
import shapely.wkb as wkb
import json
import hashlib
import os
import yaml
import logging
import re
from typing import Dict, Any, List, Optional
import config as cfg
import copy
import tempfile
import threading
from google.cloud import storage

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_HEAVY_PRELOAD_STATUS: Dict[str, Any] = {
    "in_progress_release": None,
    "completed_release": None,
    "lock": threading.Lock(),
}
_SCORING_DATASET_LOAD_LOCK = threading.RLock()


def _bg_preload_scoring_datasets(data_hash: str) -> None:
    """Best-effort cache warm-up; it never changes session-owned app data."""
    completed = False
    try:
        _get_scoring_datasets_for_release(data_hash)
        completed = True
    except Exception as e:
        logger.error(f"Background preload of heavy datasets failed: {e}")
    finally:
        with _HEAVY_PRELOAD_STATUS["lock"]:
            if _HEAVY_PRELOAD_STATUS["in_progress_release"] == data_hash:
                _HEAVY_PRELOAD_STATUS["in_progress_release"] = None
            if completed:
                _HEAVY_PRELOAD_STATUS["completed_release"] = data_hash


def warm_scoring_datasets(data_hash: str) -> None:
    """Warm one release in the background when it is not already cached/loading."""
    with _HEAVY_PRELOAD_STATUS["lock"]:
        if data_hash in {
            _HEAVY_PRELOAD_STATUS["in_progress_release"],
            _HEAVY_PRELOAD_STATUS["completed_release"],
        }:
            return
        _HEAVY_PRELOAD_STATUS["in_progress_release"] = data_hash

    thread = threading.Thread(
        target=_bg_preload_scoring_datasets,
        args=(data_hash,),
        daemon=True,
    )
    thread.start()


def load_scores_config_as_df(config_path: str) -> pd.DataFrame:
    """Loads the scores configuration YAML as a DataFrame."""
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    data = []
    scores_list = config.get("scores", [])
    for item in scores_list:
        data.append(
            {
                "cat": item.get("category"),
                "score": item.get("id"),
                "label": item.get("display", {}).get("name", item.get("id")),
                "description": item.get("display", {}).get("tooltip", ""),
                "weight": item.get("weight", 1.0),
                "min_bound": item.get("min_bound"),
                "max_bound": item.get("max_bound"),
                "score_affichage": item.get("display", {}).get("strong_point_text", ""),
                "high_value_adjective": item.get("display", {}).get(
                    "high_value_adjective", ""
                ),
                "bdv_factor": item.get("bdv_factor", 0.0),
                "metric": item.get("source_metric"),
                "computation": item.get("computation", "live"),
                "display_factor": item.get("display", {}).get("display_factor", 1.0),
                "unit": item.get("display", {}).get("unit", ""),
                "scaling_type": item.get("scaling_type", "linear"),
                "mu": item.get("mu"),
                "sigma": item.get("sigma"),
                "baseline": item.get("baseline", False),
                "format": item.get("display", {}).get("format", None),
                "missing_strategy": item.get("missing_strategy", "exclude"),
            }
        )
    return pd.DataFrame(data)


def apply_demo_data_if_present(defaults: Dict[str, Any]) -> None:
    """Checks query params for 'demo' and updates defaults with demo scenario."""
    query_params = st.query_params
    if "demo" in query_params:
        demo_id = query_params["demo"]
        if not demo_id or demo_id == "true":
            scenario = cfg.DEMO_SCENARIOS.get("1", {})
        else:
            scenario = cfg.DEMO_SCENARIOS.get(demo_id, {})

        for key, value in scenario.items():
            if key in defaults:
                defaults[key] = value

        st.toast(
            f"Mode Démo activé (Scénario {demo_id if demo_id != 'true' else 'Défaut'})",
            icon="ℹ️",
        )


def apply_logged_in_org_defaults(defaults: Dict[str, Any]) -> None:
    """Updates defaults with organization profile from st.session_state['org'] using a smart merge."""
    org = st.session_state.get("org")
    if org:
        defaults["org_context"] = org.id
        defaults["org_strategic_locations"] = org.default_zones
        defaults["org_strategic_locations_type"] = org.zone_type

        # Smart Merge of profile defaults (F-54 Expansion)
        # - Lists: Union (Add partner-specific options to the global defaults)
        # - Scalars: Override (Partner specific value takes precedence)
        org_defaults = org.defaults
        for key, val in org_defaults.items():
            if key in defaults:
                if isinstance(defaults[key], list) and isinstance(val, list):
                    # Union of lists to avoid duplicates while preserving existing defaults
                    defaults[key] = list(set(defaults[key]) | set(val))
                else:
                    # Direct override for strings, numbers, etc.
                    defaults[key] = val
            else:
                # Add organization-specific defaults not present in global defaults
                defaults[key] = val

        # Toast gating to avoid showing on every page load/re-run
        if st.session_state.get("org_defaults_applied") != org.id:
            # st.toast(f"Profil Organisation activé : **{org.name}**", icon="🏢")
            st.session_state["org_defaults_applied"] = org.id


def session_states_init(defaults: Dict[str, Any]) -> None:
    """Initializes session state with defaults if not already set."""
    if "demo_data" not in st.session_state:
        st.session_state["demo_data"] = defaults

    key_mapping = {
        "commune_actuelle": "ui_commune",
        "departement_actuel": "ui_departement",
    }

    for key, value in defaults.items():
        ui_key = key_mapping.get(key, f"ui_{key}")
        if ui_key not in st.session_state:
            st.session_state[ui_key] = value
        if key not in st.session_state:
            st.session_state[key] = value

    # List inputs
    for key_base, key_in_defaults in [
        ("ui_classe_enfant", "classe_enfants"),
        ("ui_metiers_adult", "codes_metiers"),
        ("ui_formations_adult", "codes_formations"),
    ]:
        if key_in_defaults in defaults and isinstance(defaults[key_in_defaults], list):
            for i, val in enumerate(defaults[key_in_defaults]):
                k = f"{key_base}_{i}"
                if k not in st.session_state:
                    st.session_state[k] = val

    # Initialize individual organization boosts keys (F-54 Expansion)
    # This prevents Streamlit widget warning loops when rendering boost sliders.
    org_boosts = st.session_state.get("ui_org_boosts") or defaults.get("org_boosts")
    if org_boosts and isinstance(org_boosts, dict):
        for criterion_id, default_val in org_boosts.items():
            ui_key = f"ui_org_boost_{criterion_id}"
            slider_key = f"ui_org_boost_slider_{criterion_id}"
            if ui_key not in st.session_state:
                st.session_state[ui_key] = float(default_val)
            if slider_key not in st.session_state:
                st.session_state[slider_key] = int(st.session_state[ui_key])


def apply_search_criteria_to_ui(criteria: Any) -> None:
    """
    Maps a SearchCriterias model (from AI extraction) to the ui_ session states.
    Uses dynamic iteration over model fields to ensure 100% parity with UI variables.
    """
    if not criteria:
        return

    # Convert model to dict - ONLY include fields that were explicitly set by the AI
    # This prevents default values (like weights=0.0) from overwriting profile values.
    crit_dict = (
        criteria.model_dump(exclude_unset=True)
        if hasattr(criteria, "model_dump")
        else criteria.__dict__
    )

    # 1. Generic flattening (extract code/label from CriteriaItems)
    def flatten_val(key, v):
        if isinstance(v, dict) and "code" in v and "label" in v:
            # We want the label for commune input, but codes for everything else
            return v["label"] if key == "commune_actuelle" else v["code"]
        if isinstance(v, list):
            return [flatten_val(key, item) for item in v]
        return v

    flat_crit = {}
    for k, v in crit_dict.items():
        if v is not None:
            flat_crit[k] = flatten_val(k, v)

    # 2. Iterate and set all ui_ dynamically
    for k, v in flat_crit.items():
        st.session_state[f"ui_{k}"] = v

    # Mapping specific values that are used directly in component initialization
    if "commune_actuelle" in flat_crit:
        st.session_state["ui_commune"] = flat_crit["commune_actuelle"]
        # Try to infer department from the original code ONLY if it looks like an INSEE code (5 digits)
        code = criteria.commune_actuelle.code
        if code and len(code) == 5 and code.isdigit():
            st.session_state["ui_departement"] = code[:2]
        elif code and len(code) == 5 and code[:2].isdigit():  # Handle 2A/2B
            st.session_state["ui_departement"] = code[:2]

    # Handle 'sante' field properly
    val_sante = flat_crit.get("besoin_sante") or flat_crit.get("sante")
    if val_sante is None or val_sante == "Aucun":
        st.session_state["ui_besoin_sante"] = []
    elif isinstance(val_sante, list):
        st.session_state["ui_besoin_sante"] = val_sante
    else:
        st.session_state["ui_besoin_sante"] = [val_sante]

    # Synchronize checkboxes for housing and health to loaded states (F-53 Checkboxes Sync)
    current_heb = st.session_state.get("ui_hebergement_cible", [])
    for opt in cfg.HEBERGEMENT_OPTIONS:
        cb_key = f"ui_heb_cb_{opt.replace(' ', '_').lower()}"
        st.session_state[cb_key] = opt in current_heb

    current_sante = st.session_state.get("ui_besoin_sante", [])
    for opt in cfg.SANTE_OPTIONS:
        safe_opt = (
            opt.replace(" ", "_")
            .replace("'", "_")
            .replace("(", "")
            .replace(")", "")
            .lower()
        )
        cb_key = f"ui_sante_cb_{safe_opt}"
        st.session_state[cb_key] = opt in current_sante

    # 3. Handle specific lists mapping that have index suffixes (e.g. metiers_adult_0)
    for key_base, crit_key in [
        ("ui_classe_enfant", "classe_enfants"),
        ("ui_metiers_adult", "codes_metiers"),
        ("ui_formations_adult", "codes_formations"),
    ]:
        if crit_key in flat_crit and isinstance(flat_crit[crit_key], list):
            for i, val in enumerate(flat_crit[crit_key]):
                st.session_state[f"{key_base}_{i}"] = val

    # 4. Handle Mobility Form special case (which deviates from 1-to-1 parsing)
    loc_area = flat_crit.get("loc_search_area")
    loc_code = flat_crit.get("loc_search_code") or []  # Now a list

    if loc_area == "france":
        st.session_state["ui_france_search"] = True
        st.session_state["ui_region_search"] = False
    elif loc_area == "region":
        st.session_state["ui_france_search"] = False
        st.session_state["ui_region_search"] = True
        if loc_code:
            st.session_state["ui_mobility_region"] = (
                loc_code if isinstance(loc_code, list) else [loc_code]
            )
    elif loc_area == "departement" and loc_code:
        st.session_state["ui_france_search"] = False
        st.session_state["ui_region_search"] = False

        # loc_code is a list of department codes
        st.session_state["ui_mobility_dept"] = (
            loc_code if isinstance(loc_code, list) else [loc_code]
        )

        # Infer region from the first department
        first_dept = loc_code[0] if isinstance(loc_code, list) else loc_code
        app_data = st.session_state.get("app_data", {})
        dept_details = app_data.get("dept_details", {})
        reg_code = dept_details.get(first_dept, {}).get("reg_code")
        if reg_code:
            st.session_state["ui_mobility_region"] = [reg_code]

    # 5. Handle notes_qualitatives (UI expects a string, model provides a list of strings)
    if "notes_qualitatives" in flat_crit:
        val = flat_crit["notes_qualitatives"]
        if isinstance(val, list):
            st.session_state["ui_notes_qualitatives"] = "\n".join(val)
        else:
            st.session_state["ui_notes_qualitatives"] = str(val) if val else ""

    # 6. Handle Weight Profile & Weights (F-15 & User Feedback)
    # If a profile is selected, we MUST set the individual ui_poids_... keys
    # because Streamlit widgets don't trigger on_change when set programmatically.
    profile = flat_crit.get("weight_profile")
    if profile in cfg.WEIGHT_PROFILES:
        profile_weights = cfg.WEIGHT_PROFILES[profile]
        for pw_key, pw_val in profile_weights.items():
            # Profiles in config are already 0-100
            st.session_state[f"ui_{pw_key}"] = pw_val

    # Finally, if any explicit weights were extracted (higher priority), apply them
    # Now unified: everything is 0.0-1.0
    has_custom_weights = False
    for k, v in flat_crit.items():
        if k.startswith("poids_"):
            st.session_state[f"ui_{k}"] = float(v)
            has_custom_weights = True

    # If custom weights are present, activate the "Expert Weights" toggle
    if has_custom_weights:
        st.session_state["ui_expert_weights"] = True

    # 6b. Handle Organization Boosts
    if "org_boosts" in flat_crit and isinstance(flat_crit["org_boosts"], dict):
        st.session_state["ui_org_boosts"] = flat_crit["org_boosts"]
        for b_key, b_val in flat_crit["org_boosts"].items():
            st.session_state[f"ui_org_boost_{b_key}"] = float(b_val)
            st.session_state[f"ui_org_boost_slider_{b_key}"] = int(b_val)

    # 7. Town Size Reverse Lookup (Sync Radio Button with Mu/Sigma)
    target_pop = flat_crit.get("target_population")
    target_sigma = flat_crit.get("target_population_sigma")
    if target_pop and target_sigma:
        for label, mapping in cfg.CITY_SIZE_MAPPING.items():
            if mapping["mu"] == target_pop and mapping["sigma"] == target_sigma:
                st.session_state["ui_target_city_size_label"] = label
                break

    # 8. Inclusion Services Sync (Checkboxes + Multiselect)
    # inc_services_selection in flat_crit is a list of CODES
    inc_codes = flat_crit.get("inc_services_selection", [])
    if inc_codes:
        # Standard list for the composite key
        st.session_state["ui_inc_services_selection"] = inc_codes

        # Checkboxes sync
        checkbox_slugs = set(cfg.INC_SERVICES_CHECKBOX_MAPPING.keys())
        for slug in checkbox_slugs:
            cb_key = f"ui_cb_inc_{slug.replace('-', '_')}"
            st.session_state[cb_key] = slug in inc_codes

        # Multiselect sync (Labels)
        inclusion_index = app_data.get("inclusion_services_index", pd.DataFrame())
        multi_labels = []
        if not inclusion_index.empty:
            for c in inc_codes:
                if c in inclusion_index.index and c not in checkbox_slugs:
                    multi_labels.append(inclusion_index.loc[c, "label"])
        st.session_state["ui_inc_services_multi_only"] = multi_labels

    # 9. Inclusion Associations Sync
    asso_codes = flat_crit.get("inc_asso_add_selection", [])
    if asso_codes:
        st.session_state["ui_inc_asso_add_selection_raw"] = asso_codes


def ensure_data_initialized(
    load_heavy: bool = False, *, initialize_rag: bool = True
) -> Dict[str, Any]:
    """Initialize session defaults and return the requested data bundle.

    The foreground caller owns ``st.session_state['app_data']``. A light entry
    point may warm the full cache, but that worker never swaps a session from
    light to heavy data behind the UI's back.
    """
    # Force re-initialization IF a demo parameter is present in query string
    force_refresh = "demo" in st.query_params

    if "demo_data" not in st.session_state or force_refresh:
        defaults = copy.deepcopy(cfg.DEMO_DATA_DEFAULT)

        # Apply demo scenario if present
        apply_demo_data_if_present(defaults)

        # Apply logged in org defaults
        apply_logged_in_org_defaults(defaults)

        st.session_state["demo_data"] = defaults

    # Always ensure session states are initialized if missing
    session_states_init(st.session_state["demo_data"])

    # If we just loaded a demo/org, or on first run, we dispatch the model to the UI
    if force_refresh:
        from core.models import SearchCriterias

        try:
            criteria = SearchCriterias(**st.session_state["demo_data"])
            apply_search_criteria_to_ui(criteria)
        except Exception as e:
            logger.error(f"Failed to apply demo via SearchCriterias: {e}")

    # Load datasets based on Tier requirement
    if load_heavy:
        app_data = get_app_data(load_heavy=True)
        st.session_state["app_data"] = app_data

        if "heavy_data_toast_shown" not in st.session_state:
            load_errors = app_data.get("_load_errors", [])
            if load_errors:
                st.toast(
                    "Toutes les données n'ont pas pu être chargées, les résultats peuvent en être affectés",
                    icon="⚠️",
                )
            st.session_state["heavy_data_toast_shown"] = True

        # RAG is not required to render form controls. Keep it opt-in for
        # foreground flows that actually use analysis/enrichment.
        if initialize_rag and "rna_rag_service" not in st.session_state:
            try:
                from services.rna_rag import RNARagService

                st.session_state["rna_rag_service"] = RNARagService()
                st.session_state["rna_rag_status"] = "connected"
            except Exception as e:
                st.session_state["rna_rag_status"] = "failed"
                st.error(
                    f"🚨 **Erreur de connexion BigQuery/Vertex AI** : {e}\n\n"
                    "Le service de recherche sémantique (RAG) ne sera pas disponible. "
                    "Assurez-vous d'avoir configuré vos identifiants GCP (gcloud auth application-default login)."
                )
    else:
        app_data = get_app_data(load_heavy=False)
        st.session_state["app_data"] = app_data

        # The home/snapshot flow stays light. Warming is only a cache
        # optimization; the form will load the complete bundle itself if the
        # worker has not finished by navigation time.
        mtime = get_data_mtime()
        warm_scoring_datasets(mtime)

    return app_data


def resolve_dataset_path(filename_or_path: str) -> Optional[str]:
    """Resolve a release artifact from the active GCS version into ``/tmp``."""
    filename = os.path.basename(filename_or_path)

    # Resolve and download from the active immutable GCS release. The process
    # cache is versioned, so a changed pointer cannot mix old and new parquet.
    try:
        bucket_name = os.getenv("GCS_DATASETS_BUCKET", "odis-stream2-eu")
        datasets_prefix = os.getenv("GCS_DATASETS_PREFIX", "datasets").strip("/")
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        release_version = _read_gcs_release_version(bucket, datasets_prefix)
        if not release_version:
            raise RuntimeError("Active GCS dataset release pointer is missing")
        remote_prefix = f"{datasets_prefix}/releases/{release_version}"

        tmp_cache_dir = os.path.join(
            tempfile.gettempdir(), "odis_data_cache", release_version
        )
        tmp_cache_path = os.path.join(tmp_cache_dir, filename)
        if os.path.exists(tmp_cache_path):
            logger.debug(
                f"💾 [GCS CACHE] Loaded '{filename}' from local temp cache ({tmp_cache_path})."
            )
            return tmp_cache_path

        blob_path = f"{remote_prefix}/{filename}"
        blob = bucket.blob(blob_path)
        if blob.exists():
            os.makedirs(tmp_cache_dir, exist_ok=True)
            tmp_download_path = f"{tmp_cache_path}.{os.getpid()}.tmp"
            logger.info(
                f"📡 [GCS] Downloading dataset '{filename}' from gs://{bucket_name}/{blob_path}..."
            )
            try:
                blob.download_to_filename(tmp_download_path)
                os.replace(tmp_download_path, tmp_cache_path)
            finally:
                if os.path.exists(tmp_download_path):
                    os.remove(tmp_download_path)
            logger.info(
                f"✅ [GCS] Successfully cached '{filename}' to {tmp_cache_path}"
            )
            return tmp_cache_path
        else:
            logger.warning(
                f"⚠️ [GCS] Blob '{blob_path}' not found in bucket 'gs://{bucket_name}'"
            )
    except Exception as e:
        logger.warning(f"⚠️ [GCS] Failed to fetch dataset '{filename}' from GCS: {e}")

    return None


def _read_gcs_release_version(bucket: Any, datasets_prefix: str) -> Optional[str]:
    """Read the active release pointer without caching it in process memory."""
    pointer_blob = bucket.blob(f"{datasets_prefix}/current.json")
    if not pointer_blob.exists():
        return None

    raw_pointer = pointer_blob.download_as_bytes()
    if isinstance(raw_pointer, bytes):
        raw_pointer = raw_pointer.decode("utf-8")
    pointer = json.loads(raw_pointer)
    release_version = pointer.get("version")
    if not isinstance(release_version, str) or not re.fullmatch(
        r"[A-Za-z0-9._-]+", release_version
    ):
        raise ValueError("Invalid version in GCS dataset release pointer")
    return release_version


def load_active_data_manifest() -> Dict[str, Any]:
    """Load and verify the manifest belonging to the active GCS dataset release.

    The manifest, like every parquet artifact, belongs to the active GCS release.
    """
    try:
        bucket_name = os.getenv("GCS_DATASETS_BUCKET", "odis-stream2-eu")
        datasets_prefix = os.getenv("GCS_DATASETS_PREFIX", "datasets").strip("/")
        bucket = storage.Client().bucket(bucket_name)
        pointer_blob = bucket.blob(f"{datasets_prefix}/current.json")
        if not pointer_blob.exists():
            raise RuntimeError("Active dataset release pointer is missing")
        pointer = json.loads(pointer_blob.download_as_bytes())
        release_version = pointer.get("version")
        manifest_info = pointer.get("manifest")
        if not isinstance(release_version, str) or not re.fullmatch(
            r"[A-Za-z0-9._-]+", release_version
        ):
            raise ValueError("Invalid version in GCS dataset release pointer")
        if not isinstance(manifest_info, dict):
            raise ValueError("Active dataset release pointer has no manifest metadata")

        manifest_name = manifest_info.get("name")
        expected_sha256 = manifest_info.get("sha256")
        if manifest_name != "data_manifest.json" or not isinstance(expected_sha256, str):
            raise ValueError("Active dataset manifest metadata is invalid")
        manifest_blob = bucket.blob(
            f"{datasets_prefix}/releases/{release_version}/{manifest_name}"
        )
        manifest_bytes = manifest_blob.download_as_bytes()
        actual_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError("Active dataset manifest checksum mismatch")
        manifest = json.loads(manifest_bytes)
        if manifest.get("pipeline_run_id") != release_version:
            raise ValueError("Active dataset manifest does not belong to its release")
        manifest["active_release_version"] = release_version
        return manifest
    except Exception as exc:
        raise RuntimeError(f"Unable to load active data manifest: {exc}") from exc


@st.cache_data(ttl=3600)
def fetch_salesforce_jaccueille_bdv() -> pd.DataFrame:
    """Loads the pre-aggregated Salesforce J'accueille BDV table using the unified dataset loader."""
    logger.info("📡 [SALESFORCE] Fetching Salesforce J'accueille BDV dataset...")
    df = load_parquet_dataset("salesforce_jaccueille_bdv.parquet")
    if not df.empty:
        logger.info(f"✅ [SALESFORCE] Loaded {len(df)} rows from salesforce_jaccueille_bdv.parquet")
    else:
        logger.warning("⚠️ [SALESFORCE] salesforce_jaccueille_bdv.parquet is missing or empty")
    return df


def get_salesforce_jaccueille_counts() -> pd.DataFrame:
    """Return the single Salesforce-derived source of J'Accueille score inputs.

    The published BDV dataset deliberately drives both the score inputs and the
    result-page details.  Do not add a runtime BigQuery or local-file fallback:
    it would mix versions and make the data source ambiguous.
    """
    df = fetch_salesforce_jaccueille_bdv()
    required = {"bassin_de_vie", "contact_count", "lead_count"}
    if df.empty or not required.issubset(df.columns):
        missing = sorted(required - set(df.columns)) if not df.empty else sorted(required)
        logger.error(
            "[J'ACCUEILLE] Salesforce BDV dataset is unavailable or incomplete; "
            "missing columns: %s",
            ", ".join(missing),
        )
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
    return (
        counts.groupby("bassin_de_vie", as_index=False)[
            ["heb_accueillants_count", "prospects_count"]
        ]
        .sum()
    )


def _load_parquet(
    path: str, columns: Optional[list] = None, error_list: Optional[list] = None
) -> pd.DataFrame:
    """Internal non-cached loader with error tracking and GCS dataset resolution."""
    resolved_path = resolve_dataset_path(path)
    if not resolved_path or not os.path.exists(resolved_path):
        fname = os.path.basename(path)
        logger.error(f"File not found: {path} (Critical for this feature)")
        if error_list is not None:
            error_list.append(fname)
        return pd.DataFrame()
    if columns:
        return pd.read_parquet(resolved_path, engine="fastparquet", columns=columns)
    return pd.read_parquet(resolved_path, engine="fastparquet")


@st.cache_resource
def load_parquet_dataset(path: str, columns: Optional[list] = None) -> pd.DataFrame:
    """Generic loader for parquet datasets with caching."""
    return _load_parquet(path, columns)


def get_pois_by_category(pois_df: pd.DataFrame, category: str) -> pd.DataFrame:
    """Filters POIs by category and returns a copy."""
    if pois_df.empty:
        return pd.DataFrame()
    return pois_df[pois_df["category"] == category].copy()


def _enrich_waldec_index(
    waldec_index: pd.DataFrame, associations_data: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Enriches the WALDEC index with association counts and returns both the full
    sorted index and the top items list.
    """
    if waldec_index.empty:
        return waldec_index, waldec_index

    enriched_waldec = waldec_index.copy()
    if not associations_data.empty and {"id_waldec", "count"}.issubset(
        associations_data.columns
    ):
        topo_assos = associations_data.groupby("id_waldec")["count"].sum()
        enriched_waldec["count"] = enriched_waldec.index.map(topo_assos)
    else:
        enriched_waldec["count"] = 0
    enriched_waldec["count"] = (
        pd.to_numeric(enriched_waldec["count"], errors="coerce")
        .fillna(0)
        .astype(int)
    )

    enriched_waldec = enriched_waldec.sort_values(
        by=["count", "label"], ascending=[False, True]
    )

    waldec_top_index = enriched_waldec.head(500)

    return enriched_waldec, waldec_top_index


def _enrich_rome_index(
    rome_index: pd.DataFrame, live_jobs_data: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Enriches the ROME index with job offer counts and returns both the full
    sorted index and the top items list.
    """
    if rome_index.empty or live_jobs_data.empty:
        return rome_index, rome_index

    jobs_top = live_jobs_data.groupby("romeCode")["total_postes"].sum().to_frame()

    enriched_rome = rome_index.copy()
    enriched_rome = enriched_rome.join(jobs_top, how="left")
    enriched_rome["total_postes"] = enriched_rome["total_postes"].fillna(0)

    enriched_rome = enriched_rome.sort_values(
        by=["total_postes", "label"], ascending=[False, True]
    )

    return enriched_rome, enriched_rome


def load_referentiels_raw() -> Dict[str, Any]:
    """
    Load the lightweight reference indices from the active GCS release.

    Used by the home/snapshot flow; form controls use the complete bundle.
    """
    refs_df = _load_parquet(cfg.REFERENTIELS_FILE)
    if refs_df.empty:
        raise RuntimeError("Active GCS release has no usable referentials dataset")

    commune_names = {}
    bv_names = {}
    regions_names = {}
    departements_names = {}
    dept_details = {}

    if not refs_df.empty:
        c_ref = refs_df[refs_df["key"] == "communes"]
        if not c_ref.empty:
            commune_names = c_ref.set_index("code")["label"].to_dict()

        bv_ref = refs_df[refs_df["key"] == "bassins_de_vie"]
        if not bv_ref.empty:
            bv_names = bv_ref.set_index("code")["label"].to_dict()

        reg_ref = refs_df[refs_df["key"] == "regions"]
        if not reg_ref.empty:
            regions_names = reg_ref.set_index("code")["label"].to_dict()

        dep_ref = refs_df[refs_df["key"] == "departements"]
        if not dep_ref.empty:
            departements_names = dep_ref.set_index("code")["label"].to_dict()
            cols_to_dict = ["label"]
            if "reg_code" in dep_ref.columns:
                cols_to_dict.append("reg_code")
            dept_details = dep_ref.set_index("code")[cols_to_dict].to_dict(
                orient="index"
            )

    # Build lightweight depcom_df and coddep_set from referentiels
    depcom_df = pd.DataFrame(columns=["libgeo", "dep_code"])
    coddep_set: List[str] = []
    if not refs_df.empty:
        c_ref = refs_df[refs_df["key"] == "communes"]
        if not c_ref.empty:
            codes = c_ref["code"].astype(str)
            deps = codes.apply(lambda c: c[:3] if c.startswith("97") else c[:2])
            depcom_df = pd.DataFrame(
                {"libgeo": c_ref["label"].values, "dep_code": deps.values},
                index=pd.Index(codes.values, name="codgeo"),
            )

        dep_ref = refs_df[refs_df["key"] == "departements"]
        if not dep_ref.empty:
            coddep_set = sorted(dep_ref["code"].astype(str).unique().tolist())
        elif not depcom_df.empty:
            coddep_set = sorted(depcom_df["dep_code"].unique().tolist())

    rome_index = pd.DataFrame(columns=["label"])
    codformations_index = pd.DataFrame(columns=["label"])
    inclusion_services_index = pd.DataFrame(columns=["label"])
    waldec_index = pd.DataFrame(columns=["label"])

    if not refs_df.empty:
        rome_ref_df = refs_df[refs_df["key"] == "rome_codes"]
        if not rome_ref_df.empty:
            rome_index = (
                rome_ref_df[["code", "label"]]
                .drop_duplicates(subset=["code"])
                .set_index("code")
            )
            rome_index = rome_index.sort_values(by="label")

        form_ref_df = refs_df[refs_df["key"] == "formation_codes"]
        if not form_ref_df.empty:
            codformations_index = form_ref_df[["code", "label"]].set_index("code")

        incl_ref_df = refs_df[refs_df["key"] == "inclusion_services"]
        if not incl_ref_df.empty:
            inclusion_services_index = incl_ref_df[["code", "label"]].set_index("code")

        waldec_ref_df = refs_df[refs_df["key"] == "waldec_codes"]
        if not waldec_ref_df.empty:
            waldec_index = waldec_ref_df[["code", "label"]].set_index("code")

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
        "codformations_index": codformations_index,
        "inclusion_services_index": inclusion_services_index,
        "waldec_index": waldec_index,
        "waldec_top_index": waldec_index.head(500),
        # Empty Tier 2 placeholders
        "odis": pd.DataFrame(),
        "odis_geo": pd.Series(dtype="object"),
        "annuaire_ecoles": pd.DataFrame(),
        "annuaire_sante": pd.DataFrame(),
        "annuaire_inclusion": pd.DataFrame(),
        "incl_index": pd.DataFrame(),
        "associations_data": pd.DataFrame(),
        "formations_data": pd.DataFrame(),
        "bv_geo": pd.DataFrame(),
        "bv_data": pd.DataFrame(),
        "live_jobs_data": pd.DataFrame(),
        "live_jobs_coverage": pd.DataFrame(),
        "structures_ccas": pd.DataFrame(),
        "pois": pd.DataFrame(),
        "refugee_associations_data": pd.DataFrame(),
        "siae_jobs_data": pd.DataFrame(),
        "siae_jobs_coverage": pd.DataFrame(),
        "_load_errors": [],
    }


def load_scoring_datasets_raw(refs_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Tier 2: Heavy dataset loading (ODIS communes, WKB geometries, POIs, vertical files, BQ).
    """
    if refs_data is None:
        refs_data = load_referentiels_raw()

    res = copy.copy(refs_data)
    logger.info("Loading heavy scoring datasets from the active GCS release")

    # 1. Load Main ODIS Communes Data
    odis_path = cfg.ODIS_FILE

    try:
        temp_df = _load_parquet(odis_path)
        all_cols = temp_df.columns.tolist()
        del temp_df

        essential_cols = {
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

        columns_to_load = {
            c
            for c in all_cols
            if c in essential_cols
            or c.endswith("_scaled")
            or c.startswith("inc_rna_")
            or c == "inc_asso_refug_count"
        }

        try:
            scores_path = os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE)
            if os.path.exists(scores_path):
                sc_df = load_scores_config_as_df(scores_path)
                raw_metrics = sc_df["metric"].dropna().unique().tolist()
                for m in raw_metrics:
                    if m in all_cols:
                        columns_to_load.add(m)
        except Exception as e:
            logger.warning(f"Could not load raw metrics from config: {e}")

        odis = _load_parquet(odis_path, columns=list(columns_to_load))

        # Geometry processing (JIT DEHYDRATION)
        odis_geo = pd.Series(dtype="object")
        if "polygon" in odis.columns:
            logger.info("Dehydrating geometries to odis_geo (Lazy Load pattern)...")
            odis_geo = odis[["codgeo", "polygon"]].set_index("codgeo")["polygon"]

            odis.drop(columns=["polygon"], inplace=True)
            if "centroid" in odis.columns:
                odis.drop(columns=["centroid"], inplace=True)

        odis.set_index("codgeo", inplace=True)

        if "population" in odis.columns:
            # A partial candidate or historical release can legitimately have
            # missing population. Keep it nullable instead of failing before
            # the scoring layer can apply its missing-data policy.
            odis["population"] = pd.to_numeric(
                odis["population"], errors="coerce"
            ).astype("Int32")

        float_cols = [c for c in columns_to_load if "scaled" in c or "score" in c]
        for col in float_cols:
            if col in odis.columns:
                odis[col] = odis[col].astype("float32")

        for col in ["dep_code", "reg_code", "epci_code", "bassin_de_vie"]:
            if col in odis.columns:
                odis[col] = odis[col].astype(str)

    except Exception as e:
        logger.error(f"Failed to load ODIS data: {e}")
        raise e

    # 2. Load POIs
    pois_path = cfg.POIS_FILE
    pois_df = _load_parquet(pois_path)
    if not pois_df.empty and "lat" in pois_df.columns and "lon" in pois_df.columns:
        pois_df["geometry"] = gpd.points_from_xy(pois_df.lon, pois_df.lat)
        pois_df = gpd.GeoDataFrame(pois_df, geometry="geometry", crs="EPSG:4326")

    annuaire_ecoles = get_pois_by_category(pois_df, "education")
    annuaire_sante = get_pois_by_category(pois_df, "sante")
    annuaire_inclusion = get_pois_by_category(pois_df, "incl_services")

    if not annuaire_inclusion.empty:
        annuaire_inclusion = annuaire_inclusion.rename(
            columns={"type": "categorie", "name": "label", "category": "service"}
        )
        annuaire_inclusion["thematiques"] = annuaire_inclusion.get("categorie", "")
        if "service" not in annuaire_inclusion.columns:
            annuaire_inclusion["service"] = "Service d'inclusion"

    commune_names = res.get("commune_names", {})
    bv_names = res.get("bv_names", {})

    if "libgeo" not in odis.columns:
        odis["libgeo"] = odis.index.map(commune_names)
        odis["libgeo"] = odis["libgeo"].fillna(odis.index.to_series())

    if "bassin_de_vie" in odis.columns:
        odis["libelle_bassin_de_vie"] = odis["bassin_de_vie"].astype(str).map(bv_names)
        odis["libelle_bassin_de_vie"] = odis["libelle_bassin_de_vie"].fillna(
            odis["bassin_de_vie"]
        )

    incl_index = pd.DataFrame()
    if not annuaire_inclusion.empty:
        annuaire_inclusion["slug"] = annuaire_inclusion["categorie"]
        incl_index = (
            annuaire_inclusion.groupby("codgeo", observed=False)["slug"]
            .apply(set)
            .rename("key")
            .to_frame()
        )

    # Subsetting of odis columns for depcom_df
    depcom_cols = [c for c in ["libgeo", "dep_code"] if c in odis.columns]
    depcom_df = odis[depcom_cols].copy()
    coddep_set = (
        sorted(odis["dep_code"].dropna().unique().tolist())
        if "dep_code" in odis.columns
        else res.get("coddep_set", [])
    )

    # 4. Vertical Data
    load_errors: List[str] = []

    live_jobs_data = _load_parquet(
        cfg.LIVE_JOBS_FILE, error_list=load_errors
    )
    live_jobs_coverage = _load_parquet(
        cfg.LIVE_JOBS_COVERAGE_FILE, error_list=load_errors
    )
    associations_data = _load_parquet(
        cfg.AGG_ASSOCIATIONS_FILE, error_list=load_errors
    )
    refugee_associations_data = _load_parquet(
        cfg.REFUGEE_ASSOCIATIONS_FILE, error_list=load_errors
    )
    formations_data = _load_parquet(
        cfg.AGG_FORMATIONS_FILE, error_list=load_errors
    )

    if not formations_data.empty and "formation_code" in formations_data.columns:
        formations_data["formation_code"] = (
            formations_data["formation_code"]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
        )

    structures_ccas = _load_parquet(
        cfg.CCAS_FILE, error_list=load_errors
    )

    siae_jobs_data = _load_parquet(
        cfg.SIAE_JOBS_FILE, error_list=load_errors
    )
    siae_jobs_coverage = _load_parquet(
        cfg.SIAE_JOBS_COVERAGE_FILE, error_list=load_errors
    )

    # --- Enrichment: Index Sorting & Truncation ---
    rome_index = res.get("rome_index", pd.DataFrame())
    waldec_index = res.get("waldec_index", pd.DataFrame())
    rome_index, rome_top_index = _enrich_rome_index(rome_index, live_jobs_data)
    waldec_index, waldec_top_index = _enrich_waldec_index(
        waldec_index, associations_data
    )

    # 5. Bassins de Vie Geo
    bv_path = cfg.BV_FILE
    bv_geo = _load_parquet(bv_path, error_list=load_errors)
    if not bv_geo.empty:
        if "polygon" in bv_geo.columns:
            if isinstance(bv_geo["polygon"].iloc[0], bytes):
                bv_geo["polygon"] = bv_geo["polygon"].apply(wkb.loads)
            bv_geo = gpd.GeoDataFrame(bv_geo, geometry="polygon", crs=cfg.PROJECTED_CRS)
            if "centroid" not in bv_geo.columns:
                bv_geo["centroid"] = bv_geo.geometry.centroid

        key_col = (
            cfg.BV_CODE_COL if cfg.BV_CODE_COL in bv_geo.columns else "bassin_de_vie"
        )
        if key_col in bv_geo.columns:
            bv_geo.set_index(key_col, inplace=True)
            if cfg.BV_CODE_COL != "bassin_de_vie":
                bv_geo.index.name = cfg.BV_CODE_COL

        cols_to_drop = ["polygon", "centroid", "libgeo"]
        bv_geo = bv_geo.drop(
            columns=[c for c in cols_to_drop if c in bv_geo.columns], errors="ignore"
        )

    # --- 5b. Enrich with the published Salesforce J'Accueille dataset ---
    df_jaccueille = get_salesforce_jaccueille_counts()

    if df_jaccueille.empty:
        logger.error("❌ [J'ACCUEILLE] Salesforce BDV data is missing or incomplete.")
        load_errors.append("J'Accueille Salesforce data missing")

    if not bv_geo.empty:
        bv_geo = bv_geo.reset_index()
        if not df_jaccueille.empty:
            bv_geo = bv_geo.merge(df_jaccueille, on="bassin_de_vie", how="left")
        bv_geo["heb_accueillants_count"] = bv_geo.get("heb_accueillants_count", pd.Series(0.0, index=bv_geo.index)).fillna(0)
        bv_geo["prospects_count"] = bv_geo.get("prospects_count", pd.Series(0.0, index=bv_geo.index)).fillna(0)
        bv_geo["heb_jaccueille_accueillants_score"] = (
            bv_geo["heb_accueillants_count"] > 0
        ).astype(float)
        bv_geo["heb_jaccueille_prospects_score"] = (
            bv_geo["prospects_count"] > 0
        ).astype(float)
        bv_geo = bv_geo.set_index("bassin_de_vie")

    if not odis.empty:
        odis = odis.reset_index()
        if not df_jaccueille.empty:
            odis = odis.merge(df_jaccueille, on="bassin_de_vie", how="left")
        odis["heb_accueillants_count"] = odis.get("heb_accueillants_count", pd.Series(0.0, index=odis.index)).fillna(0)
        odis["prospects_count"] = odis.get("prospects_count", pd.Series(0.0, index=odis.index)).fillna(0)
        odis["heb_jaccueille_accueillants_score"] = (odis["heb_accueillants_count"] > 0).astype(
            float
        )
        odis["heb_jaccueille_prospects_score"] = (odis["prospects_count"] > 0).astype(
            float
        )
        odis = odis.set_index("codgeo")

    res.update(
        {
            "odis": odis,
            "odis_geo": odis_geo,
            "annuaire_ecoles": annuaire_ecoles,
            "annuaire_sante": annuaire_sante,
            "annuaire_inclusion": annuaire_inclusion,
            "incl_index": incl_index,
            "associations_data": associations_data,
            "formations_data": formations_data,
            "depcom_df": depcom_df,
            "coddep_set": coddep_set,
            "bv_geo": bv_geo,
            "bv_data": bv_geo,
            "live_jobs_data": live_jobs_data,
            "live_jobs_coverage": live_jobs_coverage,
            "structures_ccas": structures_ccas,
            "pois": pois_df,
            "refugee_associations_data": refugee_associations_data,
            "waldec_index": waldec_index,
            "waldec_top_index": waldec_top_index,
            "rome_index": rome_index,
            "rome_top_index": rome_top_index,
            "siae_jobs_data": siae_jobs_data,
            "siae_jobs_coverage": siae_jobs_coverage,
            "_load_errors": load_errors,
        }
    )
    return res


def load_all_data_raw() -> Dict[str, Any]:
    """
    Initializes and loads all necessary datasets for the application.
    (Non-cached version for MCP usage or testing)
    """
    print("################### DATA RELOADED ###################")
    refs = load_referentiels_raw()
    return load_scoring_datasets_raw(refs)


def get_data_mtime() -> str:
    """Return the active immutable GCS release ID used as the cache key."""
    try:
        bucket_name = os.getenv("GCS_DATASETS_BUCKET", "odis-stream2-eu")
        datasets_prefix = os.getenv("GCS_DATASETS_PREFIX", "datasets").strip("/")
        release_version = _read_gcs_release_version(
            storage.Client().bucket(bucket_name), datasets_prefix
        )
    except Exception as e:
        raise RuntimeError(f"Unable to read active GCS dataset release: {e}") from e

    if not release_version:
        raise RuntimeError("Active GCS dataset release pointer is missing")
    return f"gcs:{release_version}"


@st.cache_resource
def get_referentiels_data(data_hash: str) -> Dict[str, Any]:
    """Cached wrapper for Tier 1 Referentiels."""
    return load_referentiels_raw()


@st.cache_resource
def get_scoring_datasets(data_hash: str) -> Dict[str, Any]:
    """Cached wrapper for Tier 2 Heavy Scoring datasets."""
    refs = get_referentiels_data(data_hash)
    return load_scoring_datasets_raw(refs)


def _get_scoring_datasets_for_release(data_hash: str) -> Dict[str, Any]:
    """Serialize the cold load shared by foreground form entry and warm-up."""
    with _SCORING_DATASET_LOAD_LOCK:
        return get_scoring_datasets(data_hash)


def get_app_data(load_heavy: bool = True) -> Dict[str, Any]:
    """
    Universal entry point to get the shared datasets (cached).
    - load_heavy=False: Returns the lightweight reference bundle.
    - load_heavy=True: Returns the complete scoring bundle.
    """
    mtime = get_data_mtime()
    if not load_heavy:
        return get_referentiels_data(mtime)
    return _get_scoring_datasets_for_release(mtime)
