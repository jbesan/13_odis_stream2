"""Application controller for deterministic search execution."""

from __future__ import annotations

import gc
import logging
import threading
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

import config as cfg
from agents.utils import get_odis_bg_store
from core import maps_deck, scoring
from core.hydration import submit_background_work
from core.models import SearchCriterias, SearchResultsData
from core.postscoring import launch_post_scoring_tasks
from services import telemetry
from services.app_session import AppSession
from services.server_activity import record_server_activity
from ui import ui_telemetry
from utils import common, data_loader



logger = logging.getLogger(__name__)


class SearchController:
    """Execute one search and publish its lifecycle through ``AppSession``."""

    def __init__(self, session: AppSession):
        self.session = session

    def restore_snapshot(self, **snapshot_state: Any) -> None:
        """Publish a snapshot prepared by the persistence service."""
        self.session.restore_snapshot(**snapshot_state)

    def begin_snapshot_edit(self) -> None:
        """Expose snapshot criteria for an explicit fork on current data."""
        if self.session.state.get("immutable_shared_snapshot"):
            self.session.state["shared_snapshot_editing"] = True

    def execute(
        self, config: SearchCriterias, app_data: Mapping[str, Any]
    ) -> SearchResultsData:
        logger.info("--- Running deterministic search ---")
        record_server_activity()
        gc.collect()
        telemetry.reset_interaction_id()
        # The bundle was resolved before this search. Reusing its release ID
        # keeps the saved search coherent even if `current.json` moves while
        # the user is filling in the form.
        self.session.begin_search(
            config,
            app_data.get("_release_id") or data_loader.get_data_mtime(),
        )

        engine = self._build_engine(app_data)
        search_results, processed_gdf = engine.run_optimized(
            config, log_prefix="classic"
        )
        processed_gdf = self._attach_geometries(processed_gdf, app_data)
        search_results.execution_id = uuid4().hex
        self.session.complete_search(
            engine=engine,
            search_results=search_results,
            processed_gdf=processed_gdf,
        )

        search_hash = search_results.background_key
        org = self.session.state.get("org")
        username = self.session.state.get("username", "unknown")
        org_id = getattr(org, "id", "unknown") if org else "unknown"
        is_ai_free = cfg.is_ai_free_mode(org)
        launch_post_scoring_tasks(
            engine,
            config,
            search_results,
            search_hash,
            interaction_id=telemetry.get_interaction_id(),
            username=username,
            org_id=org_id,
            is_ai_free=is_ai_free,
        )

        ui_telemetry.track_ui_event(
            "run_search",
            payload={
                "search_hash": search_hash,
                "total_matches": len(search_results.results),
            },
        )

        self._center_map(config, search_results, app_data)

        from services.share_service import (
            auto_persist_search_snapshot,
            should_schedule_auto_persist,
        )

        if should_schedule_auto_persist():
            share_id = uuid4().hex[:8]
            self.session.state["active_share_id"] = share_id
            self._sync_search_query_param(share_id)

            center_val = self.session.state.get("center")
            bg_entry = get_odis_bg_store().setdefault(search_hash, {})
            bg_entry["snapshot_meta"] = {
                "share_id": share_id,
                "created": False,
                "lock": threading.Lock(),
                "config": config.model_copy(deep=True)
                if hasattr(config, "model_copy")
                else config,
                "search_results": search_results.model_copy(deep=True)
                if hasattr(search_results, "model_copy")
                else search_results,
                "processed_gdf": processed_gdf,
                "selected_geo": self.session.state.get("selected_geo"),
                "pois_df": app_data.get("pois"),
                "structures_ccas_df": app_data.get("structures_ccas"),
                "odis_df": app_data.get("odis"),
                "inclusion_services_index": app_data.get("inclusion_services_index"),
                "data_release": self.session.state.get("active_data_release"),
                "center": list(center_val) if isinstance(center_val, list) else None,
                "zoom": self.session.state.get("zoom"),
                "username": username,
                "org_id": org_id,
            }
            try:
                submit_background_work(
                    lambda h=search_hash: auto_persist_search_snapshot(
                        h, is_initial=True
                    )
                )
            except Exception as exc:
                logger.warning("Initial auto-snapshot scheduling failed: %s", exc)
        else:
            self._sync_search_query_param(None)

        self.session.state["fgs_to_show"] = set()
        self.session.state["highlighted_result"] = [False, None]
        return search_results

    @staticmethod
    def _sync_search_query_param(share_id: str | None) -> None:
        """Keep the browser ``?search=`` parameter aligned with auto-persisted runs."""
        if get_script_run_ctx(suppress_warning=True) is None:
            return
        if share_id:
            st.query_params["search"] = share_id
        else:
            st.query_params.pop("search", None)

    @staticmethod
    def _build_engine(app_data: Mapping[str, Any]) -> scoring.ScoringEngine:
        return scoring.ScoringEngine(
            df_all_communes=app_data["odis"],
            df_bv_geo=app_data["bv_geo"],
            scores_cat=app_data["scores_cat"],
            incl_index=app_data["incl_index"],
            associations_data=app_data["associations_data"],
            formations_data=app_data["formations_data"],
            codformations_index=app_data["codformations_index"],
            waldec_index=app_data["waldec_index"],
            global_stats={},
            refugee_associations_data=app_data["refugee_associations_data"],
            live_jobs_data=app_data["live_jobs_data"],
            siae_jobs_data=app_data["siae_jobs_data"],
            annuaire_ecoles=app_data.get("annuaire_ecoles", pd.DataFrame()),
            annuaire_sante=app_data.get("annuaire_sante", pd.DataFrame()),
            annuaire_inclusion=app_data.get("annuaire_inclusion", pd.DataFrame()),
            inclusion_services_index=app_data.get(
                "inclusion_services_index", pd.DataFrame()
            ),
            rome_index=app_data.get("rome_index", pd.DataFrame()),
            bv_data=app_data.get("bv_data"),
            pois=app_data.get("pois", pd.DataFrame()),
            incl_index_bdv=app_data.get("incl_index_bdv"),
            asso_thematiques_data=app_data.get("asso_thematiques_data"),
            asso_tags_bdv_data=app_data.get("asso_tags_bdv_data"),
            asso_tags_ref=app_data.get("asso_tags_ref"),
        )

    @staticmethod
    def _attach_geometries(
        processed_gdf: pd.DataFrame, app_data: Mapping[str, Any]
    ) -> pd.DataFrame:
        geometries = app_data.get("odis_geo")
        if geometries is None or geometries.empty:
            return processed_gdf
        logger.info(
            "[HYDRATION] Attaching WKB geometries for %s results",
            len(processed_gdf),
        )
        return processed_gdf.join(geometries.rename("polygon"), how="left")

    def _center_map(
        self,
        config: SearchCriterias,
        search_results: SearchResultsData,
        app_data: Mapping[str, Any],
    ) -> None:
        state = self.session.state
        search_hash = search_results.search_hash
        if state.get("last_centered_hash") == search_hash:
            return

        top_results = search_results.results[:5]
        center = list(cfg.DEFAULT_MAP_CENTER)
        if top_results:
            codes = [str(result.codgeo) for result in top_results]
            top_data = app_data["odis"].loc[app_data["odis"].index.isin(codes)]
            if not top_data.empty and "centroid_lon" in top_data.columns:
                lon, lat = common.project_point(
                    top_data["centroid_lon"].mean(),
                    top_data["centroid_lat"].mean(),
                    from_crs=cfg.PROJECTED_CRS,
                    to_crs="EPSG:4326",
                )
                center = [lat, lon]

        state["center"] = center
        state["initial_center"] = center
        state["zoom"] = maps_deck.get_map_zoom(config.loc_search_area)
        state["last_centered_hash"] = search_hash
        if config.commune_actuelle is not None:
            c_code = config.commune_actuelle.code
            if c_code in app_data["odis"].index:
                selected_geo = app_data["odis"].loc[[c_code]].copy()
                if "odis_geo" in app_data and c_code in app_data["odis_geo"].index:
                    selected_geo["polygon"] = app_data["odis_geo"].loc[[c_code]].values
                state["selected_geo"] = selected_geo
