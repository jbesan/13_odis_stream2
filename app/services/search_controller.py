"""Application controller for deterministic search execution."""

from __future__ import annotations

import gc
import logging
from collections.abc import Mapping
from typing import Any

import pandas as pd

import config as cfg
from agents.utils import odis_get_bg_result
from core import maps, scoring
from core.models import SearchCriterias, SearchResultsData
from core.postscoring import launch_post_scoring_tasks
from services import telemetry
from services.app_session import AppSession
from utils import common, data_loader


logger = logging.getLogger(__name__)


class SearchController:
    """Execute one search and publish its lifecycle through ``AppSession``."""

    def __init__(self, session: AppSession):
        self.session = session

    def execute(
        self, config: SearchCriterias, app_data: Mapping[str, Any]
    ) -> SearchResultsData:
        logger.info("--- Running deterministic search ---")
        gc.collect()
        telemetry.reset_interaction_id()
        self.session.begin_search(config, data_loader.get_data_mtime())

        engine = self._build_engine(app_data)
        search_results, processed_gdf = engine.run_optimized(
            config, log_prefix="classic"
        )
        processed_gdf = self._attach_geometries(processed_gdf, app_data)
        self.session.complete_search(
            engine=engine,
            search_results=search_results,
            processed_gdf=processed_gdf,
        )

        search_hash = search_results.search_hash
        if odis_get_bg_result(search_hash) is None:
            launch_post_scoring_tasks(
                engine, config, search_results, search_hash
            )

        self._center_map(config, search_results, app_data)
        self.session.state["fgs_to_show"] = set()
        self.session.state["highlighted_result"] = [False, None]
        return search_results

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
        state["zoom"] = maps.get_map_zoom(config.loc_search_area)
        state["last_centered_hash"] = search_hash
        if config.commune_actuelle is not None:
            state["selected_geo"] = app_data["odis"].loc[
                [config.commune_actuelle.code]
            ].copy()

