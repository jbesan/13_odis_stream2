"""Conventions for business state stored in a Streamlit user session."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

import config as cfg


@dataclass(frozen=True)
class IdentityContext:
    username: str
    user: Any
    org: Any
    login_session_id: str | None = None


class AppSession:
    """Small facade for identity, active-search and result-view state.

    Widget keys deliberately remain owned by Streamlit and ``FormState``.  This
    facade owns only transitions that must stay consistent across pages.
    """

    PRESERVED_ON_RESET = {
        "app_data",
        "_data_hash",
        "password_correct",
        "username",
        "user",
        "org",
        "login_session_id",
        "org_defaults_applied",
        "rna_rag_service",
        "rna_rag_status",
    }

    SNAPSHOT_KEYS = {
        "shared_snapshot_editing",
        "shared_snapshot_version",
        "shared_snapshot_data_release",
        "shared_snapshot_created_at",
        "shared_snapshot_has_map",
        "snapshot_current_map_context",
    }

    RESULT_VIEW_DEFAULTS = {
        "highlighted_result": lambda: [False, None],
        "fgs_to_show": set,
        "center": lambda: list(cfg.DEFAULT_MAP_CENTER),
        "initial_center": lambda: list(cfg.DEFAULT_MAP_CENTER),
        "zoom": lambda: 6,
        "active_ia_city_index": lambda: None,
        "active_details_index": lambda: None,
        "active_ccas_index": lambda: None,
    }

    def __init__(self, state: MutableMapping[str, Any]):
        self.state = state

    def identity(self) -> IdentityContext:
        """Return the complete authenticated identity or reject partial state."""
        user = self.state.get("user")
        org = self.state.get("org")
        username = self.state.get("username")
        if not username or user is None:
            raise RuntimeError("Authenticated session has incomplete identity context")
        return IdentityContext(
            username=str(username),
            user=user,
            org=org,
            login_session_id=self.state.get("login_session_id"),
        )

    def ensure_result_view(self) -> None:
        for key, factory in self.RESULT_VIEW_DEFAULTS.items():
            if key not in self.state:
                self.state[key] = factory()

    def begin_search(self, config: Any, data_release: str) -> None:
        """Start a mutable search on the active data release."""
        self.state["pdf_data"] = None
        self.state["pdf_modal_data"] = None
        self.state["active_share_id"] = None
        self.state["immutable_shared_snapshot"] = False
        for key in self.SNAPSHOT_KEYS:
            self.state.pop(key, None)
        self.state["config"] = config
        self.state["active_data_release"] = data_release
        self.state["search_results"] = None
        self.state["processed_gdf"] = None
        self.state["unaggregated_gdf"] = None
        self.state["engine"] = None
        self.state["active_search_hash"] = None

    def complete_search(
        self,
        *,
        engine: Any,
        search_results: Any,
        processed_gdf: Any,
    ) -> None:
        """Atomically publish deterministic scoring output to the UI."""
        self.state["processed_gdf"] = processed_gdf
        self.state["unaggregated_gdf"] = processed_gdf
        self.state["engine"] = engine
        self.state["search_results"] = search_results
        self.state["active_search_hash"] = search_results.search_hash
        self.state["form_completed"] = False

    def restore_snapshot(
        self,
        *,
        config: Any,
        search_results: Any,
        share_id: str,
        processed_gdf: Any,
        current_map_context: Any,
        version: str,
        data_release: str | None,
        created_at: str | None,
        has_map: bool,
        center: list[float],
        zoom: int,
    ) -> None:
        """Publish an immutable shared result without creating live workers."""
        self.state.update(
            {
                "config": config,
                "search_results": search_results,
                "processed_gdf": processed_gdf,
                "unaggregated_gdf": processed_gdf,
                "active_search_hash": search_results.search_hash,
                "active_share_id": share_id,
                "form_completed": False,
                "immutable_shared_snapshot": True,
                "shared_snapshot_version": version,
                "shared_snapshot_data_release": data_release or "unknown",
                "shared_snapshot_created_at": created_at,
                "shared_snapshot_has_map": has_map,
                "snapshot_current_map_context": current_map_context,
                "center": center,
                "initial_center": center,
                "zoom": zoom,
                "last_centered_hash": search_results.search_hash,
                "highlighted_result": [False, None],
            }
        )
        self.state.pop("engine", None)
        self._drop_workers_for(search_results.search_hash)

    def _drop_workers_for(self, search_hash: str) -> None:
        store = self.state.get("odis_bg_store")
        if not isinstance(store, dict):
            return
        store.pop(search_hash, None)
        for key in list(store):
            if str(key).startswith(f"analysis_{search_hash}_"):
                store.pop(key, None)

    def reset_for_home(self) -> int:
        """Clear the draft and active run while retaining identity/resources."""
        removed = 0
        for key in list(self.state):
            if key not in self.PRESERVED_ON_RESET:
                del self.state[key]
                removed += 1
        return removed
